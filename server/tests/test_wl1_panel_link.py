"""WL1 (R88 section 4): the panel over Wi-Fi. A fake panel dials the station's real TLS listener (station/panel_net.py):
the token, the same protocol as on USB, USB wins, the keep-alive, `W` lines for the scale, and everything that must not
take the station down (wrong token, bad hello, long lines, a drop and a return, a second panel)."""
from __future__ import annotations

import hashlib
import logging
import socket
import ssl
import threading
import time
import types

import pytest

from filament_kit.station import flow, http, nfc, panel, panel_net, panel_setup, wire
from filament_kit.station import state as st

TOKEN = "k3y-0123456789abcdef"


class FakePanel:
    """The panel's side: TLS client without verification (the pin is checked in its own test), lines in and out."""

    def __init__(self, port):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        self.sock = ctx.wrap_socket(socket.create_connection(("127.0.0.1", port), timeout=5))
        self.buf = b""
        net.clients.append(self)

    def send(self, line):
        self.sock.sendall(line if isinstance(line, bytes) else line.encode() + b"\n")

    def hello(self, token=TOKEN, version="13", chip="esp32c3"):
        self.send(f"HELLO intake-panel {version} {chip} {token}")
        return self

    def line(self, timeout=3):
        """The next line, or None when the station closed the connection (or nothing came in time)."""
        end = time.time() + timeout
        while b"\n" not in self.buf:
            self.sock.settimeout(max(0.05, end - time.time()))
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                return None
            except OSError:
                data = b""
            if not data:
                return None
            self.buf += data
        out, _, self.buf = self.buf.partition(b"\n")
        return out

    def until(self, prefix, timeout=3):
        end = time.time() + timeout
        while time.time() < end:
            got = self.line(max(0.05, end - time.time()))
            if got is None:
                return None
            if got.startswith(prefix):
                return got
        return None

    def closed(self, timeout=3):
        """The station closed the connection (lines still on the way are read past)."""
        end = time.time() + timeout
        while time.time() < end:
            self.sock.settimeout(max(0.05, end - time.time()))
            try:
                if not self.sock.recv(4096):
                    return True
            except socket.timeout:
                return False
            except OSError:
                return True
        return False

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Net:
    def __init__(self):
        self.clients, self.port, self.fp = [], 0, ""


net = Net()


def wait(cond, timeout=3):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def usb_panel():
    """What panel_loop keeps in st.panel["ser"] for a panel on USB."""
    return types.SimpleNamespace(port="/dev/ttyACM0", panel_version="11", panel_chip="esp32c3", write=lambda data: None)


@pytest.fixture(autouse=True)
def station(tmp_path, monkeypatch):
    """A listener on a free port with the station's own certificate and a stored token; no panel, no scale reading."""
    monkeypatch.setenv("STATE_DIRECTORY", str(tmp_path))
    panel_setup.store_token(TOKEN)                                # the file `panel-wifi` writes: the one source
    cert, key, fp = st.ensure_cert(tmp_path / "tls")
    listener = panel_net.listen(0)
    net.port, net.fp, net.clients = listener.getsockname()[1], fp, []
    threading.Thread(target=panel_net.accept_loop, args=(listener, http.tls_context(cert, key)), daemon=True).start()
    monkeypatch.setattr(panel_net, "REFUSE_DELAY", 0)
    monkeypatch.setattr(panel_net, "_waiting", threading.BoundedSemaphore(panel_net.MAX_WAITING))
    monkeypatch.setitem(st.panel, "ser", None)
    monkeypatch.setitem(st.settings, "panel_swap", False)
    for key_ in ("panel", "panel_version", "panel_chip", "panel_nfc"):
        monkeypatch.setitem(st.ports, key_, None)
    saved = dict(st.state)
    monkeypatch.setattr(panel.scale, "find_scale", lambda: None)
    st.panel_scale["on"] = False
    yield
    with st.panel_lock:
        st.panel["ser"] = None
    for c in net.clients:
        c.close()
    listener.close()
    st.state.clear()
    st.state.update(saved)
    st.panel_scale["on"] = False


def connect(**kw):
    """A panel that said HELLO and was taken: its first lamp line came back."""
    p = FakePanel(net.port).hello(**kw)
    assert p.until(b"L ") is not None
    return p


def link():
    return st.panel["ser"]


# (a) the settings and the listener


def test_the_listener_is_off_unless_the_link_is_wifi(monkeypatch):
    started = []
    monkeypatch.setattr(panel_net, "serve", lambda port: started.append(port))
    monkeypatch.setitem(st.settings, "panel_link", "usb")
    assert panel_net.start() is False
    monkeypatch.setitem(st.settings, "panel_link", "wifi")
    monkeypatch.setitem(st.settings, "panel_port", 5004)
    assert panel_net.start() is True and wait(lambda: started == [5004])


def test_the_settings_come_from_the_drop_in(monkeypatch):
    for name in ("STATION_PANEL_LINK", "STATION_PANEL_PORT"):
        monkeypatch.delenv(name, raising=False)
    s = st.settings_from_env()
    assert (s["panel_link"], s["panel_port"]) == ("usb", 5004)
    monkeypatch.setenv("STATION_PANEL_LINK", "wifi")
    monkeypatch.setenv("STATION_PANEL_PORT", "6000")
    s = st.settings_from_env()
    assert (s["panel_link"], s["panel_port"]) == ("wifi", 6000)
    monkeypatch.setenv("STATION_PANEL_LINK", "bluetooth")      # anything else is usb
    monkeypatch.setenv("STATION_PANEL_PORT", "99999")
    s = st.settings_from_env()
    assert (s["panel_link"], s["panel_port"]) == ("usb", 65535)


def test_the_panel_loop_starts_the_listener(monkeypatch):
    started = []

    class Stop(Exception):
        pass

    def find():
        raise Stop

    monkeypatch.setattr(panel.panel_net, "start", lambda: started.append(1))
    monkeypatch.setattr(panel, "find_panel", find)
    with pytest.raises(Stop):
        panel.panel_loop()
    assert started == [1]


def test_the_stations_own_certificate_is_the_one_the_panel_pins():
    p = FakePanel(net.port)
    assert hashlib.sha256(p.sock.getpeercert(True)).hexdigest() == net.fp


# (b) the hello and the protocol over the connection


def test_a_panel_with_the_token_is_taken_and_gets_the_lamps():
    p = FakePanel(net.port).hello(version="13")
    assert p.until(b"V 50") is not None            # a C3 panel is told the volume
    assert p.until(b"L ") is not None
    assert wait(lambda: link() is not None)
    assert link().wifi and link().panel_version == "13" and link().panel_chip == "esp32c3"
    assert st.ports["panel_version"] == "13" and st.view()["panel"] == "wifi"
    assert st.view()["panel_version"] == "13"


def test_the_panels_lines_reach_the_flow_and_the_station_writes_back(monkeypatch):
    calls, tags = [], []
    monkeypatch.setattr(flow, "on_yellow", lambda long: calls.append(("yellow", long)))
    monkeypatch.setattr(flow, "on_red", lambda: calls.append(("red",)))
    monkeypatch.setattr(nfc, "on_identifier", lambda uid: tags.append(uid))
    p = connect()
    for line in ("P H", "L H", "P D", "TAG 04a1b2c3"):
        p.send(line)
    assert wait(lambda: calls == [("yellow", False), ("yellow", True), ("red",)] and tags == [bytes.fromhex("04a1b2c3")])
    assert wire.panel_send("B 100 50") is True       # the buzzer, speech frames: the one write path
    assert p.until(b"B 100 50") == b"B 100 50"
    assert wire.panel_send(b"A 3\nabc") is True
    assert p.until(b"A 3") == b"A 3"


def test_the_token_is_compared_in_constant_time(monkeypatch):
    seen = []
    real = panel_net.hmac.compare_digest
    monkeypatch.setattr(panel_net.hmac, "compare_digest", lambda a, b: seen.append((a, b)) or real(a, b))
    connect()
    assert seen == [(TOKEN.encode(), TOKEN.encode())]


def test_the_token_reader(tmp_path):
    assert panel_net.read_token() == TOKEN
    panel_setup.store_token("   ")
    assert panel_net.read_token() is None
    (tmp_path / panel_setup.TOKEN_FILE).write_text("not xml")
    assert panel_net.read_token() is None
    panel_setup.forget_token()
    assert panel_net.read_token() is None
    assert not hasattr(panel_net, "TOKEN_FILE")                   # no second convention (`panel-token`)


# (f) what must not take the station down


@pytest.mark.parametrize("token", ["bad-one", TOKEN[:-1], TOKEN + "x", "K3Y-0123456789ABCDEF"])
def test_a_wrong_token_is_refused_and_logged_without_the_token(token, caplog):
    caplog.set_level(logging.INFO, logger="intake-station")
    p = FakePanel(net.port).hello(token=token)
    assert p.line() == b"REFUSED token"
    assert p.closed() and link() is None and st.view()["panel"] is None
    assert token not in caplog.text and TOKEN not in caplog.text
    connect()                                      # the station still takes the right one


def test_no_stored_token_refuses_every_panel(tmp_path):
    panel_setup.forget_token()
    p = FakePanel(net.port).hello(token="anything")
    assert p.line() == b"REFUSED token" and link() is None


@pytest.mark.parametrize("first", [
    "HELLO intake-panel 12 esp32c3",                 # no token
    "HELLO intake-panel 12 esp32c3 " + TOKEN + " extra",
    "HELLO other-board 12 esp32c3 " + TOKEN,
    "hello intake-panel 12 esp32c3 " + TOKEN,
    "P H",
    "",
    "\x00\x01\x02",
])
def test_a_malformed_hello_is_refused(first):
    p = FakePanel(net.port)
    p.send(first)
    assert p.line() == b"REFUSED hello"
    assert p.closed() and link() is None
    connect()


def test_an_oversize_hello_is_refused():
    p = FakePanel(net.port)
    p.send(b"HELLO intake-panel 12 esp32c3 " + b"x" * 400 + b"\n")
    assert p.line() == b"REFUSED hello" and link() is None
    p = FakePanel(net.port)
    p.send(b"x" * 400)                               # no end of line at all
    assert p.line() == b"REFUSED hello" and link() is None
    connect()


def test_an_oversize_line_after_the_hello_ends_the_link_and_the_station_takes_the_next_panel():
    p = connect()
    p.send(b"x" * 400 + b"\n")
    assert wait(lambda: link() is None) and p.closed() and st.view()["panel"] is None
    connect()


def test_a_connection_that_is_not_tls_or_never_speaks_is_dropped(monkeypatch):
    monkeypatch.setattr(panel_net, "HELLO_SECONDS", 0.5)
    raw = socket.create_connection(("127.0.0.1", net.port), timeout=5)
    raw.sendall(b"GET / HTTP/1.0\r\n\r\n")
    raw.settimeout(3)
    try:
        raw.recv(100)                                # a TLS alert or a close; either way it is over
    except OSError:
        pass
    raw.close()
    silent = FakePanel(net.port)                     # TLS, then nothing: freed after HELLO_SECONDS
    assert silent.closed(3)
    assert link() is None
    connect()


def test_too_many_waiting_connections_are_closed_at_once(monkeypatch):
    monkeypatch.setattr(panel_net, "_waiting", threading.BoundedSemaphore(1))
    holder = FakePanel(net.port)                     # in its hello, holding the one place
    time.sleep(0.2)
    with pytest.raises(OSError):
        FakePanel(net.port)
    holder.hello()
    assert holder.until(b"L ") is not None


def test_a_panel_that_drops_and_returns_is_taken_again():
    a = connect()
    assert wait(lambda: link() is not None)
    a.close()
    assert wait(lambda: link() is None and st.ports["panel_version"] is None) and st.view()["panel"] is None
    b = connect(version="13")
    assert link().panel_version == "13" and st.view()["panel"] == "wifi"
    assert b.until(b"L ") is not None


def test_a_panel_dialling_in_from_the_same_address_replaces_its_dead_link():
    a = connect()
    first = link()
    b = connect(version="13")                        # a never noticed the drop: same address, still looks alive
    assert wait(lambda: link() is not first and link() is not None)
    assert link().panel_version == "13" and a.closed()
    assert st.ports["panel_version"] == "13"         # the old session's end does not clear the new one's state


def test_a_second_panel_from_another_address_is_refused_while_the_first_is_heard(monkeypatch):
    closed = []
    other = panel_net.Link(types.SimpleNamespace(settimeout=lambda t: None, setsockopt=lambda *a: None,
                                                  close=lambda: closed.append(1)),
                           "10.9.9.9", "13", "esp32c3")
    monkeypatch.setitem(st.panel, "ser", other)
    p = FakePanel(net.port).hello()
    assert p.line() == b"REFUSED busy" and link() is other and not closed
    other.heard = time.time() - 10                   # silent for good: the newcomer takes over
    connect()
    assert link() is not other and closed == [1]


def test_a_panel_on_usb_wins():
    usb = usb_panel()
    st.panel["ser"] = usb
    p = FakePanel(net.port).hello()
    assert p.line() == b"REFUSED usb" and p.closed()
    assert link() is usb and st.view()["panel"] == "usb"


def test_a_panel_found_on_usb_later_takes_over_from_the_wifi_one():
    p = connect()
    usb = usb_panel()
    with st.panel_lock:
        st.panel["ser"] = usb
    st.ports["panel_version"] = "11"
    assert p.closed() is True
    time.sleep(0.5)
    assert link() is usb and st.ports["panel_version"] == "11" and st.view()["panel"] == "usb"


# (c) the keep-alive


def test_the_station_sends_the_lamps_every_second(monkeypatch):
    p = connect()
    start = time.time()
    count = 0
    while time.time() - start < 2.6:
        if p.until(b"L ", 1.5):
            count += 1
    assert count >= 3


def test_five_seconds_without_a_line_and_the_service_counts_the_panel_as_gone(monkeypatch):
    assert panel_net.KEEPALIVE_SECONDS == 5
    monkeypatch.setattr(panel_net, "KEEPALIVE_SECONDS", 0.6)
    p = connect()
    assert wait(lambda: link() is not None)
    assert wait(lambda: link() is None, 3) and p.closed() and st.view()["panel"] is None


def test_a_panel_that_keeps_talking_stays(monkeypatch):
    monkeypatch.setattr(panel_net, "KEEPALIVE_SECONDS", 0.6)
    p = connect()
    end = time.time() + 1.8
    while time.time() < end:
        p.send("K")
        time.sleep(0.2)
    assert link() is not None and st.view()["panel"] == "wifi"


# (d) W lines


def test_a_w_line_is_the_scale_when_the_computer_has_none():
    p = connect()
    p.send("W 812.5 1")
    assert wait(lambda: st.state["scale"] is not None)
    assert st.state["scale"] == {"grams": 812.5, "status": "stable", "unit": 2, "stable": True}
    assert st.state["last"] == 812.5 and st.state["scale_at"] is not None and st.panel_scale["on"]
    p.send("W 790.0 0")
    assert wait(lambda: st.state["scale"]["status"] == "moving")
    assert st.state["scale"]["stable"] is False and st.state["last"] == 812.5   # an unsettled reading is no weight


def test_a_w_line_under_the_minimum_is_not_a_weight():
    before = st.state["last"]
    panel.on_line(b"W 5.0 1")
    assert st.state["scale"]["grams"] == 5.0 and st.state["last"] == before


@pytest.mark.parametrize("line", [b"W", b"W 5", b"W abc 1", b"W 12 2", b"W 12 1 9", b"W nan 1", b"W inf 1",
                                  b"W 99999999 1", b"W -- 1"])
def test_a_malformed_w_line_is_ignored(line):
    st.state.update(scale=None, scale_at=None)
    panel.on_line(line)
    assert st.state["scale"] is None and not st.panel_scale["on"]


def test_the_computers_own_scale_is_not_replaced(monkeypatch):
    monkeypatch.setattr(panel.scale, "find_scale", lambda: "/dev/hidraw0")
    own = {"grams": 40.0, "status": "stable", "unit": 2, "stable": True}
    st.state.update(scale=own, scale_at=1.0)
    panel.on_line(b"W 812.5 1")
    assert st.state["scale"] == own and st.state["scale_at"] == 1.0 and not st.panel_scale["on"]


def test_a_scale_reading_from_the_panel_goes_when_the_panel_goes():
    p = connect()
    p.send("W 300 1")
    assert wait(lambda: st.panel_scale["on"])
    p.close()
    assert wait(lambda: link() is None) and wait(lambda: st.state["scale"] is None) and not st.panel_scale["on"]


def test_the_computers_reading_is_kept_when_a_panel_goes():
    own = {"grams": 40.0, "status": "stable", "unit": 2, "stable": True}
    st.state.update(scale=own, scale_at=1.0)
    p = connect()
    p.close()
    assert wait(lambda: link() is None)
    assert st.state["scale"] == own


# (e) /state


def test_the_state_says_how_the_panel_is_connected():
    assert st.view()["panel"] is None
    st.panel["ser"] = usb_panel()
    assert st.view()["panel"] == "usb"
    st.panel["ser"] = None
    connect()
    assert st.view()["panel"] == "wifi"
