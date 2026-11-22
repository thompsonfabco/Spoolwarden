"""The station's HTTP handler: GET /state, GET /last-tag and GET /photo (shapes in the package docstring; the first
two keep their shape from filament-ops so filament-mcp keeps reading them), its own page (`GET /`, `/static/*`), its
settings (`GET` and `POST /settings`), the voice (`POST /voice`, `POST /voice/test`: the Hub's call) and the ticket and session routes of the page (`POST /ticket`, `POST /session`:
page.py). 404 for anything else, 403 for a client not in `allow` and holding no session."""
from __future__ import annotations

import datetime
import json
import ssl
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def iso(t):
    return datetime.datetime.fromtimestamp(t).astimezone().isoformat(timespec="seconds") if t else None


def age(t):
    return round(time.time() - t) if t else None


class TlsServer(ThreadingHTTPServer):
    """The station's listener: HTTPS only (R82 section 2: every listener another machine can reach). One thread per
    caller; the TLS handshake happens in that thread (do_handshake_on_connect=False), so a caller that stalls in it
    holds no one else up."""
    daemon_threads = True

    def __init__(self, address, handler, context: ssl.SSLContext):
        self.context = context
        super().__init__(address, handler)

    def get_request(self):
        sock, addr = self.socket.accept()
        return self.context.wrap_socket(sock, server_side=True, do_handshake_on_connect=False), addr

    def handle_error(self, request, client_address):
        pass  # a failed handshake (a plain-HTTP caller, a scanner) or a dropped connection: nothing to log


def tls_context(cert, key) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(cert, key)
    return ctx


def make_handler(state_view: Callable[[], dict], allow: set[str], access=None, store=None,
                 wrong_tickets=None, *, page_ranges: str | None = None,
                 page_from: str = "home") -> type[BaseHTTPRequestHandler]:
    """A handler class over `state_view()`: a snapshot of the station state plus "panel" (the panel's port); "photo" is the JPEG bytes.

    Every request is guarded by the caller's address and cookie (guard.py, R83 01:10): an address on `allow` (the Hub) asks
    everything, as before, and `POST /ticket` with its proof; another home address gets the page's own files and `POST
    /session` and, with the station's session (page.py), `/state`, `/settings` and `/photo`; the rest is 403
    `{error, message}`. `access` is the page.Access (proofs, tickets, sessions), `store` the settings.Store (with none the
    settings routes are not there) and `wrong_tickets` the guard.WrongTickets table of `POST /session`."""
    from .. import __version__
    from . import guard, page
    access = access or page.Access(lambda: None)
    wrong_tickets = wrong_tickets or guard.WrongTickets()
    listed = {guard.peer_address(address) for address in allow}
    networks = guard.page_networks(page_ranges)
    if page_from not in ("home", "subnets", "allowed"):
        page_from = "home"

    class Handler(BaseHTTPRequestHandler):
        server_version = "intake-station"
        sys_version = ""
        timeout = 30    # a caller that stops talking (or never finishes the TLS handshake) frees its thread

        def _send(self, code, body, headers=None):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _error(self, code, error, message=None):
            """A refusal: `{"error": <code word>, "message": <sentence>}`."""
            self._send(code, {"error": error, "message": message or guard.TEXTS[error]})

        def _caller(self) -> str:
            return guard.caller_class(self.client_address[0], listed, networks)

        def _session(self) -> bool:
            return access.session_ok(self.headers.get("Cookie"))

        def _file(self, found):
            data, ctype = found
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            for k, v in page.SECURITY_HEADERS.items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path, _, query = self.path.partition("?")
            caller = self._caller()
            found = page.read_file(path)         # the page's own files: any home address, nothing private in them
            if found and caller != guard.OUTSIDE:
                return self._file(found)
            if not guard.allowed(caller, "GET", path, self._session(), "page=1" in query.split("&")):
                return self._error(403, "forbidden")
            s = state_view()
            held = s["held"] is not None  # weight taken (yellow): the scan and weight stay current until red / next scan
            tag = {"status": "ok", "nfc_id": s["tag"], "read_at": iso(s["tag_at"]), "read_at_unix": s["tag_at"],
                   "age_seconds": age(s["tag_at"]), "held": held}
            if path == "/last-tag":
                return self._send(200, tag)
            if path == "/settings" and store is not None:
                return self._send(200, {"settings": store.listing(), "page_from": page_from})
            if path == "/state":
                sc = s["scale"]
                air = s["air"] if s["air"] and time.time() - s["air_at"] < 60 else None  # panel gone: no stale reading
                last = ({"grams": s["held"], "age_seconds": age(s["held_at"]), "held": True} if held else
                        {"grams": s["last"], "age_seconds": age(s["last_at"]), "held": False} if s["last"] else None)
                return self._send(200, {
                    "tag": tag,
                    "scale": dict(sc, stable=sc["status"] == "stable", age_seconds=age(s["scale_at"])) if sc else None,
                    "last_weight": last, "panel": s["panel"], "panel_version": s["panel_version"],
                    "panel_nfc": s["panel_nfc"],
                    "air": dict(air, age_seconds=age(s["air_at"])) if air else None, "flow": s["flow"],
                    "photo": {"url": "/photo", "taken_at": iso(s["photo_at"]), "age_seconds": age(s["photo_at"])}
                    if s["photo"] else None,
                    "version": __version__, "settings_changed": bool(store and store.changed)})
            if path == "/photo" and s["photo"]:
                self.send_response(200)
                self.send_header("Content-Type", "image/jpeg")
                self.send_header("Content-Length", str(len(s["photo"])))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.end_headers()
                return self.wfile.write(s["photo"])
            self._error(404, "not_found")

        def _body(self):
            """The request body (bytes, at most page.MAX_BODY), or None after answering 400 / 413."""
            try:
                n = int(self.headers.get("Content-Length") or "")
            except ValueError:
                self._error(400, "bad_request")
                return None
            if not 0 <= n <= page.MAX_BODY:
                self._error(413, "too_big")
                return None
            return self.rfile.read(n)

        def _json(self, raw):
            try:
                data = json.loads(raw or b"{}")
            except ValueError:
                data = None
            if not isinstance(data, dict):
                self._error(400, "bad_request", guard.TEXTS["not_json"])
                return None
            return data

        def _same_origin(self) -> bool:
            """A browser call from the page itself: its Origin (when it sends one) is this station's own address."""
            origin, host = self.headers.get("Origin"), self.headers.get("Host")
            return origin is None or (host is not None and origin in (f"https://{host}", f"http://{host}"))

        def do_POST(self):
            path = self.path.split("?")[0]
            caller = self._caller()
            if not guard.allowed(caller, "POST", path, self._session()):
                return self._error(403, "forbidden")
            if path not in ("/ticket", "/session", "/settings", "/voice", "/voice/test"):
                return self._error(404, "not_found")
            raw = self._body()
            if raw is None:
                return
            proof = self.headers.get(page.PROOF_HEADER)
            if path == "/ticket":       # the Hub call: an address on the allow-list (guard) and the proof of the pairing
                if not access.proof_ok("ticket", proof, raw):
                    return self._error(403, "forbidden")
                ticket = access.new_ticket()
                if ticket is None:
                    return self._error(429, "too_many_waiting_tickets")
                return self._send(200, {"ticket": ticket, "expires_in": page.TICKET_SECONDS})
            if path == "/session":      # the page call: the ticket is the credential
                address = guard.peer_address(self.client_address[0])
                if wrong_tickets.refused(address):
                    return self._error(429, "too_many_wrong_tickets")
                data = self._json(raw)
                if data is None:
                    return
                if not self._same_origin():
                    return self._error(403, "forbidden")
                sid = access.open_session(data.get("ticket"))
                if sid is None:
                    wrong_tickets.wrong(address)
                    return self._error(403, "forbidden", guard.TEXTS["ticket_not_valid"])
                return self._send(200, {"ok": True}, {"Set-Cookie": page.cookie_header(sid)})
            if path in ("/voice", "/voice/test"):    # the Hub's call: an allow-listed address and the proof, as /settings
                if not (caller == guard.LISTED and proof is not None and access.proof_ok("voice", proof, raw)):
                    return self._error(403, "forbidden")
                data = self._json(raw)
                if data is None:
                    return
                from . import speech
                try:
                    speech.set_voice(data) if path == "/voice" else speech.test_voice(data)
                except speech.VoiceRefused as e:
                    return self._error(e.status, e.code, e.message)
                return self._send(200, {})
            if store is None:
                return self._error(404, "not_found")
            # a setting changes on the Hub call (allow-list and the proof of the pairing) or from the page's own session
            hub = caller == guard.LISTED and proof is not None and access.proof_ok("settings", proof, raw)
            browser = (not hub and self._session() and self._same_origin()
                       and (self.headers.get("Content-Type") or "").split(";")[0].strip() == "application/json")
            if not (hub or browser):
                return self._error(403, "forbidden")
            data = self._json(raw)
            if data is None:
                return
            from .settings import SetOnHub, SettingsError
            try:
                listing = store.reset() if data.get("reset") is True and len(data) == 1 else store.change(data)
            except SetOnHub as e:
                return self._error(409, e.code, str(e))
            except SettingsError as e:
                return self._error(400, "bad_request", str(e))
            self._send(200, {"settings": listing})

        def log_message(self, fmt, *args):
            pass  # the phone app polls; one journal line per poll is noise

    return Handler
