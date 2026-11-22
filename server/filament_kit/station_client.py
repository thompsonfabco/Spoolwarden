"""Read-only client for an intake station's `GET /state` (filament-ops station/intake_station.py).

The state comes back as the station sent it: `{"tag": {...}, "scale": {...}|null, "last_weight": {...}|null,
"panel": ...}` ("held" sits inside "tag" and "last_weight"). After a failure the client refuses straight away
for `backoff` seconds, so a dead station costs the app one quick 503 per call instead of a connect timeout (same
policy as filament-mcp).

HTTPS: a station that has told the Hub its certificate (`pairing.station_info`: fingerprint, port,
version, from its hello call) is called over HTTPS at that port and accepts only that certificate; a changed one is
refused (`StationCertChanged`, with the Error and Fix texts below), never retried over plain HTTP. A station that sent
no fingerprint (not updated yet) is called at its configured url as before. The same pinned connection and a small
store of pins (`pins.xml` in the state folder, for the tag sources and the remote Label Reader) are here too, so
every pinned call of the Hub is one piece of code.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import socket
import ssl
import threading
import time
import urllib.error
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from . import demo
from .pairing import AmbiguousStation


def _say(key: str, /, **values) -> str:
    """A sentence of a failed call to the Weigh Station, from the catalogue (D13: the Hub page shows it)."""
    from .station.page import say
    return say(key, **values)


# Texts for the Checks rows and the page (AF5 moves them to the catalogue).
TEXT_STATION_CERT_CHANGED = "The Weigh Station's certificate is not the one the Hub trusts."
TEXT_STATION_CERT_FIX = ("Restart the Weigh Station so it tells the Hub its new certificate "
                         "(sudo systemctl restart filament-kit-station), or pair it again.")
TEXT_STATION_NOT_HTTPS = "The Weigh Station {name} is not using HTTPS. Update it."
# the row for a station that said it could not make its certificate ("plain by necessity")
TEXT_STATION_NOT_HTTPS_AT = "The Weigh Station at {address} is not using HTTPS."
TEXT_STATION_NOT_HTTPS_WHY = "The Weigh Station at {address} is not using HTTPS: it could not make its certificate ({reason})."
# a pinned station that now answers only plain HTTP or with another certificate: the same Error and Fix, no downgrade


class StationUnreachable(Exception):
    pass


class StationCertChanged(StationUnreachable):
    """The station presented a certificate other than the pinned one: refused, with the Error and the Fix to show."""
    error = TEXT_STATION_CERT_CHANGED
    fix = TEXT_STATION_CERT_FIX


class PinMismatch(ssl.SSLError):
    """The peer's certificate is not the pinned one. `got` / `expected`: their SHA-256 fingerprints."""

    def __init__(self, got: str, expected: str):
        super().__init__(f"certificate changed (fingerprint {got[:16]}..., expected {expected[:16]}...)")
        self.got, self.expected = got, expected


def _context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE   # trust is the pinned fingerprint, checked before anything is sent
    return ctx


class PinnedConnection(http.client.HTTPSConnection):
    """An HTTPS connection that sends nothing unless the peer's certificate has the SHA-256 `fp` (raises PinMismatch).
    With `fp` None every certificate is taken and its fingerprint is left in `.seen` (trust on first use: the caller
    decides what to keep)."""

    def __init__(self, host: str, port: int | None, fp: str | None, timeout: float):
        super().__init__(host, port, timeout=timeout, context=_context())
        self.fp, self.seen = fp, None

    def connect(self):
        demo.stop()                  # no connection to a station, a tag source or the network in the demo
        super().connect()
        self.seen = hashlib.sha256(self.sock.getpeercert(binary_form=True) or b"").hexdigest()
        if self.fp is not None and self.seen != self.fp:
            self.sock.close()
            raise PinMismatch(self.seen, self.fp)


def pinned_get(host: str, port: int, path: str, fp: str | None, timeout: float, cap: int = 1 << 20,
               headers: dict | None = None) -> tuple[int, bytes, str]:
    """GET `path` over a PinnedConnection: (status, body of at most cap + 1 bytes, the certificate's fingerprint). No
    redirect is followed. Raises PinMismatch, or OSError / http.client.HTTPException."""
    c = PinnedConnection(host, port, fp, timeout)
    try:
        c.request("GET", path, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read(cap + 1), c.seen
    finally:
        c.close()


# Name resolution has no socket timeout. Cap the number of outstanding lookups so stuck system
# DNS cannot leave an unbounded number of threads behind after the request's deadline.
_RESOLVERS = threading.BoundedSemaphore(4)


def _remaining(deadline: float) -> float:
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("the answer is too slow")
    return seconds


def _resolve_before(address, deadline: float):
    host, port = address
    try:
        return socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, flags=socket.AI_NUMERICHOST)
    except socket.gaierror:
        pass
    if not _RESOLVERS.acquire(timeout=_remaining(deadline)):
        raise TimeoutError("the address lookup is too slow")
    ready = threading.Event()
    result = []

    def lookup():
        try:
            result.append(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except (OSError, UnicodeError) as e:
            result.append(OSError(str(e)))
        finally:
            _RESOLVERS.release()
            ready.set()

    try:
        threading.Thread(target=lookup, daemon=True).start()
    except RuntimeError:
        _RESOLVERS.release()
        raise
    if not ready.wait(max(0, deadline - time.monotonic())):
        raise TimeoutError("the address lookup is too slow")
    found, = result
    if isinstance(found, OSError):
        raise found
    return found


def _connect_before(address, deadline: float) -> socket.socket:
    demo.stop()                  # no connection to a station or the network in the demo
    error: OSError = OSError("no address found")
    for family, kind, protocol, _, target in _resolve_before(address, deadline):
        seconds = _remaining(deadline)
        try:
            sock = socket.socket(family, kind, protocol)
        except OSError as e:
            error = e
            continue
        try:
            sock.settimeout(seconds)
            sock.connect(target)
            sock.settimeout(_remaining(deadline))
        except OSError as e:
            sock.close()
            error = e
        else:
            return sock
    _remaining(deadline)
    raise error


def bounded_call(host: str, port: int, fp: str | None, method: str, path: str, body: bytes | None = None,
                 headers: dict | None = None, cap: int = 1 << 20, timeout: float = 5,
                 plain: bool = False) -> tuple[int, str, bytes]:
    """One call with ONE deadline of `timeout` seconds for the whole of it (connect, headers and body: a station that
    trickles its answer is cut off, the read after the deadline raises TimeoutError). (status, content type, body of at
    most `cap` bytes). No redirect is followed (a 3xx comes back as its status), no proxy of the environment is used.
    HTTPS accepts only the certificate with the SHA-256 `fp` (PinMismatch); `plain` is for an older station that has told
    the Hub no certificate. Raises ValueError when the answer is larger than `cap`, OSError, http.client.HTTPException."""
    deadline = time.monotonic() + timeout
    c = http.client.HTTPConnection(host, port, timeout=timeout) if plain else PinnedConnection(host, port, fp, timeout)
    c._create_connection = lambda address, *_: _connect_before(address, deadline)
    held: list = [None]         # the socket, kept: getresponse() lets c.sock go for an answer that ends by closing

    fired = [False]

    def cut():
        fired[0] = True
        sock = held[0] or c.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)       # only SHUT: the reading thread sees the end; it closes in `finally`
            except OSError:
                pass
    timer = threading.Timer(timeout, cut)
    timer.daemon = True
    timer.start()
    try:
        c.connect()
        held[0] = c.sock
        _remaining(deadline)
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = bytearray()
        while True:
            chunk = r.read1(8192)       # what has arrived, not a wait for the whole 8192 bytes
            if not chunk:
                break
            data += chunk
            if len(data) > cap:
                raise ValueError("the answer is too large")
            if time.monotonic() > deadline:
                raise TimeoutError("the answer is too slow")
        if fired[0] or time.monotonic() > deadline:     # the cut ends a slow answer as if it were complete: it is not
            raise TimeoutError("the answer is too slow")
        return r.status, r.getheader("Content-Type", "") or "", bytes(data)
    except (OSError, http.client.HTTPException):
        if fired[0]:                        # whatever the shut socket made of it (a broken pipe, a reset): it was too slow
            raise TimeoutError("the answer is too slow") from None
        raise
    except AttributeError as e:             # http.client closing under a shut socket
        raise TimeoutError("the answer is too slow") if time.monotonic() >= deadline - 0.05 else OSError(str(e)) from None
    finally:
        timer.cancel()
        c.close()


def peer_fingerprint(host: str, port: int, timeout: float = 5) -> str:
    """The SHA-256 of the certificate a listener presents (the handshake only). Raises OSError."""
    c = PinnedConnection(host, port, None, timeout)
    try:
        c.connect()
        return c.seen
    finally:
        c.close()


PINS_FILE = "pins.xml"


def _pins_path():
    from . import config
    return config.paths().state / PINS_FILE


def _read_pins() -> dict[tuple[str, str], str]:
    try:
        root = ET.fromstring(_pins_path().read_bytes())
    except (OSError, ET.ParseError):
        return {}
    return {(e.get("kind") or "", e.get("key") or ""): (e.get("fp") or "").lower() for e in root.iter("pin")}


def pin_get(kind: str, key: str) -> str | None:
    """The fingerprint kept for (`kind`, `key`): "tag" with the source's address, "reader" with the reader's. None
    when none is kept."""
    return _read_pins().get((kind, key)) or None


def pin_set(kind: str, key: str, fp: str | None) -> None:
    """Keep `fp` for (`kind`, `key`), replacing the one there; None drops it (the owner saved the address again).
    State data: XML, written whole and owned like the state folder's other files."""
    from . import pairing
    with pairing._locked():
        pins = _read_pins()
        if fp:
            pins[(kind, key)] = fp.lower()
        else:
            pins.pop((kind, key), None)
        root = ET.Element("pins")
        for (k, key_), f in sorted(pins.items()):
            ET.SubElement(root, "pin", kind=k, key=key_, fp=f)
        _pins_path().parent.mkdir(parents=True, exist_ok=True)
        pairing._write_atomic(_pins_path(), ET.tostring(root, encoding="utf-8", xml_declaration=True), 0o600,
                              state_owned=True)


def _station_info() -> dict | None:
    from . import pairing
    try:
        return pairing.station_info()
    except (OSError, ValueError):
        return None        # (AmbiguousStation is not caught here: the callers refuse, they never go plain)


class StationClient:
    def __init__(self, url: str | None, timeout: float = 3, backoff: float = 60):
        self.url = url.rstrip("/") if url else None
        self.timeout = timeout
        self.backoff = backoff
        self._failed_at: float | None = None
        self._lock = threading.Lock()

    def state(self) -> dict:
        if not self.url:
            raise StationUnreachable("no station url")
        with self._lock:
            failed = self._failed_at
        if failed is not None and time.monotonic() - failed < self.backoff:
            raise StationUnreachable("station unreachable (backing off)")
        try:
            info = _station_info()
        except AmbiguousStation as e:
            self._fail()
            raise StationUnreachable(str(e)) from e
        try:
            u = urllib.parse.urlsplit(self.url)
            if not u.hostname or u.scheme not in ("https", "http"):
                raise StationUnreachable("the station address must be HTTP or HTTPS")
            if info:
                port, fp = info["port"], info["fp"]
            elif u.scheme == "https":
                raise StationUnreachable(TEXT_STATION_NO_PIN)
            else:
                port, fp = u.port or 80, None
            status, _kind, raw = bounded_call(u.hostname, port, fp, "GET", "/state", timeout=self.timeout,
                                              plain=info is None)
            if status != 200:
                raise StationUnreachable(f"station answered {status}")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except StationUnreachable:
            self._fail()
            raise
        except PinMismatch as e:
            self._fail()
            raise StationCertChanged(f"station at {self.url}: {e}") from e
        except ssl.SSLError as e:       # a pinned station that does not speak TLS (plain HTTP only): never called plain
            self._fail()
            raise StationCertChanged(f"station at {self.url} does not answer over HTTPS: {e}") from e
        except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as e:
            self._fail()
            raise StationUnreachable(f"station at {self.url}: {e}") from e
        with self._lock:
            self._failed_at = None
        return data

    def _fail(self) -> None:
        with self._lock:
            self._failed_at = time.monotonic()


REACH_KEEP = 30         # seconds a reachability answer is reused: the Hub page and `devices` must not wait on a dead station each time
_reach: dict[str, tuple[float, bool]] = {}


def station_reach(devs: list[dict], url: str | None) -> dict[str, bool]:
    """{device id: does the Weigh Station answer at `url`} for the one station pairing. A station only calls the Hub when
    someone uses it, so "last seen" alone makes an idle one look dead; this asks it instead (its `GET /state`, which every
    station version has). Empty when no station address is set or the station pairing is not exactly one (which
    pairing the address belongs to would be a guess)."""
    ids = [d["id"] for d in devs if d.get("type") == "station"]
    if not url or len(ids) != 1:
        return {}
    now = time.monotonic()
    hit = _reach.get(url)
    if hit is None or now - hit[0] > REACH_KEEP:
        try:
            StationClient(url, timeout=2, backoff=0).state()
            hit = (now, True)
        except StationUnreachable:
            hit = (now, False)
        _reach[url] = hit
    return {ids[0]: hit[1]}


_panel_seen: dict[str, tuple[float, str | None, str | None]] = {}


def _panel_state(url: str | None) -> tuple[str | None, str | None]:
    """(panel, flash notice) from the station's `GET /state`, one read reused for REACH_KEEP seconds. Both are None when the
    station does not answer. The notice is the Hub's own catalogue sentence while the station carries a `flash_notice`."""
    if not url:
        return None, None
    now = time.monotonic()
    hit = _panel_seen.get(url)
    if hit is None or now - hit[0] > REACH_KEEP:
        try:
            got = StationClient(url, timeout=2, backoff=0).state()
        except (StationUnreachable, StationCertChanged):
            got = {}
        value, note = got.get("panel"), got.get("flash_notice")
        hit = (now, value if value in ("usb", "wifi") else None,
               TEXT_STATION_WIFI_LOST if isinstance(note, str) and note.strip() else None)
        _panel_seen[url] = hit
    return hit[1], hit[2]


def station_panel(url: str | None) -> str | None:
    """How the Weigh Station's panel is connected, from its `GET /state` `panel`: "usb" or "wifi"; None when it has no panel
    connected or does not answer (the Devices row then says nothing). Reused for REACH_KEEP seconds."""
    return _panel_state(url)[0]


def station_flash_notice(url: str | None) -> str | None:
    """The sentence to show while the station's `GET /state` carries a `flash_notice` (the panel was flashed from scratch and
    lost its Wi-Fi set-up); None when it has none or does not answer."""
    return _panel_state(url)[1]


def station_https(url: str | None) -> str | None:
    """How the Hub reaches the Weigh Station at `url`, for the Checks: None (no url), "plain" (it has told the Hub no
    certificate: not updated; the row TEXT_STATION_NOT_HTTPS), "https" (answers with the pinned certificate),
    "changed" (a different certificate: TEXT_STATION_CERT_CHANGED with TEXT_STATION_CERT_FIX) or "unreachable"."""
    if not url:
        return None
    try:
        if _station_info() is None:
            return "unreachable" if urllib.parse.urlsplit(url).scheme == "https" else "plain"
    except (AmbiguousStation, ValueError):
        return "unreachable"
    try:
        StationClient(url, timeout=2, backoff=0).state()
    except StationCertChanged:
        return "changed"
    except StationUnreachable:
        return "unreachable"
    return "https"


def station_https_row(url: str | None) -> str | None:
    """The Checks row text for a Weigh Station that is not on HTTPS, else None: `TEXT_STATION_NOT_HTTPS_WHY` when the
    station said it could not make its certificate, `TEXT_STATION_NOT_HTTPS_AT` otherwise (an older station that has
    told the Hub no certificate). Only for a url whose `station_https` is "plain"."""
    if not url or station_https(url) != "plain":
        return None
    from . import pairing
    try:
        reason = pairing.station_plain_reason()
    except (OSError, ValueError):
        reason = None
    u = urllib.parse.urlsplit(url)
    address = f"{u.hostname}:{u.port}" if u.port else str(u.hostname)
    return (TEXT_STATION_NOT_HTTPS_WHY.format(address=address, reason=reason) if reason
            else TEXT_STATION_NOT_HTTPS_AT.format(address=address))



# ---- the Hub's checked calls to the station's own routes ----
# What the Hub takes from a station is data, checked: JSON only (`application/json`), at most 256 KiB, one 5 second deadline
# for the whole call, a redirect is an error, only the stored host with the port and certificate of that pairing (never plain
# HTTP). A change or a ticket carries the proof of the pairing (station/page.py: an HMAC with a key both sides derive from the
# token, which never travels: the Hub keeps the token's SHA-256 in the pairing, the station has the token).

STATION_TIMEOUT = 5
MAX_JSON = 256 * 1024
MAX_PHOTO = 4 * 1024 * 1024
MAX_SETTINGS = 60
MAX_LABEL = 80
MAX_TEXT = 200
SETTING_ID = re.compile(r"[a-z0-9_.-]{1,40}")
SETTING_KINDS = ("switch", "choice", "number", "text")
TICKET = re.compile(r"[A-Za-z0-9_-]{16,128}")
def _catalogue_text(key: str, fallback: str | None = None) -> str:
    text = (Path(__file__).resolve().parent / "shared_static" / "strings.js").read_text(encoding="utf-8")
    cat = json.loads(re.sub(r",\s*}$", "}", text[text.index("{"):text.rindex("}") + 1]))
    vals = {"product": cat.get("app_name", ""), "command": cat.get("command_name", "")}
    return re.sub(r"\{(\w+)\}", lambda m: vals.get(m.group(1), m.group(0)), cat[key] if fallback is None else cat.get(key, fallback))


TEXT_STATION_NO_PIN = _catalogue_text("station_not_pinned")
# the station refuses a page change of Speak or Volume once the Hub has sent values (409 `set_on_hub`); same key as the station's
TEXT_STATION_SET_ON_HUB = _catalogue_text("station_voice_set_on_hub", "Speaking and volume are set on the Hub.")
# after a from-scratch flash of a wireless panel the station says its Wi-Fi set-up is gone (`flash_notice`); the owner reads this
TEXT_STATION_WIFI_LOST = _catalogue_text("station_panel_wifi_lost", "The panel was flashed from scratch, so its Wi-Fi set-up is gone. "
                                         "On the Weigh Station's computer, run: sudo {command} weigh-station panel-wifi")


class StationCallError(Exception):
    """The Hub could not get a checked answer from the station: `status` is what the Hub page says (502, 503 ...)."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class StationSetOnHub(StationCallError):
    """The station refused a change of Speak or Volume: the Hub sets them (409 `set_on_hub`)."""


class StationPinMissing(StationCallError):
    """The Hub knows the station's address but has no certificate to check."""


def proof_key(*, pinned: bool = True) -> bytes | None:
    """The key of the Hub's proofs for the one station pairing that holds a certificate (`pinned=False`: the one station
    pairing, also one that told the Hub no certificate and is called over plain HTTP): HKDF of the SHA-256 of its token,
    which is what the pairing record keeps (`hash`). None when there is no such pairing or there are two."""
    from . import pairing
    from .station import page
    try:
        with pairing._locked():
            found = [d for d in pairing._load_devices() if d.get("claimed") is not None
                     and pairing.device_type(d) == "station" and (d.get("fp") or not pinned)]
    except (OSError, ValueError):
        return None
    if len(found) != 1 or not re.fullmatch(r"[0-9a-f]{64}", str(found[0].get("hash") or "")):
        return None
    return page.proof_key(found[0]["hash"])


def pinned_endpoint(url: str | None) -> tuple[str, int, str]:
    """Resolve the stored Station host and the port and certificate announced by the paired Station."""
    try:
        host = urllib.parse.urlsplit(url or "").hostname
        info = _station_info()
    except (AmbiguousStation, ValueError) as e:
        raise StationCallError(503, str(e)) from None
    if not host:
        raise StationCallError(503, _say("station_call_address_unknown"))
    if info is None:
        raise StationPinMissing(503, TEXT_STATION_NO_PIN)
    return host, info["port"], info["fp"]


def call(url: str | None, method: str, path: str, body: bytes | None = None, cap: int = MAX_JSON,
         timeout: float = STATION_TIMEOUT) -> tuple[int, str, bytes]:
    """(status, content type, body) of one pinned call to the Station, with one deadline and no redirects."""
    return _call(station_endpoint(url), method, path, body, cap, timeout)


def station_endpoint(url: str | None) -> tuple[str, int, str | None]:
    """Permit the configured HTTP endpoint only when no station certificate was announced."""
    try:
        return pinned_endpoint(url)
    except StationPinMissing:
        try:
            parsed = urllib.parse.urlsplit(url or "")
            if parsed.scheme == "http" and parsed.hostname:
                return parsed.hostname, parsed.port or 80, None
        except ValueError:
            raise StationCallError(503, _say("station_call_address_unknown")) from None
        raise


def _call(endpoint, method, path, body, cap=MAX_JSON, timeout=STATION_TIMEOUT):
    from .station import page

    host, port, fp = endpoint
    headers = {"Accept": "*/*"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    purpose = {("POST", "/ticket"): "ticket", ("POST", "/settings"): "settings", ("POST", "/voice"): "voice",
               ("POST", "/voice/test"): "voice"}.get((method, path))
    if purpose == "ticket" and fp is None:
        # D14: the page ticket is the way into the station's own page: the Hub never asks for one over plain HTTP, whatever the proof
        raise StationPinMissing(503, TEXT_STATION_NO_PIN)
    if purpose:
        # the proof, not HTTPS, guards a change; a station that told the Hub no certificate is called over
        # plain HTTP for it (`station_endpoint` never goes plain for a pinned one)
        key = proof_key(pinned=fp is not None)
        if key is None:
            raise StationCallError(503, _say("station_call_cannot_prove"))
        headers[page.PROOF_HEADER] = page.make_proof(key, purpose, body or b"")
    try:
        options = {"plain": True} if fp is None else {}
        status, kind, data = bounded_call(host, port, fp, method, path, body, headers, cap, timeout, **options)
    except PinMismatch:
        raise StationCallError(502, f"{TEXT_STATION_CERT_CHANGED} {TEXT_STATION_CERT_FIX}") from None
    except TimeoutError:
        raise StationCallError(502, _say("station_call_too_slow")) from None
    except UnicodeError:
        raise StationCallError(502, _say("station_call_address_invalid")) from None
    except ValueError:
        raise StationCallError(502, _say("station_call_answer_too_large")) from None
    except (OSError, http.client.HTTPException) as e:
        raise StationCallError(502, _say("station_call_no_answer", kind=type(e).__name__)) from None
    if 300 <= status < 400:
        raise StationCallError(502, _say("station_call_redirect"))
    return status, kind, data


def call_json(url: str | None, method: str, path: str, body: dict | None = None) -> dict:
    """The JSON object a station answers with a 2xx: `application/json` only. A refusal of the station (4xx) is a
    StationCallError with its status and its own reason (`error` or `message`, at most 200 characters)."""
    return _json_reply(*call(url, method, path, None if body is None else json.dumps(body).encode()))


def _json_reply(status: int, kind: str, data: bytes) -> dict:
    if kind.split(";")[0].strip().lower() != "application/json":
        raise StationCallError(502, _say("station_call_not_json"))
    try:
        out = json.loads(data or b"{}")
    except ValueError:
        out = None
    if not isinstance(out, dict):
        raise StationCallError(502, _say("station_call_answer_invalid"))
    if status == 409 and out.get("error") == "set_on_hub":
        raise StationSetOnHub(409, TEXT_STATION_SET_ON_HUB)
    if not 200 <= status < 300:
        why = out.get("message") or out.get("error")
        raise StationCallError(status if status in (400, 403, 404, 429) else 502,
                               str(why)[:MAX_TEXT] if isinstance(why, str) and why else f"the Weigh Station answered {status}")
    return out


def ticket(url: str | None, *, endpoint: tuple[str, int, str | None] | None = None) -> dict:
    """A one-time ticket for the station's own page, `{ticket, expires_in: 60}` (the proof goes with the call). The
    caller opens `<the station's address>#ticket=<ticket>` for the browser: the ticket is never in a request line."""
    got = _json_reply(*_call(endpoint if endpoint is not None else station_endpoint(url), "POST", "/ticket", b"{}"))
    t, expires = got.get("ticket"), got.get("expires_in")
    if not (isinstance(t, str) and TICKET.fullmatch(t)) or expires != 60 or isinstance(expires, bool):
        raise StationCallError(502, _say("station_call_ticket_invalid"))
    return {"ticket": t, "expires_in": 60}


def read_state(url: str | None) -> dict:
    """The station's `GET /state` as checked data: an object, with the station's `photo` reduced to a bool."""
    out = call_json(url, "GET", "/state")
    out["photo"] = bool(out.get("photo"))
    return out


def read_settings(url: str | None) -> tuple[list[dict], bool]:
    """(the settings that pass the checks, whether any was dropped), from `GET /settings`."""
    return clean_settings(call_json(url, "GET", "/settings").get("settings"))


def change_settings(url: str | None, values: dict) -> tuple[list[dict], bool]:
    """`POST /settings` with the proof: `{<id>: <value>}` or `{reset: true}`; the new list, checked as `read_settings`."""
    return clean_settings(call_json(url, "POST", "/settings", values).get("settings"))


def send_voice(url: str | None, values: dict) -> None:
    """`POST /voice` with `{speech, speaker, speed, volume}` (any subset): the station keeps them in memory, with
    no restart. Raises StationCallError when it does not answer or refuses them."""
    call_json(url, "POST", "/voice", values)


def voice_test(url: str | None, values: dict) -> tuple[int, str]:
    """`POST /voice/test` with `{speaker?, speed?, volume?}`: (status, the station's `error` code or ""). 200 played, 409
    `busy` or `off`. Raises StationCallError when it does not answer."""
    status, kind, data = call(url, "POST", "/voice/test", json.dumps(values).encode())
    code = ""
    if kind.split(";")[0].strip().lower() == "application/json":
        try:
            got = json.loads(data or b"{}")
        except ValueError:
            got = None
        if isinstance(got, dict) and isinstance(got.get("error"), str):
            code = got["error"]
    return status, code


def read_photo(url: str | None) -> bytes:
    """The station's photo: only `image/jpeg`, at most 4 MiB, and it must begin as a JPEG does."""
    status, kind, data = call(url, "GET", "/photo", cap=MAX_PHOTO)
    if status != 200 or kind.split(";")[0].strip().lower() != "image/jpeg" or not data.startswith(b"\xff\xd8"):
        raise StationCallError(502, _say("station_call_photo_invalid"))
    return data


def _text(v, limit: int = MAX_TEXT) -> str | None:
    return v if isinstance(v, str) and len(v) <= limit else None


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) < 1e12


def clean_settings(raw) -> tuple[list[dict], bool]:
    """(the settings that pass the checks, whether any was dropped): `id` [a-z0-9_.-]{1,40}, `kind` one of switch, choice,
    number, text, `label` at most 80 characters, at most 60 settings, a value of the kind's own type; any other is dropped."""
    if not isinstance(raw, list):
        return [], bool(raw)
    out, seen, dropped = [], set(), len(raw) > MAX_SETTINGS
    for s in raw[:MAX_SETTINGS]:
        item = _clean_setting(s) if isinstance(s, dict) else None
        if item is None or item["id"] in seen:
            dropped = True
            continue
        seen.add(item["id"])
        out.append(item)
    return out, dropped


def _clean_setting(s: dict) -> dict | None:
    sid, kind, label, value = s.get("id"), s.get("kind"), _text(s.get("label"), MAX_LABEL), s.get("value")
    if not (isinstance(sid, str) and SETTING_ID.fullmatch(sid)) or kind not in SETTING_KINDS or not label:
        return None
    out = {"id": sid, "label": label, "kind": kind}
    if kind == "switch":
        if not isinstance(value, bool):
            return None
    elif kind == "number":
        if not _number(value):
            return None
        for k in ("min", "max"):
            if _number(s.get(k)):
                out[k] = s[k]
    elif kind == "text":
        if _text(value) is None:
            return None
    else:
        if not (_text(value) is not None or _number(value)):
            return None
        choices = []
        for c in (s.get("choices") if isinstance(s.get("choices"), list) else [])[:40]:
            v, lab = (c.get("value"), c.get("label")) if isinstance(c, dict) else (c, c)
            if _text(v, MAX_LABEL) is None or _text(lab, MAX_LABEL) is None:
                return None
            choices.append(v if not isinstance(c, dict) else {"value": v, "label": lab})
        out["choices"] = choices
    out["value"] = value
    if s.get("readonly") is True:                   # the station's own page shows it, but it is not changed there (VO1)
        out["readonly"] = True
    if isinstance(s.get("note"), str) and s["note"].strip():
        out["note"] = s["note"].strip()[:MAX_TEXT]
    return out
