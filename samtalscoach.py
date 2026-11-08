"""Viska - lyssnar på samtalet och visar vad du kan svara, i realtid.

Ljud: mikrofon (= Du) + Windows systemljud via WASAPI loopback (= Motpart).
Tal-till-text: OpenAI Realtime, modell gpt-live-transcribe (en anslutning per ljudkälla).
Svarsförslag: OpenAI Responses API, modell gpt-6-luna, reasoning effort "none", strömmat.
API-nyckel: miljövariabeln OPENAI_API_KEY, annars .env i projektmappen, annars %APPDATA%\\Viska\\config.json.
"""
from __future__ import annotations

import base64
import json
import os
import queue
import re
import sys
import threading
import time
import numpy as np
import webview
import pyaudiowpatch as pa
from openai import OpenAI
from websockets.sync.client import connect

APP_NAME = "Viska"
TRANSCRIBE_MODEL = "gpt-live-transcribe"
COACH_MODEL = "gpt-6-luna"
RT_URL = "wss://api.openai.com/v1/realtime?intent=transcription"
RATE = 24000                 # Realtime vill ha 24 kHz PCM16 mono
SILENCE_HANG = 0.55          # sekunder tystnad = turen är slut
SETUP_W, SETUP_H = 900, 760
DELAY = "high"             # noggrannare tal-till-text (mer ljudkontext), lite långsammare
EARLY = False             # vänta på färdig text i stället för halvfärdig
PREROLL = 0.3                # sekunder ljud före talstart som skickas med

CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")


# ---------------------------------------------------------------- config
def log(*a):
    r"""Enkel loggfil i %APPDATA%\Viska\viska.log (för felsökning av ljud)."""
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        with open(os.path.join(CONFIG_DIR, "viska.log"), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a) + "\n")
    except Exception:
        pass


def safe_print(*a):
    try:
        print(*a, flush=True)
    except Exception:
        pass


def load_config() -> dict:
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def env_file_key() -> str:
    """Läser OPENAI_API_KEY från en .env-fil bredvid .exe:n eller i projektmappen."""
    here = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
    for d in (here, os.path.dirname(here)):
        try:
            with open(os.path.join(d, ".env"), encoding="utf-8-sig") as f:
                for line in f:
                    k, _, v = line.strip().partition("=")
                    if k.strip() == "OPENAI_API_KEY" and v.strip().strip('"\''):
                        return v.strip().strip('"\'')
        except OSError:
            pass
    return ""


def api_key(cfg: dict) -> str:
    return os.environ.get("OPENAI_API_KEY") or env_file_key() or cfg.get("openai_api_key", "")


# ---------------------------------------------------------------- audio
# PortAudio är inte trådsäkert: en enda gemensam instans, och öppna/stäng strömmar under lås.
_PA = None
_PA_LOCK = threading.Lock()
_ACTIVE = 0


def pa_instance(refresh=False):
    global _PA
    with _PA_LOCK:
        if refresh and _PA is not None and _ACTIVE == 0:
            _PA.terminate()   # ladda om enhetslistan (t.ex. nytt headset), bara när inget spelar in
            _PA = None
        if _PA is None:
            _PA = pa.PyAudio()
        return _PA


def list_devices(refresh=False):
    p = pa_instance(refresh)
    with _PA_LOCK:
        w = p.get_host_api_info_by_type(pa.paWASAPI)
        mics, loops = [], []
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] != w["index"] or d["maxInputChannels"] < 1:
                continue
            (loops if d.get("isLoopbackDevice") else mics).append(d["name"])
        def_mic = p.get_device_info_by_index(w["defaultInputDevice"])["name"]
        def_out = p.get_device_info_by_index(w["defaultOutputDevice"])["name"]
        def_loop = next((l for l in loops if l.startswith(def_out)), loops[0] if loops else "")
        return mics, loops, def_mic, def_loop


def to_24k_mono(raw: bytes, channels: int, rate: int) -> np.ndarray:
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
    if channels > 1:
        x = x.reshape(-1, channels).mean(axis=1)
    if rate != RATE and len(x):
        if rate % RATE == 0:
            f = rate // RATE
            n = len(x) // f * f
            x = x[:n].reshape(-1, f).mean(axis=1)
        else:
            n_out = int(len(x) * RATE / rate)
            x = np.interp(np.linspace(0, len(x) - 1, n_out), np.arange(len(x)), x)
    return x


_FOREIGN = re.compile(r"[\u0400-\u04FF\u0590-\u06FF\u0E00-\u0E7F\u3040-\u30FF\u3400-\u9FFF\uAC00-\uD7AF]")


def is_garbage(text: str) -> bool:
    """Felhörningar: andra skriftsystem (kinesiska, thai, kyrilliska ...) eller bara ett tecken."""
    return bool(_FOREIGN.search(text)) or len(text.strip(" .,!?-")) < 2


class Source:
    """En ljudkälla -> egen Realtime-transkribering. Enkel energi-VAD styr turerna."""

    def __init__(self, label, device_name, key, on_event, hints=None):
        self.label, self.device_name, self.key, self.on_event = label, device_name, key, on_event
        self.hints = hints or {}
        self.stop = threading.Event()
        self.ws = None
        self.lock = threading.Lock()
        self.speaking = False
        self.last_voice = 0.0
        self.appended = False
        self.noise = 150.0
        self.preroll: list[np.ndarray] = []
        self.commit_times: list[tuple[float, str]] = []   # (tal slut, talare) per commit
        self.item_label: dict[str, str] = {}
        self.ptt = False    # håll-knapp: hålls = motparten pratar, annars = du
        self.held = False
        self.item_end: dict[str, float] = {}
        self.partial: dict[str, str] = {}
        self.early: dict[str, str] = {}
        self.level = 0.0

    def start(self):
        self.ws = connect(RT_URL, additional_headers={"Authorization": f"Bearer {self.key}"},
                          max_size=None, open_timeout=10, close_timeout=1)
        tr = {"model": TRANSCRIBE_MODEL, "languages": ["sv"], "delay": DELAY}
        tr.update(self.hints)   # prompt + keywords från formuläret
        self.ws.send(json.dumps({
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {"input": {
                    "format": {"type": "audio/pcm", "rate": RATE},
                    "transcription": tr,
                    "turn_detection": None,
                }},
            },
        }))
        for fn in (self._capture, self._receive, self._watchdog):
            threading.Thread(target=fn, daemon=True).start()

    def close(self):
        self.stop.set()
        try:
            self.ws.close()
        except Exception:
            pass

    def _send(self, obj):
        with self.lock:
            self.ws.send(json.dumps(obj))

    def _append(self, x: np.ndarray):
        pcm = np.clip(x, -32768, 32767).astype("<i2").tobytes()
        self._send({"type": "input_audio_buffer.append", "audio": base64.b64encode(pcm).decode()})
        self.appended = True

    def _commit(self, label=None, t_end=None):
        if self.appended:
            self.appended = False
            self.commit_times.append((t_end or self.last_voice or time.perf_counter(), label or self.label))
            self._send({"type": "input_audio_buffer.commit"})

    def press(self):
        """Håll-knappen trycks ned: det som sagts hittills är ditt, det som följer är motpartens."""
        if self.held:
            return
        try:
            if self.speaking:
                self._commit(label="Du")
            self.held = True
            self.speaking = True
            self.last_voice = time.perf_counter()
            for b in self.preroll:          # ta med ljudet strax innan du tryckte
                self._append(b)
            self.preroll.clear()
        except Exception as e:
            log(self.label, "press fel:", repr(e))

    def release(self):
        """Håll-knappen släpps: skicka motpartens replik direkt, utan att vänta på tystnad."""
        if not self.held:
            return
        self.held = False
        self.speaking = False
        try:
            self._commit(label="Motpart", t_end=time.perf_counter())
        except Exception as e:
            log(self.label, "release fel:", repr(e))

    def _capture(self):
        global _ACTIVE
        p = pa_instance()
        try:
            with _PA_LOCK:
                wasapi = p.get_host_api_info_by_type(pa.paWASAPI)["index"]
                dev = next(p.get_device_info_by_index(i) for i in range(p.get_device_count())
                           if p.get_device_info_by_index(i)["name"] == self.device_name
                           and p.get_device_info_by_index(i)["hostApi"] == wasapi)
                ch = max(1, min(2, int(dev["maxInputChannels"])))
                rate = int(dev["defaultSampleRate"])
                log(self.label, "öppnar", self.device_name, "index", dev["index"], "rate", rate, "ch", ch)
                st = p.open(format=pa.paInt16, channels=ch, rate=rate, input=True,
                            input_device_index=dev["index"], frames_per_buffer=rate // 50)
                _ACTIVE += 1
                log(self.label, "öppnad OK")
        except Exception as e:
            log(self.label, "FEL vid öppning:", e)
            self.on_event("error", self.label, "Mikrofonen går inte att öppna. Är den mutad (Fn-tangent eller Windows-ljud)? Avmuta och tryck Avsluta, sedan Starta." if self.label in ("Du", "Samtal") else f"Kunde inte öppna {self.device_name}: {e}")
            return
        peak, tlog = 0.0, time.perf_counter()
        try:
            while not self.stop.is_set():
                raw = st.read(rate // 50, exception_on_overflow=False)  # 20 ms
                x = to_24k_mono(raw, ch, rate)
                rms = float(np.sqrt(np.mean(x * x))) if len(x) else 0.0
                self.level = rms
                now = time.perf_counter()
                peak = max(peak, rms)
                if now - tlog >= 1.0:
                    log(self.label, f"nivå topp {peak:.0f} brus {self.noise:.0f} talar={self.speaking}")
                    peak, tlog = 0.0, now
                # Tröskel följer bakgrundsbruset, så även lågt samtalsljud räknas som tal.
                voiced = self.held or rms > max(60.0, self.noise * 3.0)
                if not voiced:
                    self.noise = 0.98 * self.noise + 0.02 * rms
                if voiced:
                    self.last_voice = now
                    if not self.speaking:
                        self.speaking = True
                        for b in self.preroll:
                            self._append(b)
                        self.preroll.clear()
                        self.on_event("speech", self.label, None)
                if self.speaking:
                    self._append(x)
                else:
                    self.preroll.append(x)
                    while len(self.preroll) > int(PREROLL / 0.02):
                        self.preroll.pop(0)
        except Exception as e:
            if not self.stop.is_set():
                self.on_event("error", self.label, f"Ljudfel: {e}")
        finally:
            with _PA_LOCK:
                st.close()
                _ACTIVE -= 1

    def _watchdog(self):
        # Loopback skickar inga paket när det är helt tyst, så tystnad mäts på väggklockan.
        while not self.stop.is_set():
            time.sleep(0.03)
            if self.speaking and not self.held and time.perf_counter() - self.last_voice > SILENCE_HANG:
                self.speaking = False
                try:
                    self._commit(label="Du" if self.ptt else None)
                except Exception:
                    pass

    def _receive(self):
        try:
            for msg in self.ws:
                try:
                    self._handle(json.loads(msg))
                except Exception as e:
                    log(self.label, "fel i händelse:", repr(e))
        except Exception as e:
            if not self.stop.is_set():
                self.on_event("error", self.label, f"Anslutningen bröts: {e}")

    def _handle(self, ev):
        if True:
            if True:
                t = ev.get("type", "")
                if t == "input_audio_buffer.committed":
                    iid = ev.get("item_id")
                    t_end, lab = self.commit_times.pop(0) if self.commit_times else (time.perf_counter(), self.label)
                    self.item_end[iid] = t_end
                    self.item_label[iid] = lab
                    # Snabbspår: texten har redan strömmat in, så vänta inte på "completed".
                    early = self.partial.get(iid, "").strip()
                    if EARLY and len(early.split()) >= 2:
                        self.early[iid] = early
                        self.on_event("final", lab, (early, t_end))
                elif t.endswith("input_audio_transcription.delta"):
                    iid = ev.get("item_id")
                    self.partial[iid] = self.partial.get(iid, "") + ev.get("delta", "")
                    who = ("Motpart" if self.held else "Du") if self.ptt else self.label
                    self.on_event("partial", who, self.partial[iid])
                elif t.endswith("input_audio_transcription.completed"):
                    iid = ev.get("item_id")
                    text = (ev.get("transcript") or self.partial.get(iid, "")).strip()
                    self.partial.pop(iid, None)
                    t_end = self.item_end.pop(iid, time.perf_counter())
                    lab = self.item_label.pop(iid, self.label)
                    early = self.early.pop(iid, None)
                    if text and is_garbage(text):
                        log(self.label, "ignorerar skräptext:", text[:60])
                        return
                    if early is not None:
                        if text and text != early:
                            self.on_event("fix", lab, (early, text))
                    elif text:
                        self.on_event("final", lab, (text, t_end))
                elif t == "error":
                    self.on_event("error", self.label, ev.get("error", {}).get("message", str(ev)))


# ---------------------------------------------------------------- coach
def make_hints(form: dict) -> dict:
    """Ordlista till tal-till-text: namn och produktord som annars lätt stavas fel."""
    import re
    text = " ".join(str(form.get(k, "")) for k in ("who", "me", "product", "args", "notes"))
    words, seen = [], set()
    for w in re.findall(r"[A-Za-zÅÄÖåäöÉé][\w\-]{2,}", text):
        if (w[0].isupper() or "-" in w) and w.lower() not in seen:
            seen.add(w.lower()); words.append(w)
    hints = {}
    if words:
        hints["keywords"] = words[:20]
    ctx = ", ".join(str(form.get(k, "")).strip() for k in ("who", "product") if form.get(k))
    hints["prompt"] = ("Svenskt telefonsamtal, säljsamtal. " + ctx)[:400]
    return hints


PHONE_NOTE = (
    "\nVIKTIGT: Samtalet fångas av EN mikrofon (telefon på högtalare), så rader saknar talare. "
    "Entreprenören (du coachar) och motparten blandas. Avgör själv vem som sa sista repliken. "
    "Om sista repliken sannolikt sades av entreprenören själv, eller om motparten inte har sagt "
    "något nytt som kräver svar, skriv exakt ordet SKIP och inget annat.\n"
)


def build_instructions(form: dict, phone: bool = False) -> str:
    return (
        "Du är en blixtsnabb säljcoach som viskar i örat på en svensk entreprenör under ett "
        "pågående telefonsamtal. Du får transkriptionen (kan innehålla hörfel). Ge det BÄSTA "
        "nästa sak att säga högt, på naturlig talad svenska, max 25 ord, utan citattecken. "
        "Var konkret, vänlig och driv mot målet. Hantera invändningar med en kort fråga eller "
        "ett argument från listan. Hitta aldrig på fakta, priser eller löften som inte står nedan. "
        "Använd aldrig platshållare som [ditt namn]. Behövs namn eller företag (t.ex. om motparten "
        "frågar vem det är), använd det som står under 'Du är'. Står inget där, skriv runt det.\n"
        "Format: rad 1 = förslaget. Sedan högst 2 korta alternativ, varsin rad som börjar med '- '.\n\n"
        f"Du är: {form.get('me', '')}\n"
        f"Vem som rings: {form.get('who', '')}\n"
        f"Syfte: {form.get('goal', '')}\n"
        f"Vad som säljs: {form.get('product', '')}\n"
        f"Pris: {form.get('price', '')}\n"
        f"Viktiga argument: {form.get('args', '')}\n"
        f"Övrigt: {form.get('notes', '')}\n"
    ) + (PHONE_NOTE if phone else "")


class Coach:
    def __init__(self, key, form, on_event, phone=False):
        self.client = OpenAI(api_key=key, timeout=15)
        self.phone = phone
        self.instructions = build_instructions(form, phone)
        self.on_event = on_event
        self.history: list[tuple[str, str]] = []
        self.gen = 0

    def warmup(self):
        threading.Thread(target=self._warm, daemon=True).start()

    def _warm(self):
        try:
            self.client.responses.create(model=COACH_MODEL, input="Svara: ok",
                                         reasoning={"effort": "none"}, max_output_tokens=16)
        except Exception as e:
            self.on_event("error", "AI", f"{COACH_MODEL}: {e}")

    def add(self, who, text):
        # Slå ihop korta pauser ("Hej," + "det är Anna") till en replik.
        if self.history and self.history[-1][0] == who and not self.phone:
            self.history[-1] = (who, self.history[-1][1] + " " + text)
        else:
            self.history.append((who, text))
        self.history = self.history[-14:]

    def fix(self, who, early, full):
        """Byt ut snabbspårets text mot den slutliga transkriptionen."""
        for i in range(len(self.history) - 1, -1, -1):
            w, t = self.history[i]
            if w == who and early in t:
                self.history[i] = (w, t.replace(early, full))
                return

    def suggest(self, t_end):
        self.gen += 1
        threading.Thread(target=self._run, args=(self.gen, t_end), daemon=True).start()

    def _run(self, gen, t_end):
        convo = "\n".join(f"{w}: {t}" for w, t in self.history) or "(samtalet har precis börjat)"
        t_req = time.perf_counter()
        first = None
        out = ""
        try:
            stream = self.client.responses.create(
                model=COACH_MODEL, instructions=self.instructions,
                input=f"Samtalet hittills:\n{convo}\n\nVad ska jag säga nu?",
                reasoning={"effort": "none"}, max_output_tokens=160, stream=True)
            for ev in stream:
                if gen != self.gen:
                    return  # ett nyare förslag är på väg
                if ev.type == "response.output_text.delta":
                    if first is None:
                        first = time.perf_counter()
                    out += ev.delta
                    if self.phone and len(out.strip()) < 5 and "SKIP".startswith(out.strip().upper()):
                        continue  # kan bli SKIP: visa inget förrän vi vet
                    if self.phone and out.strip().upper().startswith("SKIP"):
                        self.on_event("skip", "AI", None)
                        return
                    self.on_event("suggestion", "AI", (out, None))
            if self.phone and out.strip().upper().startswith("SKIP"):
                self.on_event("skip", "AI", None)
                return
            done = time.perf_counter()
            lat = {"tal_slut_till_text": t_req - t_end, "forsta_ord": (first or done) - t_end,
                   "klart": done - t_end, "llm_forsta_token": (first or done) - t_req}
            self.on_event("suggestion", "AI", (out, lat))
        except Exception as e:
            self.on_event("error", "AI", str(e))


def app_windows() -> list:
    """Appens synliga toppfönster (titel Viska) i den här processen."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    pid = os.getpid()
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        p = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value == pid and user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value == APP_NAME:
                found.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return found


def set_window_alpha(alpha: int, hwnds=None) -> list:
    """Sätt hela fönstrets genomskinlighet (0-255) via layered window."""
    import ctypes
    user32 = ctypes.windll.user32
    GWL_EXSTYLE, WS_EX_LAYERED, LWA_ALPHA = -20, 0x80000, 0x2
    hwnds = app_windows() if hwnds is None else hwnds
    for h in hwnds:
        user32.SetWindowLongW(h, GWL_EXSTYLE, user32.GetWindowLongW(h, GWL_EXSTYLE) | WS_EX_LAYERED)
        user32.SetLayeredWindowAttributes(h, 0, max(0, min(255, int(alpha))), LWA_ALPHA)
    return hwnds


def fade_out_window(duration: float = 0.38, ease_in: bool = False) -> None:
    """Tona ut hela fönstret, t.ex. innan appen stängs eller minimeras.
    ease_in=True: långsamt i början, snabbt på slutet (så genie-formen hinner synas)."""
    hwnds = set_window_alpha(255)
    t0 = time.perf_counter()
    while True:
        p = min(1.0, (time.perf_counter() - t0) / duration)
        a = 1 - p ** 2.2 if ease_in else (1 - p) ** 2
        set_window_alpha(255 * a, hwnds)
        if p >= 1.0:
            break
        time.sleep(0.012)


def fade_in_window(duration: float = 0.34) -> None:
    """Tona in fönstret från osynligt till helt synligt."""
    hwnds = app_windows()
    t0 = time.perf_counter()
    while True:
        p = min(1.0, (time.perf_counter() - t0) / duration)
        set_window_alpha(255 * (1 - (1 - p) ** 3), hwnds)
        if p >= 1.0:
            break
        time.sleep(0.012)


def set_topmost(on: bool) -> None:
    """Alltid överst via Windows direkt, asynkront (pywebviews on_top kan låsa sig)."""
    import ctypes
    user32 = ctypes.windll.user32
    found = app_windows()
    HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
    flags = 0x0001 | 0x0002 | 0x0010 | 0x4000   # NOSIZE | NOMOVE | NOACTIVATE | ASYNCWINDOWPOS
    for h in found:
        user32.SetWindowPos(h, HWND_TOPMOST if on else HWND_NOTOPMOST, 0, 0, 0, 0, flags)
    log("topmost", on, "fönster:", len(found))


# ---------------------------------------------------------------- UI (HTML/CSS via WebView2)
def resource(*parts):
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


class Api:
    """Anropas från JavaScript i ui/index.html (window.pywebview.api.*)."""

    def __init__(self):
        self.cfg = load_config()
        self.q: queue.Queue = queue.Queue()
        self.sources: list[Source] = []
        self.coach: Coach | None = None
        self.paused = False
        self.phone = False
        self._win = None
        self._alpha_hidden = False          # fönstret är osynligt (alpha 0) efter minimering
        self._alpha_lock = threading.Lock()
        # Alla fönsteranrop (JS, storlek, överst) går genom EN tråd, i tur och ordning.
        # Annars kan nivåmätaren och Avsluta vänta på varandra och låsa fönstret.
        self._uiq: queue.Queue = queue.Queue()
        threading.Thread(target=self._ui_loop, daemon=True).start()

    def _ui_loop(self):
        while True:
            fn = self._uiq.get()
            tag = getattr(fn, "_tag", "")
            t0 = time.perf_counter()
            if tag:
                log("ui start:", tag)
            try:
                fn()
            except Exception as e:
                log("ui-fel:", tag, repr(e))
            dt = time.perf_counter() - t0
            if tag or dt > 1.0:
                log("ui klar:", tag or "(js)", f"{dt:.2f}s")

    def _ui(self, fn, tag=""):
        if tag:
            fn._tag = tag
        self._uiq.put(fn)

    def _ui_sync(self, fn, timeout=10):
        """Kör i UI-kön och vänta på svaret (används av tester)."""
        box, done = [], threading.Event()
        self._ui(lambda: (box.append(fn()), done.set()), "sync")
        done.wait(timeout)
        return box[0] if box else None

    # ---------- formulär
    def init(self):
        mics, loops, def_mic, def_loop = list_devices()
        return {
            "form": self.cfg.get("form", {}),
            "mode": self.cfg.get("mode", "computer"),
            "mics": mics, "loops": loops,
            "mic": self.cfg.get("mic") if self.cfg.get("mic") in mics else def_mic,
            "loop": self.cfg.get("loop") if self.cfg.get("loop") in loops else def_loop,
            "needKey": not (os.environ.get("OPENAI_API_KEY") or env_file_key()),
            "hasSavedKey": bool(self.cfg.get("openai_api_key")),
        }

    def save(self, data):
        self.cfg["form"] = data.get("form", {})
        self.cfg["mic"], self.cfg["loop"] = data.get("mic"), data.get("loop")
        self.cfg["mode"] = "phone" if data.get("mode") == "phone" else "computer"
        if data.get("key"):
            self.cfg["openai_api_key"] = data["key"].strip()
        save_config(self.cfg)
        return True

    def start(self, data):
        log("start()", "läge:", data.get("mode"), "mic:", data.get("mic"))
        if not data.get("mic") or not data.get("loop"):
            _, _, def_mic, def_loop = list_devices()
            data["mic"], data["loop"] = data.get("mic") or def_mic, data.get("loop") or def_loop
        self.save(data)
        key = api_key(self.cfg)
        if not key:
            return {"ok": False, "error": "Lägg in en OpenAI API-nyckel först."}
        self.phone = self.cfg["mode"] == "phone"
        try:
            hints = make_hints(self.cfg["form"])
            # Telefonläge: du håller en knapp när motparten pratar, så talaren är känd (ingen gissning).
            self.coach = Coach(key, self.cfg["form"], self.emit, phone=False)
            self.coach.warmup()
            if self.phone:   # telefon på högtalare: allt hörs i mikrofonen
                src = Source("Samtal", self.cfg["mic"], key, self.emit, hints)
                src.ptt = True
                self.sources = [src]
            else:
                self.sources = [Source("Motpart", self.cfg["loop"], key, self.emit, hints),
                                Source("Du", self.cfg["mic"], key, self.emit, hints)]
            for s in self.sources:
                s.start()
        except Exception as e:
            self.stop()
            return {"ok": False, "error": f"Kunde inte ansluta: {e}"}
        h = 490 if self.phone else 420
        self._ui(lambda: self._win.resize(460, h), "resize live")   # krymp först när allt är igång
        set_topmost(True)
        threading.Thread(target=self._levels, daemon=True).start()
        return {"ok": True}

    def stop(self):
        old, self.sources = self.sources, []
        # Stäng anslutningarna i bakgrunden: en websocket-stängning kan ta flera sekunder.
        threading.Thread(target=lambda: [s.close() for s in old], daemon=True).start()
        log("stop(): avslutat, stänger", len(old), "källor i bakgrunden")
        set_topmost(False)
        self._ui(lambda: self._win.resize(SETUP_W, SETUP_H), "resize setup")
        return True

    def minimize(self):
        """Genie-effekten spelas i JS; här tonar vi ut fönstret, minimerar och återställer opaciteten."""
        def go():
            try:
                fade_out_window(0.42, ease_in=True)
            except Exception as e:
                log("minimera-fade fel:", repr(e))
            # Fönstret förblir osynligt medan det är minimerat; det tonas in igen vid återställning.
            # (Annars blinkar en grå ruta medan WebView2 ritar om.)
            self._alpha_hidden = True
            self._ui_sync(self._win.minimize)
        threading.Thread(target=go, daemon=True).start()

    def on_restored(self, *_):
        self._ui(lambda: self._win.evaluate_js("restoredApp()"), "restored")
        self.ensure_visible()

    def ensure_visible(self):
        """Tona in fönstret igen efter minimering. Anropas både från Python och JS, så det sker säkert."""
        with self._alpha_lock:
            if not self._alpha_hidden:
                return
            self._alpha_hidden = False

        def go():
            time.sleep(0.06)     # låt WebView2 hinna rita först
            try:
                fade_in_window(0.34)
            except Exception as e:
                log("fade-in fel:", repr(e))
                try:
                    set_window_alpha(255)
                except Exception:
                    pass
        threading.Thread(target=go, daemon=True).start()

    def close(self):
        log("close(): stänger appen")
        for s in self.sources:
            s.stop.set()
        try:
            fade_out_window(float(os.environ.get("SC_FADE_SECS", "0.38")))
        except Exception as e:
            log("fade-fel:", repr(e))
        # Ljudtrådar kan sitta fast i en blockerande läsning och fönstret kan låsa sig vid
        # stängning, så avsluta processen direkt. Allt viktigt är redan sparat vid Start.
        os._exit(0)

    def ptt(self, down):
        """Håll-knappen (telefonläge): ned = motparten börjar prata, upp = de är klara."""
        for s in self.sources:
            if s.ptt:
                (s.press if down else s.release)()
        log("håll-knapp", "ned" if down else "upp")
        return bool(down)

    def pause(self, value):
        self.paused = bool(value)
        return self.paused

    def force(self):
        if self.coach:
            self.coach.suggest(time.perf_counter())
        return True

    # ---------- Python -> JS
    def emit(self, kind, who, data):
        if kind in ("final", "skip", "error"):
            log("händelse", kind, who, str(data)[:140])
        elif kind == "suggestion" and data[1]:
            log("förslag", f"{data[1]['forsta_ord']:.2f}s", data[0].strip().replace("\n", " | ")[:160])
        if kind == "final":
            text, t_end = data
            if self.coach:
                self.coach.add(who, text)
                if who == "Motpart" and not self.paused:
                    self.coach.suggest(t_end)
            data = text
        elif kind == "fix":
            early, full = data
            if self.coach:
                self.coach.fix(who, early, full)
            kind, data = "final", full
        elif kind == "suggestion":
            text, lat = data
            if lat:
                safe_print("LATENCY", json.dumps({k: round(v, 3) for k, v in lat.items()}))
            data = {"text": text, "lat": lat}
        elif kind in ("partial", "error"):
            safe_print(kind.upper(), who, data)
        self._js(kind, who, data)

    def _js(self, kind, who, data):
        if kind == "levels" and self._uiq.qsize() > 5:
            return   # nivåmätaren får hoppa över en bild hellre än att köa upp
        script = f"onEvent({json.dumps({'kind': kind, 'who': who, 'data': data})})"
        self._ui(lambda: self._win.evaluate_js(script))

    def _levels(self):
        while self.sources:
            lv = {s.label: min(1.0, s.level / 4000.0) if s.speaking else 0.0 for s in self.sources}
            self._js("levels", None, lv)
            time.sleep(0.12)


def main():
    import faulthandler
    try:
        os.makedirs(CONFIG_DIR, exist_ok=True)
        faulthandler.enable(file=open(os.path.join(CONFIG_DIR, "crash.log"), "a"), all_threads=True)
    except Exception:
        pass
    pa_instance()  # starta PortAudio i huvudtråden (lever hela körningen), inte i en kortlivad UI-tråd
    api = Api()
    api._win = webview.create_window(APP_NAME, resource("ui", "index.html"), js_api=api,
                                       width=SETUP_W, height=SETUP_H, min_size=(420, 300),
                                       frameless=True, easy_drag=False, hidden=True,
                                       background_color="#FAF8F5")
    # Visa fönstret först när sidan har laddat (annars syns en mörk ruta medan WebView2 startar),
    # och starta då introt. Säkerhetsnät: visa ändå efter 6 s.
    shown = threading.Event()

    def reveal(*_):
        if shown.is_set():
            return
        shown.set()
        log("visar fönstret")
        api._ui(lambda: (api._win.show(), api._win.evaluate_js("startIntro()")), "show")

    api._win.events.loaded += reveal
    api._win.events.restored += api.on_restored
    threading.Timer(6.0, reveal).start()
    demo = os.environ.get("SC_DEMO")  # visar live-läget med påhittad data, för designkontroll
    if demo:
        def _demo():
            time.sleep(2.5)
            api._ui_sync(lambda: api._win.resize(460, 420))
            api._ui_sync(lambda: api._win.evaluate_js(
                'enterLive();onEvent({kind:"levels",data:{"Du":0,"Motpart":0.7}});'
                'onEvent({kind:"final",who:"Motpart",data:"Vi har faktiskt ingen budget för sånt här just nu."});'
                'onEvent({kind:"suggestion",who:"AI",data:{text:"Förstår helt. Om första månaden var gratis, '
                'hade ni testat med en klass?\\n- Vad gör ni idag när elever behöver läxhjälp?\\n'
                '- Kan vi boka 15 minuter nästa vecka så visar jag?",lat:{forsta_ord:1.3}}});'))
        webview.start(_demo)
    elif os.environ.get("SC_AUTOSTART"):  # för automatiska tester: klickar "Starta samtal"
        def _auto():
            time.sleep(1)
            api._ui_sync(lambda: api._win.evaluate_js("(async()=>{while(!ready){boot();await new Promise(r=>setTimeout(r,200));}"
                                 "await ready;start();})()"))
            time.sleep(6)
            log("autostart JS-status:", api._ui_sync(lambda: api._win.evaluate_js(
                "JSON.stringify({errs: window.__errs, ready: !!ready, booted: booted, mode: mode})")))
            if os.environ.get("SC_TESTPTT"):    # test: håll knappen i 6 s (ljud spelas upp utifrån)
                api._ui_sync(lambda: api._win.evaluate_js("pttDown()"))
                time.sleep(6)
                api._ui_sync(lambda: api._win.evaluate_js("pttUp()"))
                time.sleep(6)
            if os.environ.get("SC_TESTSTOP"):   # test: tryck Avsluta, sedan X
                log("test: klickar Avsluta")
                api._ui_sync(lambda: api._win.evaluate_js('document.getElementById("stopBtn").click()'))
                time.sleep(3)
                log("test: efter Avsluta, klickar X")
                api._ui_sync(lambda: api._win.evaluate_js('document.querySelector(".winbtns .x").click()'))
        webview.start(_auto)
    elif os.environ.get("SC_TESTMIN"):  # för designkontroll: minimera, återställ, stäng
        def _min():
            time.sleep(3)
            api._ui_sync(lambda: api._win.evaluate_js("minimizeApp()"))
            time.sleep(2)
            api._ui_sync(api._win.restore)
            time.sleep(2.5)
            api._ui_sync(lambda: api._win.evaluate_js("quit()"))
        webview.start(_min)
    elif os.environ.get("SC_TESTQUIT"):  # för designkontroll: spelar avslutningen efter 3 s
        def _quit():
            time.sleep(3)
            api._ui_sync(lambda: api._win.evaluate_js("quit()"))
        webview.start(_quit)
    else:
        webview.start()
    os._exit(0)   # fönstret är stängt: avsluta direkt även om ljudtrådar fortfarande blockerar


if __name__ == "__main__":
    main()
