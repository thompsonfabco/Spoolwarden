"""The station's own page and who may use it (R83 section 9 and 10.4; the re-ruling of 2026-11-17 21:22).

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

# What the page says (the string catalogue is EV2's: these keys and texts go to it in the hand-off). Every one is put on
# the page as text, never as markup.
TEXTS = {
    "station.page.title": "Weigh Station",
    "station.page.open_from_hub": "Open this page from the Hub: Devices, this Weigh Station, Open this device's page.",
    "station.page.session_over": "This session is over. Open this page from the Hub: Devices, this Weigh Station, "
                                 "Open this device's page.",
    "station.page.status": "Status",
    "station.page.settings": "Settings",
    "station.page.save": "Save",
    "station.page.reset": "Back to the installed values",
    "station.page.saved": "Saved.",
    "station.page.reset_done": "Back to the installed values.",
    "station.page.not_saved": "Not saved: {reason}",
    "station.page.tag": "Tag",
    "station.page.scale": "Scale",
    "station.page.last_weight": "Last weight",
    "station.page.panel": "Panel",
    "station.page.air": "Air",
    "station.page.step": "Intake step",
    "station.page.version": "Software version",
    "station.page.none": "none",
    "station.page.no_photo": "No photo.",
    "station.page.own_values": "These settings differ from the installed values.",
    "station.page.on": "On",
    "station.page.off": "Off",
    "station.page.unreachable": "The station did not answer.",
}


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
    return raw.replace("{{texts}}", block).replace("{{title}}", TEXTS["station.page.title"])


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
