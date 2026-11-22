"""The panel over Wi-Fi: the same line protocol as on USB (see `panel`), carried by TLS.

The station listens on `station.panel_port` (5004) with its own certificate (the panel pins its fingerprint) when
`station.panel_link` is `wifi`. The panel dials in and its first line is `HELLO intake-panel <version> <chip> <token>`;
the token is compared in constant time with the one `sudo spoolwarden weigh-station panel-wifi` stored (`read_token`).
Then the station speaks the protocol exactly as on USB: it sends the lamps every second, the panel's lines (buttons,
`TAG`, `AIR`, `W`) reach `panel.on_line`.

One panel at a time, and a panel on USB wins: a second connection while USB is up (or while another Wi-Fi panel is
heard from) is answered with one line and closed:
    REFUSED token   wrong token, or none stored     REFUSED hello   not a HELLO line, or a line too long
    REFUSED usb     a panel on USB is in use        REFUSED busy    another Wi-Fi panel is connected
The lamps' one-second repeat is the keep-alive: the station sends `L` every second (the panel counts 5 s without it as
no service), and the panel sends any line, `K` when it has nothing else, at least every second (the station counts the
panel as gone after KEEPALIVE_SECONDS of silence). A panel that drops and dials in again replaces its dead link.
"""
from __future__ import annotations

import hmac
import logging
import select
import socket
import ssl
import threading
import time

from . import panel_setup
from . import state as st

log = logging.getLogger("intake-station")

MAX_LINE = 256            # the longest line either way (the HELLO with its token is the longest)
HELLO_SECONDS = 5         # a new connection has this long for its TLS handshake and its HELLO line
KEEPALIVE_SECONDS = 5     # silence from the panel for this long and the station counts it as gone
STALE_SECONDS = 2         # a Wi-Fi link silent this long gives way to a panel dialling in (its own dead link)
WRITE_SECONDS = 2         # a stalled write drops the link (it must not hold panel_lock long)
REFUSE_DELAY = 1.0        # a refused panel waits this long before the line (slows a token guesser)
MAX_WAITING = 8           # connections in their HELLO at once; more are closed at once
TICK = 0.3                # the session wakes this often to keep the lamps current (as the USB loop's readline)

_waiting = threading.BoundedSemaphore(MAX_WAITING)


def read_token() -> str | None:
    """The panel's token: the one `panel-wifi` stored in the station's state folder (`panel_setup.stored_token`, the
    only place it is kept) and `--off` removes. None when there is none (every panel is then refused)."""
    return (panel_setup.stored_token() or "").strip() or None


def token_ok(given: bytes) -> bool:
    """`given` is the stored token (compared in constant time; with no stored token the compare still runs)."""
    stored = (read_token() or "").encode("ascii", "replace")
    return hmac.compare_digest(given, stored) and bool(stored)


class Link:
    """A connected Wi-Fi panel with the part of pyserial's interface the station uses (`write`, `port`, `close`),
    so `wire.panel_send` and the speech frames reach it unchanged. `heard` is when the panel last sent a line."""
    wifi = True

    def __init__(self, sock, address, version: str, chip: str | None):
        self.sock, self.address, self.panel_version, self.panel_chip = sock, address, version, chip
        self.port = f"wifi:{address}"
        self.heard = time.time()
        self.buffer = b""
        self.io = threading.Lock()  # one read or write on the TLS object at a time
        sock.settimeout(WRITE_SECONDS)
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)   # small lines go at once, not after Nagle's wait
        except OSError:
            pass

    def write(self, data: bytes):
        with self.io:
            self.sock.sendall(data)

    def read_line(self, wait: float) -> bytes | None:
        """The next line (without its end), None when none came within `wait` seconds. ConnectionError when the panel
        closed, ValueError for a line longer than MAX_LINE."""
        while b"\n" not in self.buffer:
            if len(self.buffer) > MAX_LINE:
                raise ValueError("line too long")
            with self.io:
                ready = self.sock.pending() > 0
            if not ready:   # waiting holds no lock: a write (the speech frames) is never held up by a read that waits
                ready = bool(select.select([self.sock], [], [], wait)[0])
            if not ready:
                return None
            with self.io:
                data = self.sock.recv(1024)
            if not data:
                raise ConnectionError("closed")
            self.buffer += data
            wait = 0
        line, _, self.buffer = self.buffer.partition(b"\n")
        if len(line) > MAX_LINE:
            raise ValueError("line too long")
        self.heard = time.time()
        return line.strip()

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def refuse(sock, reason: str):
    """One `REFUSED <reason>` line, best effort, then close."""
    try:
        sock.sendall(b"REFUSED " + reason.encode() + b"\n")
    except (OSError, ValueError):
        pass
    try:
        sock.close()
    except OSError:
        pass


def hello_line(sock) -> tuple[bytes | None, bytes]:
    """(the first line of a new connection or None when it did not come in time or is too long, what follows it)."""
    end, data = time.time() + HELLO_SECONDS, b""
    while b"\n" not in data:
        left = end - time.time()
        if left <= 0 or len(data) > MAX_LINE:
            return None, b""
        sock.settimeout(left)
        try:
            chunk = sock.recv(1024)
        except (OSError, ValueError):
            return None, b""
        if not chunk:
            return None, b""
        data += chunk
    line, _, rest = data.partition(b"\n")
    return (line.strip() if len(line) <= MAX_LINE else None), rest


def usb_up() -> bool:
    ser = st.panel["ser"]
    return bool(ser) and not getattr(ser, "wifi", False)


def admit(link: Link) -> str | None:
    """Make `link` the station's panel; None, or the reason it was refused (`usb`, `busy`). A Wi-Fi panel gives way
    to one dialling in from the same address, or when it has been silent for STALE_SECONDS."""
    with st.panel_lock:
        current = st.panel["ser"]
        if current and not getattr(current, "wifi", False):
            return "usb"
        if current and current.address != link.address and time.time() - current.heard <= STALE_SECONDS:
            return "busy"
        st.panel["ser"] = link
    if current:
        current.close()  # its session sees it is no longer the panel and ends
    return None


def session(link: Link):
    """Speak the protocol with an admitted panel until it is gone or another panel replaces it (`panel_loop`'s inner
    loop, over the connection)."""
    from . import panel
    st.ports["panel_version"], st.ports["panel_chip"], st.ports["panel_nfc"] = link.panel_version, link.panel_chip, None
    log.info("button panel on Wi-Fi from %s (firmware %s)", link.address, link.panel_version)
    panel.warn_swap_needs_fw9(link.panel_version)
    if link.panel_chip == "esp32c3":
        panel.send_volume()
    seen = panel.Shown()
    why = "dropped"
    try:
        while st.panel["ser"] is link:
            line = link.read_line(TICK)
            if line:
                panel.on_line(line)
            if time.time() - link.heard > KEEPALIVE_SECONDS:
                why = "silent"
                break
            if not panel.refresh(seen, time.time()):
                break
    except (OSError, ValueError, ConnectionError) as e:
        why = str(e) or type(e).__name__
    except Exception as e:  # a bug in a line's handler must not take the station down
        log.warning("button panel on Wi-Fi: %s", e)
        why = "error"
    with st.panel_lock:
        ours = st.panel["ser"] in (link, None)  # None: wire dropped it after a failed write
        if ours:
            st.panel["ser"] = None
    link.close()
    if ours:
        panel.forget_panel_scale()
        st.ports["panel_version"] = st.ports["panel_chip"] = st.ports["panel_nfc"] = None
    log.info("button panel on Wi-Fi gone (%s)", why)


def handle(sock, address, context: ssl.SSLContext):
    """One connection: the TLS handshake, the HELLO, the token, then the session. Never raises."""
    waiting = _waiting
    if not waiting.acquire(blocking=False):
        sock.close()
        return
    held = True
    try:
        sock.settimeout(HELLO_SECONDS)
        try:
            sock = context.wrap_socket(sock, server_side=True)
        except (OSError, ValueError):  # a plain-HTTP caller, a scanner, a half handshake
            sock.close()
            return
        line, rest = hello_line(sock)
        parts = line.split() if line is not None else []
        if not (len(parts) == 5 and parts[:2] == [b"HELLO", b"intake-panel"]):
            return refuse(sock, "hello")
        if not token_ok(parts[4]):
            log.warning("button panel on Wi-Fi from %s: wrong token", address)
            time.sleep(REFUSE_DELAY)
            return refuse(sock, "token")
        link = Link(sock, address, parts[2].decode(errors="replace"), parts[3].decode(errors="replace"))
        link.buffer = rest
        why = admit(link)
        if why:
            log.info("button panel on Wi-Fi from %s refused: %s", address, why)
            return refuse(sock, why)
        held = False
        waiting.release()
        session(link)
    except Exception as e:
        log.warning("button panel on Wi-Fi from %s: %s", address, e)
    finally:
        if held:
            waiting.release()


def listen(port: int) -> socket.socket:
    """The listening socket on every address (dual stack when the host has it)."""
    dual = socket.has_dualstack_ipv6()
    sock = socket.socket(socket.AF_INET6 if dual else socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if dual:
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
    sock.bind(("::" if dual else "0.0.0.0", port))
    sock.listen(8)
    return sock


def accept_loop(sock: socket.socket, context: ssl.SSLContext):
    """Accept connections for good (a thread each, so a stalled handshake holds no one else up)."""
    while True:
        try:
            conn, address = sock.accept()
        except OSError:
            return  # the socket was closed
        threading.Thread(target=handle, args=(conn, address[0], context), daemon=True).start()


def serve(port: int):
    """The Wi-Fi listener: HTTPS-grade TLS with the station's own certificate. No certificate, no listener (the panel
    pins it; there is no plain fallback). A port that cannot be bound is tried again every 30 s."""
    from . import http
    try:
        cert, key, fp = st.ensure_cert()
        context = http.tls_context(cert, key)
    except (OSError, ValueError, ImportError) as e:
        log.error("no certificate for the Wi-Fi panel listener (%s: %s): not listening", type(e).__name__, e)
        return
    while True:
        try:
            sock = listen(port)
        except OSError as e:
            log.error("Wi-Fi panel listener on port %s: %s", port, e)
            time.sleep(30)
            continue
        log.info("Wi-Fi panel listener: port %s, certificate %s", port, fp)
        accept_loop(sock, context)


def start() -> bool:
    """Start the listener thread when station.panel_link is wifi. True when it was started."""
    if st.settings["panel_link"] != "wifi":
        return False
    threading.Thread(target=serve, args=(st.settings["panel_port"],), daemon=True).start()
    return True
