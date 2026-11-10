"""Hysch-konto: inloggning mot Supabase, planer och samtalsgränser.

Appen har inga hemligheter. Adressen och den publika nyckeln nedan är gjorda för att ligga i appar: de ger ingen
åtkomst till något utan inloggning, eftersom databasen har radskydd (RLS) i varje tabell. OpenAI-nyckeln finns
bara på servern, och appen pratar med OpenAI via vår proxy med användarens inloggningstoken.
"""
from __future__ import annotations

import base64
import json
import os
import sys
import threading
import time
import webbrowser

try:                                    # openai 3.x levereras med httpx2; äldre versioner använder httpx
    import httpx2 as httpx
except ImportError:                      # pragma: no cover
    import httpx

SUPABASE_URL = "https://jlzzcqbhpxxxwvwxaqnc.supabase.co"
SUPABASE_KEY = "sb_publishable_CcTBuO8aDe3c7DzbRhdxcg_behZFjjS"
PROXY_BASE = SUPABASE_URL + "/functions/v1/openai-proxy/v1"

# Felkoder från Supabase Auth, på svenska. Okända koder får ett vänligt standardsvar.
AUTH_ERRORS = {
    "otp_expired": "Koden stämmer inte eller har gått ut. Be om en ny.",
    "validation_failed": "Kolla att e-posten är rätt skriven.",
    "email_address_invalid": "Kolla att e-posten är rätt skriven.",
    "over_request_rate_limit": "För många försök. Vänta en stund och försök igen.",
    "over_email_send_rate_limit": "Vänta en minut innan du ber om en ny kod.",
    "signup_disabled": "Det går inte att skapa konton just nu.",
}
LIMIT_TEXT = {
    "limit_reached": "Du har använt alla samtal i din plan den här månaden.",
    "subscription_inactive": "Din prenumeration är inte aktiv. Öppna Mitt konto för att förnya.",
    "not_logged_in": "Du är inte inloggad. Logga in igen.",
}


class AccountError(Exception):
    """Ett fel som är säkert att visa för användaren."""

    def __init__(self, message: str, code: str = ""):
        super().__init__(message)
        self.code = code


# ---------------------------------------------------------------- sparad inloggning
def _dpapi(data: bytes, protect: bool) -> bytes:
    """Windows DPAPI: krypterar åt den inloggade Windows-användaren. Ingen annan användare eller dator kan läsa det."""
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    src = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = Blob()
    fn = ctypes.windll.crypt32.CryptProtectData if protect else ctypes.windll.crypt32.CryptUnprotectData
    ok = fn(ctypes.byref(src), None, None, None, None, 0, ctypes.byref(out))
    if not ok:
        raise OSError("DPAPI misslyckades")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


class SessionStore:
    """Sparar bara förnyelsetoken, krypterad. Lösenordet sparas aldrig någonstans."""

    def __init__(self, directory: str):
        self.path = os.path.join(directory, "session.bin")
        self.dir = directory

    def save(self, data: dict) -> None:
        raw = json.dumps(data).encode("utf-8")
        blob = _dpapi(raw, True) if sys.platform == "win32" else raw
        os.makedirs(self.dir, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(base64.b64encode(blob))
        if sys.platform != "win32":
            os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def load(self) -> dict | None:
        try:
            with open(self.path, "rb") as f:
                blob = base64.b64decode(f.read())
            raw = _dpapi(blob, False) if sys.platform == "win32" else blob
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return None

    def clear(self) -> None:
        try:
            os.remove(self.path)
        except OSError:
            pass


# ---------------------------------------------------------------- konto
class Account:
    def __init__(self, directory: str, transport: httpx.BaseTransport | None = None, url: str = SUPABASE_URL,
                 key: str = SUPABASE_KEY):
        self.url, self.key = url.rstrip("/"), key
        self.store = SessionStore(directory)
        self.http = httpx.Client(timeout=15, transport=transport)
        self._lock = threading.Lock()
        self.access = ""
        self.expires_at = 0.0
        self.refresh_token = ""
        self.email = ""
        self.onboarded = False          # har kontot gjort uppstartsguiden? Sparas hos servern, så det följer med till en ny dator
        self.profile: dict | None = None

    # ----- grund
    @property
    def signed_in(self) -> bool:
        return bool(self.refresh_token)

    def _headers(self, token: str | None = None) -> dict:
        return {"apikey": self.key, "authorization": f"Bearer {token or self.key}", "content-type": "application/json"}

    @staticmethod
    def _error(r: httpx.Response, fallback: str = "Något gick fel. Försök igen.") -> AccountError:
        try:
            j = r.json()
        except Exception:
            j = {}
        code = str(j.get("error_code") or j.get("code") or "")
        return AccountError(AUTH_ERRORS.get(code, fallback), code)

    def _take_session(self, j: dict) -> None:
        self.access = j["access_token"]
        self.refresh_token = j["refresh_token"]
        self.expires_at = time.time() + float(j.get("expires_in", 3600))
        user = j.get("user") or {}
        self.email = user.get("email", self.email)
        meta = user.get("user_metadata") or {}
        self.onboarded = bool(meta.get("onboarded"))
        self.profile = meta["profile"] if isinstance(meta.get("profile"), dict) else None
        self._save_session()

    def _save_session(self) -> None:
        self.store.save({"refresh_token": self.refresh_token, "email": self.email,
                         "onboarded": self.onboarded, "profile": self.profile})

    # ----- inloggning med kod (inget lösenord)
    # Man skriver sin e-post, vi mejlar en sexsiffrig kod, och koden är det enda man behöver. Nya konton skapas av sig
    # självt första gången. Det finns alltså inget lösenord som kan läcka, återanvändas eller glömmas.
    @staticmethod
    def _clean_email(email: str) -> str:
        email = (email or "").strip().lower()
        if "@" not in email or "." not in email.split("@")[-1] or " " in email:
            raise AccountError("Skriv in en giltig e-post.", "bad_email")
        return email

    def send_code(self, email: str) -> dict:
        email = self._clean_email(email)
        try:
            r = self.http.post(f"{self.url}/auth/v1/otp", headers=self._headers(),
                               json={"email": email, "create_user": True})
        except httpx.HTTPError:
            raise AccountError("Ingen kontakt med servern. Kolla internet och försök igen.", "offline")
        if r.status_code != 200:
            raise self._error(r, "Kunde inte skicka koden. Försök igen.")
        return {"email": email}

    def verify_code(self, email: str, code: str) -> dict:
        email = self._clean_email(email)
        code = "".join(ch for ch in (code or "") if ch.isdigit())
        if len(code) != 6:
            raise AccountError("Koden har sex siffror.", "bad_code")
        try:
            r = self.http.post(f"{self.url}/auth/v1/verify", headers=self._headers(),
                               json={"type": "email", "email": email, "token": code})
        except httpx.HTTPError:
            raise AccountError("Ingen kontakt med servern. Kolla internet och försök igen.", "offline")
        if r.status_code != 200:
            raise self._error(r, "Koden stämmer inte eller har gått ut. Be om en ny.")
        self._take_session(r.json())
        return {"email": self.email, "onboarded": self.onboarded}

    def sync_profile(self, profile: dict) -> None:
        """Spara valen från uppstartsguiden på kontot och markera att guiden är gjord. Då slipper man guiden på nästa dator."""
        safe = {k: profile.get(k) for k in ("name", "company", "goals", "quality", "engine")}
        r = self._rest("PUT", "/auth/v1/user", json={"data": {"onboarded": True, "profile": safe}})
        if r.status_code != 200:
            raise AccountError("Kunde inte spara dina val på kontot.", "server")
        self.onboarded, self.profile = True, safe
        self._save_session()

    def sign_out(self) -> None:
        token = self.access
        self.access, self.refresh_token, self.email, self.expires_at = "", "", "", 0.0
        self.onboarded, self.profile = False, None
        self.store.clear()
        if token:
            try:
                self.http.post(f"{self.url}/auth/v1/logout", headers=self._headers(token))
            except httpx.HTTPError:
                pass

    def restore(self) -> bool:
        """Logga in tyst med den sparade förnyelsetoken. False om det inte finns någon eller den inte gäller längre."""
        saved = self.store.load()
        if not saved or not saved.get("refresh_token"):
            return False
        self.refresh_token, self.email = saved["refresh_token"], saved.get("email", "")
        self.onboarded, self.profile = bool(saved.get("onboarded")), saved.get("profile")
        try:
            self._refresh()
            return True
        except AccountError as e:
            if e.code == "offline":                    # ingen kontakt just nu: behåll inloggningen och försök senare
                return True
            self.sign_out()
            return False

    def _refresh(self) -> None:
        try:
            r = self.http.post(f"{self.url}/auth/v1/token", params={"grant_type": "refresh_token"},
                               headers=self._headers(), json={"refresh_token": self.refresh_token})
        except httpx.HTTPError:
            raise AccountError("Ingen kontakt med servern.", "offline")
        if r.status_code != 200:
            raise AccountError("Din inloggning har gått ut. Logga in igen.", "expired")
        self._take_session(r.json())

    def token(self, min_valid: float = 120) -> str:
        """Giltigt åtkomsttoken, förnyas vid behov. Trådsäkert, eftersom flera trådar pratar med servern."""
        with self._lock:
            if not self.refresh_token:
                raise AccountError("Du är inte inloggad.", "not_logged_in")
            if not self.access or self.expires_at - time.time() < min_valid:
                try:
                    self._refresh()
                except AccountError as e:
                    if e.code == "expired":
                        self.sign_out()
                    raise
            return self.access

    # ----- konto, gränser och samtal
    def _rest(self, method: str, path: str, **kw) -> httpx.Response:
        try:
            return self.http.request(method, f"{self.url}{path}", headers=self._headers(self.token()), **kw)
        except httpx.HTTPError:
            raise AccountError("Ingen kontakt med servern. Kolla internet och försök igen.", "offline")

    def overview(self) -> dict:
        """Plan, gränser och förbrukning för Mitt konto."""
        r = self._rest("GET", "/rest/v1/my_account", params={"select": "*"})
        if r.status_code != 200:
            raise AccountError("Kunde inte hämta ditt konto just nu.", "server")
        rows = r.json()
        if not rows:
            raise AccountError("Ditt konto är inte helt klart än. Försök igen om en stund.", "server")
        return rows[0]

    def start_call(self, mode: str) -> dict:
        """Servern avgör om samtalet får starta och räknar det. Returnerar {ok, call_id, ...} eller {ok: False, reason}."""
        r = self._rest("POST", "/rest/v1/rpc/start_call", json={"p_mode": mode})
        if r.status_code != 200:
            raise AccountError("Kunde inte starta samtalet. Försök igen.", "server")
        j = r.json()
        if not j.get("ok"):
            reason = j.get("reason", "")
            raise AccountError(LIMIT_TEXT.get(reason, "Samtalet fick inte starta."), reason)
        return j

    def end_call(self, call_id: str) -> None:
        try:
            self._rest("POST", "/rest/v1/rpc/end_call", json={"p_call_id": call_id})
        except AccountError:
            pass          # missar vi den stängs samtalet ändå av tidsgränsen på servern

    def _function(self, name: str, body: dict) -> str:
        try:
            r = self.http.post(f"{self.url}/functions/v1/{name}", headers=self._headers(self.token()), json=body, timeout=30)
        except httpx.HTTPError:
            raise AccountError("Ingen kontakt med servern. Kolla internet och försök igen.", "offline")
        j = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if r.status_code == 200 and j.get("url"):
            return j["url"]
        msgs = {
            "founding_sold_out": "Grundarplatserna är slut. Välj Pro.",
            "already_subscribed": "Du har redan en prenumeration. Öppna Hantera prenumeration.",
            "no_customer": "Du har ingen prenumeration att hantera än.",
        }
        raise AccountError(msgs.get(j.get("error", ""), "Kunde inte öppna betalsidan. Försök igen."), j.get("error", "server"))

    def open_checkout(self, plan: str) -> None:
        if plan not in ("founding", "pro"):
            raise AccountError("Okänd plan.")
        webbrowser.open(self._function("create-checkout", {"plan": plan}))

    def open_portal(self) -> None:
        webbrowser.open(self._function("customer-portal", {}))

    # ----- OpenAI via vår proxy
    def proxy_client(self, call_id: str, timeout: float = 30, transport=None):
        """OpenAI-klient som pratar med vår proxy i stället för med OpenAI. api_key är en funktion som SDK:n anropar
        före varje förfrågan, så ett långt samtal eller en sen analys aldrig får ett utgånget token."""
        from openai import OpenAI

        extra = {"http_client": httpx.Client(transport=transport, timeout=timeout)} if transport else {}
        base = self.url + "/functions/v1/openai-proxy/v1"
        return OpenAI(api_key=lambda: self.token(min_valid=60), base_url=base,
                      default_headers={"x-call-id": call_id}, timeout=timeout, max_retries=1, **extra)
