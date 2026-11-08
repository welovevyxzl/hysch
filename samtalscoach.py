"""Viska - lyssnar på samtalet och visar vad du kan svara, i realtid.

Ljud: mikrofon (= Du) + Windows systemljud via WASAPI loopback (= Motpart).
Tal-till-text: varje replik skickas som en hel ljudfil till OpenAI när personen tystnat (inte live),
modell gpt-4o-transcribe, med whisper-1 som reserv om kontot saknar den.
Svarsförslag: OpenAI Responses API, modell gpt-6-luna, reasoning effort "none", strömmat.
API-nyckel: miljövariabeln OPENAI_API_KEY, annars .env i projektmappen, annars %APPDATA%\\Viska\\config.json.
"""
from __future__ import annotations

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

APP_NAME = "Viska"
STT_MODELS = ["gpt-4o-transcribe", "whisper-1"]   # tal-till-text; nästa i listan används om kontot saknar den första
COACH_MODEL = "gpt-6-luna"
RATE = 24000                 # ljudet skickas som 24 kHz mono
SILENCE_HANG = 0.75          # sekunder tystnad = repliken är slut (längre = färre avklippta meningar)
SETUP_W, SETUP_H = 900, 760
PREROLL = 0.3                # sekunder ljud före talstart som skickas med
MIN_VOICED = 0.25            # s tal: kortare ljud är troligen brus och skrivs inte ut
CHUNK_SOFT = 12.0            # s: en mycket lång replik skickas i bitar, vid nästa lilla paus efter så här länge
CHUNK_HARD = 20.0            # s: ... och senast här, så väntan efter att man tystnat aldrig blir lång

SUMMARY_MODEL = "gpt-6.1-terra"   # analys efter samtalet

CONFIG_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
CALLS_DIR = os.path.join(CONFIG_DIR, "calls")      # ett JSON-dokument per samtal
CALL_ID = re.compile(r"\d{8}-\d{6}")


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


def wav_bytes(x: np.ndarray) -> bytes:
    """24 kHz mono PCM16 som en WAV-fil i minnet."""
    import io
    import wave
    pcm = np.clip(x, -32768, 32767).astype("<i2").tobytes()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def normalize(x: np.ndarray) -> np.ndarray:
    """Höj svaga inspelningar (lågt mikrofonljud) så att modellen hör orden bättre."""
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if 200 < peak < 9000:
        x = x * min(8.0, 12000.0 / peak)
    return x


class Source:
    """En ljudkälla. En enkel energi-VAD hittar repliker; varje replik transkriberas i ett svep
    (inte live), vilket ger modellen hela meningen som sammanhang."""

    def __init__(self, label, device_name, key, on_event, hints=None):
        self.label, self.device_name, self.key, self.on_event = label, device_name, key, on_event
        self.hints = hints or {}
        self.client = OpenAI(api_key=key, timeout=30)
        self.models = list(STT_MODELS)        # första som fungerar på kontot används
        self.stop = threading.Event()
        self.speaking = False
        self.last_voice = 0.0
        self.noise = 150.0
        self.preroll: list[np.ndarray] = []
        self.blk = threading.RLock()          # skyddar ljudbufferten
        self.clip: list[np.ndarray] = []      # ljud i pågående replik
        self.clip_secs = 0.0
        self.voiced_secs = 0.0                # hur mycket av det som faktiskt var tal
        self.turn_chunks = 0                  # delbitar skickade i pågående (mycket lång) replik
        self.jobs: queue.Queue = queue.Queue()  # repliker i tur och ordning, så texten aldrig byter plats
        self.ptt = False    # håll-knapp: hålls = motparten pratar, annars = du
        self.held = False
        self.level = 0.0

    def start(self):
        for fn in (self._capture, self._watchdog, self._worker):
            threading.Thread(target=fn, daemon=True).start()

    def close(self):
        self.stop.set()
        self.jobs.put(None)

    def _append(self, x: np.ndarray):
        with self.blk:
            self.clip.append(x)
            self.clip_secs += len(x) / RATE

    def _commit(self, label=None, t_end=None, interim=False):
        """Replikens ljud är klart: lägg det i transkriberingskön.
        interim=True: en delbit mitt i en mycket lång replik (coachen svarar inte på den)."""
        with self.blk:
            if not self.clip:
                return
            who = label or self.label
            after_chunks = self.turn_chunks > 0
            voiced = self.voiced_secs
            audio = np.concatenate(self.clip)
            self.clip, self.clip_secs, self.voiced_secs = [], 0.0, 0.0
            if voiced < MIN_VOICED:
                if interim or not after_chunks:
                    log(self.label, f"slänger {voiced * 1000:.0f} ms tal (för kort, troligen brus)")
                    if not interim:
                        self.turn_chunks = 0
                    return
                audio = None    # kort slut efter delbitar: inget att skriva, men repliken är ändå slut
            self.turn_chunks = self.turn_chunks + 1 if interim else 0
            job = {"audio": audio, "who": who, "t": t_end or self.last_voice or time.perf_counter(),
                   "interim": interim, "after_chunks": after_chunks}
        self.jobs.put(job)
        if audio is not None:
            self.on_event("partial", who, "…")      # visar att vi skriver ut det som sades

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
                loud = rms > max(60.0, self.noise * 3.0)
                voiced = self.held or loud
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
                    if voiced:
                        self.voiced_secs += 0.02
                    # Mycket lång replik: skicka en bit vid en liten paus mellan ord (eller senast
                    # vid CHUNK_HARD), så att väntan efter att man tystnat aldrig blir lång.
                    b = self.clip_secs
                    if b >= CHUNK_HARD or (b >= CHUNK_SOFT and not loud):
                        who = ("Motpart" if self.held else "Du") if self.ptt else None
                        self._commit(label=who, t_end=now, interim=True)
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

    # ---------- transkribering
    def _worker(self):
        while not self.stop.is_set():
            job = self.jobs.get()
            if job is None:
                return
            try:
                self._process(job)
            except Exception as e:
                log(self.label, "transkribering fel:", repr(e)[:300])
                self.on_event("error", self.label, f"Transkribering: {e}")
                self.on_event("partial", job["who"], "")

    def _process(self, job):
        text = ""
        if job["audio"] is not None:
            t0 = time.perf_counter()
            text = self._transcribe(job["audio"])
            log(self.label, f"skrev ut {len(job['audio']) / RATE:.1f} s ljud på {time.perf_counter() - t0:.2f} s")
            if text and is_garbage(text):
                log(self.label, "ignorerar skräptext:", text[:60])
                text = ""
        if text or (not job["interim"] and job["after_chunks"]):
            # Tom slutbit efter delbitar: repliken är ändå slut, så coachen ska svara.
            self.on_event("final", job["who"], (text, job["t"], job["interim"]))
        elif job["audio"] is not None:
            self.on_event("partial", job["who"], "")     # ta bort "…"

    def _prompt(self) -> str:
        p = self.hints.get("prompt", "")
        kw = self.hints.get("keywords")
        if kw:
            p += " Namn och ord: " + ", ".join(kw) + "."
        return p[:600]

    def _transcribe(self, audio: np.ndarray) -> str:
        wav = wav_bytes(normalize(audio))
        prompt = self._prompt()
        while True:
            model = self.models[0]
            try:
                r = self.client.audio.transcriptions.create(
                    model=model, file=("tal.wav", wav, "audio/wav"), language="sv", prompt=prompt)
                return (getattr(r, "text", None) or (r if isinstance(r, str) else "")).strip()
            except Exception as e:
                msg = str(e).lower()
                no_model = getattr(e, "status_code", None) in (400, 403, 404) and "model" in msg
                if no_model and len(self.models) > 1:
                    # Kontot saknar modellen: prova nästa (whisper-1 finns på alla konton).
                    log(self.label, f"modellen {model} gick inte, provar {self.models[1]}:", str(e)[:160])
                    self.models.pop(0)
                    continue
                raise


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


# ---------------------------------------------------------------- samtal: spara + analysera
def save_call(rec: dict) -> None:
    """Spara ett samtal atomiskt (skriv till temp, byt sedan namn)."""
    os.makedirs(CALLS_DIR, exist_ok=True)
    path = os.path.join(CALLS_DIR, rec["id"] + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_call(call_id: str) -> dict | None:
    if not CALL_ID.fullmatch(call_id or ""):
        return None
    try:
        with open(os.path.join(CALLS_DIR, call_id + ".json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _arr(item):
    return {"type": "array", "items": item}


_S = {"type": "string"}
SUMMARY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "summary", "outcome", "outcome_text", "sentiment", "they_said", "you_said",
                 "objections", "todos", "score", "score_reason", "strengths", "improvements",
                 "next_step", "follow_up_email"],
    "properties": {
        "title": _S, "summary": _S,
        "outcome": {"type": "string", "enum": ["Sålt", "Bokat möte", "Uppföljning", "Nej tack", "Oklart"]},
        "outcome_text": _S,
        "sentiment": {"type": "string", "enum": ["Positiv", "Neutral", "Negativ"]},
        "they_said": _arr(_S), "you_said": _arr(_S),
        "objections": _arr({"type": "object", "additionalProperties": False,
                            "required": ["objection", "handled", "better_reply"],
                            "properties": {"objection": _S, "handled": _S, "better_reply": _S}}),
        "todos": _arr({"type": "object", "additionalProperties": False, "required": ["task", "owner", "due"],
                       "properties": {"task": _S, "owner": {"type": "string", "enum": ["Du", "Motpart"]}, "due": _S}}),
        "score": {"type": "integer"}, "score_reason": _S,
        "strengths": _arr(_S), "improvements": _arr(_S), "next_step": _S,
        "follow_up_email": {"type": "object", "additionalProperties": False, "required": ["subject", "body"],
                            "properties": {"subject": _S, "body": _S}},
    },
}

SUMMARY_PROMPT = (
    "Du är en erfaren säljchef som går igenom ett avslutat säljsamtal åt en svensk entreprenör "
    "('Du' i transkriptionen). Den som ringdes kallas 'Motpart'. Transkriptionen är automatisk och kan "
    "innehålla hörfel och ibland fel talare: tolka rimligt, men hitta aldrig på något som inte sades.\n"
    "Skriv på naturlig, konkret svenska. Var ärlig: beröm det som var bra och säg rakt vad som kan bli bättre.\n"
    "- title: kort rubrik, t.ex. 'Patrik på LS Customs: intresse för pilot'.\n"
    "- summary: 2-4 meningar om vad samtalet handlade om och hur det slutade.\n"
    "- outcome och outcome_text: utfallet och en mening om varför.\n"
    "- they_said / you_said: det viktigaste var part sa (behov, fakta, löften). Högst 6 punkter var.\n"
    "- objections: motpartens invändningar, hur de bemöttes och ett bättre svar till nästa gång. Tom lista om inga.\n"
    "- todos: konkreta saker att göra efter samtalet, med ägare (Du eller Motpart) och när (tom sträng om inget sades).\n"
    "- score: 0-100 för hur väl entreprenören skötte samtalet mot målet. score_reason: en mening.\n"
    "- strengths / improvements: 2-4 punkter var, specifika med exempel ur samtalet.\n"
    "- next_step: det viktigaste nästa steget.\n"
    "- follow_up_email: kort uppföljningsmejl från entreprenören till motparten (ämne och brödtext, "
    "du-tilltal). Signera med entreprenörens namn om det finns, annars utan namn. Inga platshållare.\n"
)


def _clock(t: float) -> str:
    t = int(t or 0)
    return f"{t // 60:02d}:{t % 60:02d}"


def summarize_call(key: str, rec: dict) -> dict:
    """Analysera ett avslutat samtal med SUMMARY_MODEL. Returnerar en dict enligt SUMMARY_SCHEMA."""
    form = rec.get("form", {})
    ctx = (f"Entreprenören (Du): {form.get('me', '')}\nMotparten: {form.get('who', '')}\n"
           f"Mål med samtalet: {form.get('goal', '')}\nVad som säljs: {form.get('product', '')}\n"
           f"Pris: {form.get('price', '')}\nArgument: {form.get('args', '')}\nInvändningar/övrigt: {form.get('notes', '')}\n")
    lines = "\n".join(f"[{_clock(l.get('t'))}] {l['who']}: {l['text']}" for l in rec.get("transcript", []))
    client = OpenAI(api_key=key, timeout=180)
    base = dict(model=SUMMARY_MODEL, instructions=SUMMARY_PROMPT + "\nBakgrund:\n" + ctx,
                input=f"Transkription av samtalet:\n{lines}")
    try:
        r = client.responses.create(**base, text={"format": {
            "type": "json_schema", "name": "samtalsanalys", "schema": SUMMARY_SCHEMA, "strict": True}})
    except Exception as e:
        # Modellen kanske inte stöder strukturerat svar: be om JSON i texten i stället.
        if not any(w in str(e).lower() for w in ("format", "schema", "json")):
            raise
        log("summering: strukturerat svar stöds inte, försöker med fri JSON:", str(e)[:160])
        base["instructions"] += ("\nSvara ENDAST med ett JSON-objekt med exakt dessa fält: "
                                 + json.dumps(SUMMARY_SCHEMA["properties"], ensure_ascii=False))
        r = client.responses.create(**base)
    raw = r.output_text or ""
    data = json.loads(raw[raw.find("{"): raw.rfind("}") + 1])
    # Säkra upp fälten så att gränssnittet alltid får något rimligt.
    data["score"] = max(0, min(100, int(data.get("score") or 0)))
    for k in ("they_said", "you_said", "objections", "todos", "strengths", "improvements"):
        data[k] = data.get(k) or []
    for td in data["todos"]:
        td["done"] = False
    data.setdefault("follow_up_email", {"subject": "", "body": ""})
    data["model"] = SUMMARY_MODEL
    return data


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
        self.call: dict | None = None        # pågående samtal: transkription och förslag
        self._call_lock = threading.Lock()
        self._last_save = 0.0
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
        now = time.time()
        with self._call_lock:
            self.call = {"id": time.strftime("%Y%m%d-%H%M%S", time.localtime(now)), "t0": now,
                         "started": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)), "ended": None,
                         "mode": self.cfg["mode"], "form": dict(self.cfg["form"]),
                         "transcript": [], "suggestions": [], "summary": None, "summary_error": None}
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
        rec = self._finish_call()
        if rec is None:
            return {"id": None}
        threading.Thread(target=self._summarize, args=(rec,), daemon=True).start()
        return {"id": rec["id"]}

    # ---------- samtalslogg och analys
    def _record(self, who, text):
        """Lägg till en replik i samtalets transkription (samma talare i följd slås ihop)."""
        with self._call_lock:
            c = self.call
            if not c or not text:
                return
            tr = c["transcript"]
            if tr and tr[-1]["who"] == who:
                tr[-1]["text"] += " " + text
            else:
                tr.append({"who": who, "t": round(time.time() - c["t0"], 1), "text": text})
            if time.time() - self._last_save > 3:     # autospara, så inget går förlorat vid krasch
                self._last_save = time.time()
                try:
                    save_call(c)
                except Exception as e:
                    log("autospara fel:", repr(e))

    def _finish_call(self):
        """Avsluta pågående samtal och spara det. None om inget sades."""
        with self._call_lock:
            rec, self.call = self.call, None
        if not rec:
            return None
        rec["ended"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        rec["duration"] = round(time.time() - rec["t0"])
        if not rec["transcript"]:
            try:
                os.remove(os.path.join(CALLS_DIR, rec["id"] + ".json"))
            except OSError:
                pass
            return None
        save_call(rec)
        log("samtal sparat:", rec["id"], len(rec["transcript"]), "repliker")
        return rec

    def _summarize(self, rec):
        key = api_key(self.cfg)
        try:
            if not key:
                raise RuntimeError("Ingen OpenAI API-nyckel.")
            rec["summary"], rec["summary_error"] = summarize_call(key, rec), None
            log("summering klar:", rec["id"], "poäng", rec["summary"].get("score"))
        except Exception as e:
            rec["summary_error"] = str(e)[:400]
            log("summering fel:", rec["id"], repr(e)[:300])
        try:
            save_call(rec)
        except Exception as e:
            log("spara fel:", repr(e))
        self._js("summary", None, rec)

    def list_calls(self):
        out = []
        try:
            names = sorted((n for n in os.listdir(CALLS_DIR) if n.endswith(".json")), reverse=True)[:60]
        except OSError:
            names = []
        for n in names:
            rec = load_call(n[:-5])
            if not rec:
                continue
            s = rec.get("summary") or {}
            out.append({"id": rec["id"], "started": rec.get("started"), "duration": rec.get("duration"),
                        "who": rec.get("form", {}).get("who", ""), "title": s.get("title", ""),
                        "outcome": s.get("outcome", ""), "score": s.get("score"),
                        "lines": len(rec.get("transcript", []))})
        return out

    def get_call(self, call_id):
        return load_call(call_id)

    def retry_summary(self, call_id):
        rec = load_call(call_id)
        if not rec:
            return False
        threading.Thread(target=self._summarize, args=(rec,), daemon=True).start()
        return True

    def set_todo(self, call_id, idx, done):
        rec = load_call(call_id)
        try:
            rec["summary"]["todos"][int(idx)]["done"] = bool(done)
            save_call(rec)
            return True
        except Exception:
            return False

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
            self._finish_call()    # stängs appen mitt i ett samtal sparas det ändå (kan analyseras senare)
        except Exception as e:
            log("spara vid stängning fel:", repr(e))
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
            text, t_end, interim = data
            self._record(who, text)
            if self.coach:
                if text:
                    self.coach.add(who, text)
                # Coachen svarar först när motpartens hela replik är slut, inte på delbitar.
                if who == "Motpart" and not interim and not self.paused:
                    self.coach.suggest(t_end)
            if not text:
                return
            if interim:
                kind = "partial"    # visa texten, men börja inte "tänka" mitt i repliken
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
                with self._call_lock:
                    if self.call:
                        self.call["suggestions"].append(
                            {"t": round(time.time() - self.call["t0"], 1), "text": text.strip()})
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
    elif os.environ.get("SC_OPENCALL"):  # för designkontroll: öppna ett sparat samtals analys
        def _open():
            time.sleep(3)
            cid = json.dumps(os.environ["SC_OPENCALL"])
            api._ui_sync(lambda: api._win.evaluate_js(f"openSummary({cid}, false)"))
        webview.start(_open)
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
