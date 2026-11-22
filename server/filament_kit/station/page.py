"""The station's own page and who may use it (the re-ruling of 2026-11-17 21:22).

The page's own files (`GET /`, `/static/*`: this module's `static/` folder) are served to any address on the home network;
they hold nothing but the page. Its data routes (`/state`, `/settings`, `/photo`, `/last-tag`) answer only the addresses on
the station's allow-list, or a browser holding the station's own session. That session comes from a ticket:

  the Hub, unlocked, asks `POST /ticket` (over its pinned call, with a proof that it holds the pairing) ->
  `{ticket, expires_in: 60}` and opens `<station address>#ticket=<ticket>`; the page hands the ticket to `POST /session`
  -> a cookie `fk_station_session`, valid 60 minutes, on the station's own origin.
  (Who may ask what is guard.py.)

A ticket works once, within 60 seconds. Tickets and sessions are kept in memory (a restart ends them). The proof is an HMAC
over a nonce and the time, keyed by a secret both sides derive from the pairing token without sending it: the station has
the token, the Hub keeps its SHA-256 (pairing.py `_hash`), and the key is HKDF(SHA-256(token)). The same proof guards a
settings change that comes from the Hub, with the body's hash in it.

All of the page's texts are constants here (TEXTS); the page uses relative addresses only, so it works at its own origin.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from pathlib import Path

STATIC = Path(__file__).with_name("static")
COOKIE = "fk_station_session"
TICKET_SECONDS = 60
SESSION_SECONDS = 3600
PROOF_SECONDS = 60          # how far a proof's time may be from the station's clock
MAX_TICKETS, MAX_SESSIONS, MAX_BODY = 8, 16, 4096
PROOF_HEADER = "X-Station-Proof"
_PROOF = re.compile(r"^(\d{1,12})\.([0-9a-f]{16,64})\.([0-9a-f]{64})$")
_TICKET = re.compile(r"^[A-Za-z0-9_-]{20,100}$")
INFO = b"spoolwarden-station-proof-v1"

_CATALOGUE: dict[str, str] | None = None


def _catalogue() -> dict[str, str]:
    """Load the generated string catalogue once, or {} when absent (standalone station)."""
    global _CATALOGUE
    if _CATALOGUE is None:
        p = Path(__file__).resolve().parent.parent / "shared_static" / "strings.js"
        try:
            raw = p.read_text(encoding="utf-8")
            start, end = raw.find("{"), raw.rfind("}")
            body = re.sub(r",\s*}$", "}", raw[start:end + 1]) if 0 <= start < end else "{}"
            data = json.loads(body)
            _CATALOGUE = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            _CATALOGUE = {}
    return _CATALOGUE


def catalogue_text(key: str, fallback: str, **values: str) -> str:
    """Resolve `key` from the string catalogue with {product}, {command}, and `values` substituted."""
    cat = _catalogue()
    raw = cat.get(key, fallback)
    if not isinstance(raw, str):
        raw = fallback
    subs = {"product": cat.get("app_name", "Spoolwarden"), "command": cat.get("command_name", "spoolwarden"), **values}
    return re.sub(r"\{(\w+)\}", lambda m: subs.get(m.group(1), m.group(0)), raw)


def say(key: str, /, **values) -> str:
    """The catalogue's text of `key` with {product}, {command} and `values` filled in. Unlike `catalogue_text` there is
    no fallback: a key the catalogue lacks is a KeyError (the Hub's own sentences, which always ship with it)."""
    raw = _catalogue().get(key)
    if not isinstance(raw, str):
        raise KeyError(key)
    return catalogue_text(key, raw, **{k: str(v) for k, v in values.items()})


# What the page says (all keys live in strings/en.json). Every one is put on the page as text, never as markup.
_PAGE_DEFAULTS = {
    "station_page_title": "Weigh Station",
    "station_page_open_from_hub": "Open this page from the Hub: Devices, this Weigh Station, Open this device's page.",
    "station_page_session_over": "This session is over. Open this page from the Hub: Devices, this Weigh Station, "
                                 "Open this device's page.",
    "station_page_status": "Status",
    "station_page_settings": "Settings",
    "station_page_page_from": "Page access: {mode}",
    "station_page_page_from_subnets_note": "If this machine's network has changed, run: sudo {command} features apply",
    "station_page_save": "Save",
    "station_page_reset": "Back to the installed values",
    "station_page_saved": "Saved.",
    "station_page_reset_done": "Back to the installed values.",
    "station_page_not_saved": "Not saved: {reason}",
    "station_page_tag": "Tag",
    "station_page_scale": "Scale",
    "station_page_last_weight": "Last weight",
    "station_page_panel": "Panel",
    "station_page_air": "Air",
    "station_page_step": "Step",
    "station_page_version": "Software version",
    "station_page_none": "none",
    "station_page_own_values": "These settings differ from the installed values.",
    "station_page_unreachable": "The Weigh Station did not answer.",
    # the status rows' values (station.js `tx`, `word`): a value with its age, units, and the words for station ids
    "station_page_age": "{value} ({seconds} s ago)",
    "station_page_grams": "{grams} g",
    "station_page_scale_value": "{grams} g, {status}",
    "station_page_panel_firmware": "{panel} (firmware {version})",
    "station_page_air_value": "{temp} °C, {rh} %",
    "station_page_step_message": "{step}: {message}",
    "station_page_mode_home": "Your home network",
    "station_page_mode_subnets": "This machine's own networks",
    "station_page_mode_allowed": "The allow-list only",
    "station_step_pairing": "Asking the Hub to pair",
    "station_step_idle": "Ready",
    "station_step_lookup": "Looking up a tag",
    "station_step_camera": "Show the spool to the camera",
    "station_step_recognising": "Recognising the spool",
    "station_step_weigh": "Put it on the scale",
    "station_step_ready": "Weight taken",
    "station_step_saving": "Saving",
    "station_step_done": "Saved",
    "station_step_phone": "Use the phone",
    "station_scale_fault": "Fault",
    "station_scale_zero": "At zero",
    "station_scale_moving": "Settling",
    "station_scale_stable": "Steady",
    "station_scale_negative": "Below zero",
    "station_scale_overload": "Too heavy for the scale",
    "station_scale_calibrate": "Needs calibrating",
    "station_scale_re_zero": "Needs zeroing",
    "station_scale_unit": "Unknown unit",
}
TEXTS = {k: catalogue_text(k, v) for k, v in _PAGE_DEFAULTS.items()}


# ---- the proof the Hub gives ------------------------------------------------------------------------------------------

def _hkdf(ikm: bytes, info: bytes, length: int = 32) -> bytes:
    """HKDF-SHA256 (RFC 5869) with an empty salt, written out (no library needed on a station)."""
    prk = hmac.new(b"\0" * 32, ikm, hashlib.sha256).digest()
    out, block = b"", b""
    for i in range(1, -(-length // 32) + 1):
        block = hmac.new(prk, block + info + bytes([i]), hashlib.sha256).digest()
        out += block
    return out[:length]


def proof_key(token_hash: str) -> bytes:
    """The key of the proofs, from the SHA-256 (lower-case hex) of the station's pairing token: the Hub reads it from the
    station's pairing record; the station computes it from its token (`token_key`)."""
    return _hkdf(bytes.fromhex(token_hash), INFO)


def token_key(token: str) -> bytes:
    return proof_key(hashlib.sha256(token.encode("utf-8")).hexdigest())


def make_proof(key: bytes, purpose: str, body: bytes = b"", now: float | None = None, nonce: str | None = None) -> str:
    """The value of the `X-Station-Proof` header: `<unix seconds>.<nonce>.<hmac>` over the purpose ("ticket" or
    "settings"), the time, the nonce and the body's SHA-256."""
    ts = str(int(time.time() if now is None else now))
    nonce = nonce or secrets.token_hex(16)
    msg = f"{purpose}\n{ts}\n{nonce}\n{hashlib.sha256(body).hexdigest()}".encode()
    return f"{ts}.{nonce}." + hmac.new(key, msg, hashlib.sha256).hexdigest()


class Access:
    """Proofs, tickets and sessions of one station process. `key_of()` gives the proof key (bytes) or None when the station
    is not paired (then no proof is good)."""

    def __init__(self, key_of, clock=time.time):
        self.key_of, self.clock = key_of, clock
        self._lock = threading.Lock()
        self._nonces: dict[str, float] = {}
        self._tickets: dict[str, float] = {}
        self._sessions: dict[str, float] = {}

    def proof_ok(self, purpose: str, header: str | None, body: bytes = b"") -> bool:
        m = _PROOF.match(header or "")
        key = self.key_of()
        if not m or not key:
            return False
        ts, nonce, got = m.groups()
        now = self.clock()
        if abs(now - int(ts)) > PROOF_SECONDS:
            return False
        msg = f"{purpose}\n{ts}\n{nonce}\n{hashlib.sha256(body).hexdigest()}".encode()
        if not hmac.compare_digest(hmac.new(key, msg, hashlib.sha256).hexdigest(), got):
            return False
        with self._lock:        # a proof is spent: the same one again is a replay
            self._nonces = {n: t for n, t in self._nonces.items() if t > now}
            if nonce in self._nonces:
                return False
            self._nonces[nonce] = now + 2 * PROOF_SECONDS
        return True

    def new_ticket(self) -> str | None:
        """A ticket good for TICKET_SECONDS and one use; None while MAX_TICKETS are waiting."""
        now = self.clock()
        with self._lock:
            self._tickets = {t: e for t, e in self._tickets.items() if e > now}
            if len(self._tickets) >= MAX_TICKETS:
                return None
            ticket = secrets.token_urlsafe(32)
            self._tickets[ticket] = now + TICKET_SECONDS
            return ticket

    def open_session(self, ticket) -> str | None:
        """Spend `ticket` and start a session: its id (the cookie's value), or None for a ticket that is unknown, used or late."""
        if not isinstance(ticket, str) or not _TICKET.match(ticket):
            return None
        now = self.clock()
        with self._lock:
            expires = self._tickets.pop(ticket, 0)
            if expires <= now:
                return None
            self._sessions = {s: e for s, e in self._sessions.items() if e > now}
            if len(self._sessions) >= MAX_SESSIONS:
                self._sessions.pop(min(self._sessions, key=self._sessions.get))
            sid = secrets.token_urlsafe(32)
            self._sessions[sid] = now + SESSION_SECONDS
            return sid

    def session_ok(self, cookie_header: str | None) -> bool:
        for part in (cookie_header or "").split(";"):
            name, _, value = part.strip().partition("=")
            if name == COOKIE and value:
                with self._lock:
                    expires = self._sessions.get(value, 0)
                return expires > self.clock()
        return False


def cookie_header(session_id: str) -> str:
    return f"{COOKIE}={session_id}; Path=/; Max-Age={SESSION_SECONDS}; HttpOnly; Secure; SameSite=Strict"


# ---- the page's files -------------------------------------------------------------------------------------------------

TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}
FILES = {"/": "index.html", "/static/station.js": "station.js", "/static/station.css": "station.css"}


def render_index(raw: str) -> str:
    """index.html with the page's texts in a data block its script reads (type application/json: it is not run, so the
    page's content security policy, which allows only its own files, holds)."""
    block = json.dumps(TEXTS, ensure_ascii=True).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return raw.replace("{{texts}}", block).replace("{{title}}", TEXTS["station_page_title"])


def read_file(path: str) -> tuple[bytes, str] | None:
    """(bytes, content type) of one of the page's files, else None. Only the three names above: no path is built from the
    request."""
    name = FILES.get(path)
    if name is None:
        return None
    try:
        raw = (STATIC / name).read_text(encoding="utf-8")
    except OSError:
        return None
    if name == "index.html":
        raw = render_index(raw)
    return raw.encode("utf-8"), TYPES[Path(name).suffix]


SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; "
                               "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}
