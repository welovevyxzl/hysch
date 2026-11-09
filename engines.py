"""Motorer som kan köras lokalt: modellnedladdning, Whisper (faster-whisper) och Ollama.

Inget här är knutet till Windows, så samma kod fungerar på Mac. Ljudinspelning och fönster
ligger kvar i samtalscoach.py.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import numpy as np

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
OLLAMA_URL = "http://127.0.0.1:11434"
NUM_CTX = 8192          # kontextfönster för chatt och uppvärmning (måste vara lika, se Ollama.warm)
UA = {"User-Agent": "Hysch"}


def log(*a):
    """Loggas av appen (samtalscoach sätter in sin egen logg)."""
    try:
        _log(*a)
    except Exception:
        pass


def _log(*a):
    pass


def set_logger(fn):
    global _log
    _log = fn


# ---------------------------------------------------------------- dator
def system_info(path: str | None = None) -> dict:
    """RAM, kärnor, ledigt diskutrymme och om en NVIDIA-GPU kan användas av Whisper."""
    ram = 0.0
    try:
        if IS_WIN:
            import ctypes

            class MEM(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                            ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                            ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                            ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            m = MEM()
            m.dwLength = ctypes.sizeof(MEM)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
            ram = m.ullTotalPhys / 1024 ** 3
        else:
            ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1024 ** 3
    except Exception:
        pass
    free = 0.0
    try:
        p = path or os.path.expanduser("~")
        while p and not os.path.exists(p):
            p = os.path.dirname(p)
        free = shutil.disk_usage(p or os.path.expanduser("~")).free / 1024 ** 3
    except Exception:
        pass
    return {"os": "windows" if IS_WIN else "mac" if IS_MAC else "linux",
            "ram_gb": round(ram, 1), "cores": os.cpu_count() or 1, "free_gb": round(free, 1),
            "cuda": cuda_usable(), "gpu_seen": cuda_available()}


def cuda_available() -> bool:
    """Finns ett NVIDIA-kort som CTranslate2 ser? (Säger inget om att biblioteken som krävs finns, se cuda_usable.)"""
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def cuda_usable() -> bool:
    """Kortet fungerar på riktigt för Whisper: NVIDIA-kort OCH CUDA-biblioteken (cuBLAS 12) går att ladda."""
    if not cuda_available():
        return False
    try:
        import ctypes
        if IS_WIN:
            ctypes.WinDLL("cublas64_12.dll")
        elif not IS_MAC:
            ctypes.CDLL("libcublas.so.12")
        else:
            return False
        return True
    except OSError:
        return False


# ---------------------------------------------------------------- modeller (förval)
# Svenska Whisper-modeller från KBLab (Apache 2.0). De har faster-whisper-filerna i rotmappen.
STT_PRESETS = [
    {"id": "kb-small", "repo": "KBLab/kb-whisper-small", "name": "Whisper Small (svensk)", "tier": "fast",
     "mb": 466, "min_ram": 4, "beam": 1},
    {"id": "kb-medium", "repo": "KBLab/kb-whisper-medium", "name": "Whisper Medium (svensk)", "tier": "balanced",
     "mb": 1462, "min_ram": 8, "beam": 3},
    {"id": "kb-large", "repo": "KBLab/kb-whisper-large", "name": "Whisper Large (svensk)", "tier": "best",
     "mb": 2949, "min_ram": 12, "beam": 5},
]
# Textmodeller via Ollama (taggarna finns i Ollamas bibliotek).
LLM_PRESETS = [
    {"tag": "gemma3:4b", "name": "Gemma 3 4B", "tier": "fast", "mb": 3200, "min_ram": 8},
    {"tag": "gemma3:12b", "name": "Gemma 3 12B", "tier": "balanced", "mb": 7800, "min_ram": 16},
    {"tag": "gemma3:27b", "name": "Gemma 3 27B", "tier": "best", "mb": 16600, "min_ram": 32},
]


def stt_preset(pid: str) -> dict | None:
    return next((p for p in STT_PRESETS if p["id"] == pid), None)


def preset_for_tier(presets: list, tier: str) -> dict:
    return next((p for p in presets if p["tier"] == tier), presets[1])


# ---------------------------------------------------------------- nedladdning
class Cancelled(Exception):
    pass


WHISPER_FILES = ("config.json", "model.bin", "tokenizer.json", "vocabulary.json", "vocabulary.txt",
                 "preprocessor_config.json")
OK_MARK = ".viska_ok"


def whisper_dir(models_root: str, preset_id: str) -> str:
    return os.path.join(models_root, "whisper", preset_id)


def whisper_installed(path: str) -> bool:
    return os.path.isfile(os.path.join(path, "model.bin")) and os.path.isfile(os.path.join(path, OK_MARK))


def _open(url: str, headers: dict | None = None, timeout: float = 30):
    req = urllib.request.Request(url, headers={**UA, **(headers or {})})
    return urllib.request.urlopen(req, timeout=timeout)


def _download_file(url: str, path: str, size: int, on_bytes, cancel: threading.Event | None, retries: int = 4):
    """Hämta en fil med återupptagning (Range) och några försök. on_bytes(hämtat_i_filen)."""
    if os.path.isfile(path) and (not size or os.path.getsize(path) == size):
        on_bytes(os.path.getsize(path))
        return
    part = path + ".part"
    last: Exception | None = None
    for attempt in range(retries):
        try:
            have = os.path.getsize(part) if os.path.isfile(part) else 0
            if size and have == size:
                os.replace(part, path)
                on_bytes(size)
                return
            headers = {"Range": f"bytes={have}-"} if have else {}
            with _open(url, headers, timeout=45) as r:
                resumed = have and r.status == 206
                got = have if resumed else 0
                with open(part, "ab" if resumed else "wb") as f:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise Cancelled()
                        chunk = r.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        got += len(chunk)
                        on_bytes(got)
            if size and os.path.getsize(part) != size:
                raise IOError(f"fel storlek på {os.path.basename(path)}: {os.path.getsize(part)} av {size}")
            os.replace(part, path)
            return
        except Cancelled:
            raise
        except Exception as e:      # nätverksfel: vänta och försök igen, fortsätt där vi var
            last = e
            log("nedladdning, försök", attempt + 1, "misslyckades:", repr(e)[:160])
            time.sleep(1 + attempt * 2)
    raise last or IOError("nedladdningen misslyckades")


def download_whisper(repo: str, dest: str, progress=None, cancel: threading.Event | None = None,
                     base: str = "https://huggingface.co"):
    """Hämta en faster-whisper-modell till dest. progress(hämtat_byte, totalt_byte, filnamn)."""
    os.makedirs(dest, exist_ok=True)
    with _open(f"{base}/api/models/{repo}?blobs=true") as r:
        info = json.load(r)
    files = [(s["rfilename"], int(s.get("size") or 0)) for s in info.get("siblings", [])
             if s["rfilename"] in WHISPER_FILES]
    if not any(n == "model.bin" for n, _ in files):
        raise IOError(f"{repo} har ingen model.bin (fel format)")
    total = sum(sz for _, sz in files)
    base_done = 0
    last_report = [0.0]

    def make_cb(name, offset):
        def cb(got):
            now = time.perf_counter()
            if progress and now - last_report[0] > 0.15:
                last_report[0] = now
                progress(offset + got, total, name)
        return cb

    for name, size in files:
        _download_file(f"{base}/{repo}/resolve/main/{name}", os.path.join(dest, name), size, make_cb(name, base_done), cancel)
        base_done += size
        if progress:
            progress(base_done, total, name)
    with open(os.path.join(dest, OK_MARK), "w", encoding="utf-8") as f:
        json.dump({"repo": repo, "files": [n for n, _ in files], "ts": int(time.time())}, f)


# ---------------------------------------------------------------- Whisper (lokal tal-till-text)
_FIR: np.ndarray | None = None


def to_16k(x: np.ndarray) -> np.ndarray:
    """24 kHz -> 16 kHz (upp 2, lågpass, ner 3). Whisper vill ha 16 kHz."""
    global _FIR
    if _FIR is None:
        taps = np.arange(-48, 49)
        h = np.sinc(2 * (7600 / 48000) * taps) * np.kaiser(len(taps), 8.0)
        _FIR = (h / h.sum() * 2).astype(np.float32)
    up = np.zeros(len(x) * 2, dtype=np.float32)
    up[::2] = x
    return np.convolve(up, _FIR, mode="same")[::3].astype(np.float32)


# Texter som Whisper hittar på i tystnad (vanligt på svenska). Ska aldrig visas som tal.
_HALLU = ("textning", "undertexter", "tack för att ni tittade", "tack för att du tittade", "amara.org",
          "svt textning", "teksting", "thanks for watching", "prenumerera")


_HALLU_EXACT = {"musik", "(musik)", "[musik]", "♪", "♪♪", "musik spelas"}    # hela texten, inte delar av en mening


def is_hallucination(text: str) -> bool:
    t = text.lower().strip(" .!?-")
    return not t or t in _HALLU_EXACT or any(h in t for h in _HALLU)


class LocalWhisper:
    """faster-whisper (CTranslate2) på CPU med int8, eller på NVIDIA-GPU om det fungerar."""

    name = "local"

    def __init__(self, model_dir: str, beam: int = 1, language: str = "sv"):
        self.model_dir, self.beam, self.language = model_dir, beam, language
        self.model = None
        self.device = ""
        self.lock = threading.Lock()

    def load(self, force_cpu: bool = False):
        from faster_whisper import WhisperModel
        tries = [("cpu", "int8")]
        if not force_cpu and cuda_usable():
            tries.insert(0, ("cuda", "float16"))
        last = None
        for device, ctype in tries:
            try:
                t0 = time.perf_counter()
                self.model = WhisperModel(self.model_dir, device=device, compute_type=ctype)
                self.device = device
                log(f"Whisper laddad ({device}/{ctype}) på {time.perf_counter() - t0:.1f} s:", self.model_dir)
                return
            except Exception as e:
                last = e
                log(f"Whisper gick inte på {device}:", repr(e)[:200])
        raise RuntimeError(f"Kunde inte ladda Whisper: {last}")

    def warm(self):
        with self.lock:
            if self.model is None:
                self.load()

    def _run(self, x16: np.ndarray, prompt: str, language: str | None) -> str:
        segs, _ = self.model.transcribe(
            x16, language=language or self.language, beam_size=self.beam, initial_prompt=prompt or None,
            condition_on_previous_text=False, temperature=[0.0, 0.2, 0.4], without_timestamps=True,
            vad_filter=False, no_speech_threshold=0.6)
        return " ".join(s.text.strip() for s in segs).strip()

    def transcribe(self, audio: np.ndarray, prompt: str = "", language: str | None = None) -> str:
        """audio: float32, 24 kHz mono, skala som 16-bitars heltal (-32768..32767)."""
        x16 = to_16k(audio) / 32768.0
        with self.lock:
            if self.model is None:
                self.load()
            try:
                return self._run(x16, prompt, language)
            except Exception as e:
                if self.device != "cuda":
                    raise
                # GPU-biblioteken saknas eller strular: byt till CPU och försök igen.
                log("Whisper på GPU misslyckades, byter till CPU:", repr(e)[:200])
                self.model = None
                self.load(force_cpu=True)
                return self._run(x16, prompt, language)


_WHISPERS: dict[str, LocalWhisper] = {}


def get_whisper(model_dir: str, beam: int = 1) -> LocalWhisper:
    """En delad instans per modell, så att den bara laddas en gång även med två ljudkällor."""
    w = _WHISPERS.get(model_dir)
    if w is None or w.beam != beam:
        w = _WHISPERS[model_dir] = LocalWhisper(model_dir, beam)
    return w


# ---------------------------------------------------------------- Ollama (lokal textmodell)
THINK_RE = re.compile(r"<think>.*?</think>", re.S)


class Ollama:
    def __init__(self, base: str = OLLAMA_URL):
        self.base = base.rstrip("/")

    # --- hitta och starta
    @staticmethod
    def binary() -> str | None:
        found = shutil.which("ollama")
        if found:
            return found
        cands = []
        if IS_WIN:
            cands += [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Ollama", "ollama.exe"),
                      r"C:\Program Files\Ollama\ollama.exe"]
        elif IS_MAC:
            cands += ["/Applications/Ollama.app/Contents/Resources/ollama", "/usr/local/bin/ollama",
                      "/opt/homebrew/bin/ollama"]
        else:
            cands += ["/usr/local/bin/ollama", "/usr/bin/ollama"]
        return next((c for c in cands if c and os.path.isfile(c)), None)

    def running(self, timeout: float = 1.5) -> bool:
        try:
            with _open(self.base + "/api/version", timeout=timeout):
                return True
        except Exception:
            return False

    def start(self, wait: float = 25.0) -> bool:
        """Starta Ollama-servern om den inte redan går. True när den svarar."""
        if self.running():
            return True
        exe = self.binary()
        if not exe:
            return False
        try:
            if IS_MAC and os.path.isdir("/Applications/Ollama.app"):
                subprocess.Popen(["open", "-a", "Ollama"])
            elif IS_WIN:
                subprocess.Popen([exe, "serve"], creationflags=0x08000000 | 0x00000008,   # NO_WINDOW | DETACHED
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
            else:
                subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
        except Exception as e:
            log("kunde inte starta Ollama:", repr(e)[:200])
            return False
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < wait:
            if self.running():
                return True
            time.sleep(0.4)
        return False

    # --- modeller
    def models(self) -> list[dict]:
        with _open(self.base + "/api/tags", timeout=10) as r:
            data = json.load(r)
        return [{"name": m["name"], "mb": round(m.get("size", 0) / 1024 ** 2)} for m in data.get("models", [])]

    def has(self, tag: str) -> bool:
        try:
            names = {m["name"] for m in self.models()}
        except Exception:
            return False
        return tag in names or (":" not in tag and tag + ":latest" in names)

    def pull(self, tag: str, progress=None, cancel: threading.Event | None = None):
        """Ladda ner en modell. progress(hämtat_byte, totalt_byte, status)."""
        body = json.dumps({"model": tag, "stream": True}).encode()
        req = urllib.request.Request(self.base + "/api/pull", data=body, headers={**UA, "Content-Type": "application/json"})
        layers: dict[str, list[int]] = {}
        with urllib.request.urlopen(req, timeout=60) as r:
            for line in r:
                if cancel is not None and cancel.is_set():
                    raise Cancelled()
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("error"):
                    raise RuntimeError(ev["error"])
                if ev.get("digest") and ev.get("total"):
                    layers[ev["digest"]] = [int(ev.get("completed") or 0), int(ev["total"])]
                done = sum(v[0] for v in layers.values())
                total = sum(v[1] for v in layers.values())
                if progress:
                    progress(done, total, ev.get("status", ""))

    # --- chatt
    def _body(self, model, system, user, max_tokens, temperature, stream, think, fmt):
        b = {"model": model, "stream": stream, "keep_alive": "30m",
             "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
             "options": {"num_predict": max_tokens, "temperature": temperature, "num_ctx": NUM_CTX}}
        if think is not None:
            b["think"] = think
        if fmt is not None:
            b["format"] = fmt
        return b

    def _post(self, body: dict, timeout: float):
        req = urllib.request.Request(self.base + "/api/chat", data=json.dumps(body).encode(),
                                     headers={**UA, "Content-Type": "application/json"})
        return urllib.request.urlopen(req, timeout=timeout)

    def _open_chat(self, body: dict, timeout: float):
        try:
            return self._post(body, timeout)
        except urllib.error.HTTPError as e:
            msg = e.read().decode("utf-8", "ignore")
            if "think" in msg.lower() and "think" in body:
                body = {k: v for k, v in body.items() if k != "think"}     # modellen kan inte "tänka": utan
                return self._post(body, timeout)
            raise RuntimeError(f"Ollama: {msg[:300] or e}") from None

    def stream(self, model: str, system: str, user: str, max_tokens: int = 160, temperature: float = 0.4):
        """Strömma svaret ord för ord."""
        r = self._open_chat(self._body(model, system, user, max_tokens, temperature, True, False, None), 60)
        raw, out = "", ""
        with r:
            for line in r:
                if not line.strip():
                    continue
                ev = json.loads(line)
                if ev.get("error"):
                    raise RuntimeError(ev["error"])
                raw += (ev.get("message") or {}).get("content", "")
                # Ta bort eventuella <think>-block (vissa modeller skriver dem i texten).
                clean = THINK_RE.sub("", raw)
                if "<think>" in clean:
                    clean = clean.split("<think>")[0]
                if len(clean) > len(out):
                    yield clean[len(out):]
                    out = clean

    def complete(self, model: str, system: str, user: str, max_tokens: int = 2500, temperature: float = 0.2,
                 schema: dict | None = None) -> str:
        r = self._open_chat(self._body(model, system, user, max_tokens, temperature, False, False, schema), 300)
        with r:
            data = json.load(r)
        if data.get("error"):
            raise RuntimeError(data["error"])
        return THINK_RE.sub("", (data.get("message") or {}).get("content", "")).strip()

    def warm(self, model: str):
        """Ladda modellen i minnet i förväg, så att första förslaget inte väntar på det.
        Samma num_ctx som i chatten: annars laddar Ollama om modellen vid första frågan (3-6 s extra)."""
        body = {"model": model, "keep_alive": "30m", "prompt": "", "stream": False, "options": {"num_ctx": NUM_CTX}}
        req = urllib.request.Request(self.base + "/api/generate", data=json.dumps(body).encode(),
                                     headers={**UA, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=300):
            pass


# ---------------------------------------------------------------- strukturerat svar från lokala modeller
# Ollama håller sig inte alltid till JSON-schemat (modellen kan hitta på egna nycklar eller lägga
# markdown runt), så svaret tolkas, valideras och rättas här i stället för att lita på `format`.
def _norm_key(k: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(k)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


def schema_skeleton(schema: dict):
    """Ett exempel på formen, att visa för modellen."""
    t = schema.get("type")
    if "enum" in schema:
        return " | ".join(schema["enum"])
    if t == "object":
        return {k: schema_skeleton(v) for k, v in schema.get("properties", {}).items()}
    if t == "array":
        return [schema_skeleton(schema.get("items", {"type": "string"}))]
    if t == "integer":
        return 0
    return "..."


def parse_json(raw: str):
    """Plocka ut ett JSON-objekt ur modellens text (tål ```json-staket och inledande prat)."""
    s = THINK_RE.sub("", raw or "").strip()
    a, b = s.find("{"), s.rfind("}")
    if a < 0 or b <= a:
        raise ValueError("ingen JSON i svaret")
    return json.loads(s[a:b + 1])


def _default(schema: dict):
    if "enum" in schema:
        e = schema["enum"]
        return "Oklart" if "Oklart" in e else "Neutral" if "Neutral" in e else e[0]
    t = schema.get("type")
    return {} if t == "object" and not schema.get("properties") else \
        {k: _default(v) for k, v in schema.get("properties", {}).items()} if t == "object" else \
        [] if t == "array" else 0 if t == "integer" else ""


def coerce(data, schema: dict):
    """Gör om svaret till schemats form: rätt nycklar (även utan accenter), rätt typer, giltiga enum-värden."""
    t = schema.get("type")
    if "enum" in schema:
        v = str(data).strip() if data is not None else ""
        hit = next((e for e in schema["enum"] if _norm_key(e) == _norm_key(v)), None)
        return hit or _default(schema)
    if t == "object":
        props = schema.get("properties", {})
        src = {_norm_key(k): v for k, v in data.items()} if isinstance(data, dict) else {}
        return {k: (coerce(src[_norm_key(k)], sub) if _norm_key(k) in src else _default(sub)) for k, sub in props.items()}
    if t == "array":
        if isinstance(data, str):
            data = [data]
        if not isinstance(data, list):
            return []
        return [coerce(x, schema.get("items", {"type": "string"})) for x in data]
    if t == "integer":
        try:
            return int(float(str(data).strip().split("/")[0]))
        except Exception:
            return 0
    return "" if data is None else (data if isinstance(data, str) else json.dumps(data, ensure_ascii=False) if isinstance(data, (dict, list)) else str(data))


def schema_problems(data, schema: dict) -> list[str]:
    """Vad som är fel på toppnivån (saknade eller felnamngivna nycklar). Tom lista = bra."""
    if not isinstance(data, dict):
        return ["svaret är inget JSON-objekt"]
    have = {_norm_key(k) for k in data}
    exact = set(data)
    out = []
    for k in schema.get("properties", {}):
        if k not in exact:
            out.append(f"nyckeln '{k}' saknas" + (" (du skrev den utan rätt stavning)" if _norm_key(k) in have else ""))
    return out


class LocalLLM:
    """Textmodell via Ollama: strömmade förslag och analys efter samtalet."""

    kind = "local"

    def __init__(self, ollama: Ollama, model: str):
        self.ol, self.model = ollama, model

    def warm(self):
        self.ol.warm(self.model)

    def stream(self, instructions: str, text: str, max_tokens: int = 160):
        yield from self.ol.stream(self.model, instructions, text, max_tokens)

    def analyze(self, instructions: str, text: str, schema: dict) -> dict:
        spec = json.dumps(schema_skeleton(schema), ensure_ascii=False, indent=1)
        system = (instructions + "\nSvara ENDAST med ett JSON-objekt, utan markdown och utan förklaringar, med EXAKT "
                  "de här nycklarna (samma stavning, inga andra):\n" + spec)
        data, problems = None, []
        for attempt in range(2):
            extra = "" if not problems else "\nDITT FÖRRA SVAR VAR FEL: " + "; ".join(problems[:6]) + ". Rätta det."
            raw = self.ol.complete(self.model, system + extra, text, 3500, 0.2)
            try:
                data = parse_json(raw)
                problems = schema_problems(data, schema)
            except Exception as e:
                data, problems = None, [f"svaret kunde inte tolkas som JSON ({e})"]
            if not problems:
                break
            log("lokal analys, försök", attempt + 1, "behöver rättas:", "; ".join(problems[:4]))
        if data is None:
            raise RuntimeError("Textmodellen gav inget läsbart svar. Prova en större modell i inställningarna.")
        return coerce(data, schema)
