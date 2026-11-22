"""The two halves of the panel's Wi-Fi link together (R88 section 4): `panel_setup` (WL4) writes the token, `panel_net`
(WL1) reads it, and the Devices and Checks rows read what the station's /state says. The fake panel is WL1's."""
from __future__ import annotations

import re

import pytest

from filament_kit import config, status
from filament_kit.station import panel_net, panel_setup as ps
from filament_kit.station import state as st
from test_wl1_panel_link import FakePanel, connect, link, net, station  # noqa: F401  (the fixture is autouse there)

WIN = "C:" + chr(92) + "state"      # a Windows drive path
SETUP = dict(ssid="Workshop net", password="hunter2-wifi-pass", address="127.0.0.1", port=5004)


@pytest.fixture(autouse=True)
def etc(tmp_path, monkeypatch):
    (tmp_path / "etc").mkdir()
    monkeypatch.setenv("FK_ETC", str(tmp_path / "etc"))


def set_up():
    """`panel-wifi` against a stub panel over USB: returns the token that went down the cable."""
    lines = []

    def send(line):
        lines.append(line)
        return ps.REPLY_OK

    ps.enable(send, version="13", fingerprint=net.fp, **SETUP)
    return re.fullmatch(r"CFG LINK \S+ \d+ [0-9a-f]{64} ([0-9a-f]{32})", lines[1]).group(1)


def rows():
    cfg = config.load()
    return [(r.group, r.text, r.state) for r in ps.panel_rows(cfg, st.view(), has_station=True)]


def test_the_token_set_up_writes_is_the_one_the_station_accepts():
    token = set_up()
    assert panel_net.read_token() == token and ps.stored_token() == token
    p = connect(token=token)
    assert link() is not None and st.view()["panel"] == "wifi"
    assert p.sock is not None


def test_the_rows_follow_the_state():
    token = set_up()
    assert ("Weigh Station", "The Weigh Station's panel is not connected.", status.FAIL) in rows()
    connect(token=token)
    assert rows() == [("Devices", "Panel: on Wi-Fi", status.INFO)]


def test_a_wrong_token_is_refused_and_a_new_set_up_retires_the_old_token():
    old = set_up()
    new = set_up()
    assert new != old
    p = FakePanel(net.port).hello(token=old)
    assert p.line() == b"REFUSED token" and link() is None and st.view()["panel"] is None
    connect(token=new)


def test_off_removes_the_token_and_the_panel_is_refused():
    token = set_up()
    ps.disable(None)
    assert panel_net.read_token() is None and not (ps.token_path()).exists()
    p = FakePanel(net.port).hello(token=token)
    assert p.line() == b"REFUSED token" and link() is None
    assert config.load()["station"]["panel_link"] == "usb"
    assert rows() == []                       # no panel on the station: no Devices row (and no FAIL row: the link is usb)


@pytest.mark.parametrize("value, folder", [
    ("/var/lib/x", "/var/lib/x"),
    ("/var/lib/x:/var/lib/y", "/var/lib/x"),
    (WIN, WIN),
    ("C:/state:D:/other", "C:/state"),
    (WIN + ":/var/lib/y", WIN),
])
def test_the_state_folder_takes_a_drive_path_and_still_splits_a_list(monkeypatch, value, folder):
    monkeypatch.setenv("STATE_DIRECTORY", value)
    assert str(st.state_dir()) == str(type(st.state_dir())(folder))


# ---- state["panel_scale"]: what the panel's W lines said (for the flow, WL3) ----

def test_panel_scale_is_none_until_the_panel_sends_a_w_line():
    assert st.state["panel_scale"] is None
    p = connect()
    assert st.state["panel_scale"] is None


def test_a_w_line_sets_panel_scale_and_a_drop_clears_it():
    from test_wl1_panel_link import wait
    p = connect()
    p.send("W 812.5 1")
    assert wait(lambda: st.state["panel_scale"] is not None)
    assert st.state["panel_scale"] == {"grams": 812.5, "status": "settled", "unit": "g"}
    p.send("W 790.0 0")
    assert wait(lambda: st.state["panel_scale"]["status"] == "unsettled")
    assert st.state["panel_scale"] == {"grams": 790.0, "status": "unsettled", "unit": "g"}
    p.close()
    assert wait(lambda: link() is None and st.state["panel_scale"] is None, timeout=10)


@pytest.mark.parametrize("line", [b"W", b"W 5", b"W abc 1", b"W 12 2", b"W 12 1 9", b"W nan 1", b"W 99999999 1"])
def test_a_malformed_w_line_leaves_panel_scale_alone(line):
    from filament_kit.station import panel
    panel.on_line(line)
    assert st.state["panel_scale"] is None
    panel.on_line(b"W 20.0 1")
    panel.on_line(line)
    assert st.state["panel_scale"] == {"grams": 20.0, "status": "settled", "unit": "g"}


def test_the_write_is_not_held_up_while_a_read_waits_and_no_delay_is_set():
    import socket as _socket
    import time as _time
    import threading as _threading
    connect()
    ln = link()
    assert ln.sock.getsockopt(_socket.IPPROTO_TCP, _socket.TCP_NODELAY) != 0
    reader = _threading.Thread(target=lambda: ln.read_line(2.0), daemon=True)
    reader.start()
    _time.sleep(0.2)
    t0 = _time.time()
    ln.write(b"L 0 0"+bytes([10]))
    assert _time.time() - t0 < 0.5
    reader.join(5)


def test_a_usb_search_that_finds_nothing_keeps_the_facts_of_a_wifi_panel(monkeypatch):
    from filament_kit.station import panel, speech
    connect()
    assert (st.ports["panel_version"], st.ports["panel_chip"]) == ("13", "esp32c3")
    st.ports["panel_nfc"] = "pn532"
    monkeypatch.setattr(st, "panel_ttys", lambda: [])
    assert panel.find_panel() is None
    assert (st.ports["panel_version"], st.ports["panel_chip"], st.ports["panel_nfc"]) == ("13", "esp32c3", "pn532")
    heard = []
    monkeypatch.setattr(panel, "send_volume", lambda: heard.append(1))
    speech.apply_voice({"volume": 50})                       # speech.py: the C3 panel is told its volume
    assert heard == [1] and st.ports["panel_chip"] == "esp32c3"   # (and the ESP32 audio format is chosen from the chip)


def test_a_usb_search_with_no_panel_at_all_still_clears_the_facts(monkeypatch):
    from filament_kit.station import panel
    st.ports["panel_version"], st.ports["panel_chip"], st.ports["panel_nfc"] = "13", "esp32c3", "pn532"
    monkeypatch.setattr(st, "panel_ttys", lambda: [])
    assert panel.find_panel() is None
    assert (st.ports["panel_version"], st.ports["panel_chip"], st.ports["panel_nfc"]) == (None, None, None)


# ---- the Hub's voice call end to end: the sender (VO2) against the station's real handler (VO1) ----

from test_vo1_station_voice import fresh, hub  # noqa: E402,F401  (a real station HTTP handler allowing 127.0.0.1)


@pytest.fixture
def sender(hub, monkeypatch):
    """`station_client`'s real request building, the network replaced by a call to the real handler. The two sides share
    the pairing key; the station's clock follows the real time because the sender stamps its proofs with it."""
    import time as _time
    from filament_kit import station_client
    sent = []
    hub.clock.t = _time.time()
    monkeypatch.setattr(station_client, "station_endpoint", lambda url: ("127.0.0.1", hub.port, "ab" * 32))
    monkeypatch.setattr(station_client, "proof_key", lambda: hub.key)

    def bounded(host, port, fp, method, path, body=None, headers=None, cap=0, timeout=0, **kw):
        sent.append((method, path, dict(headers or {}), body))
        status, _, payload = hub.call(method, path, headers=headers, raw=body)
        return status, "application/json", payload
    monkeypatch.setattr(station_client, "bounded_call", bounded)
    return station_client, sent


def test_the_station_accepts_the_senders_voice_call(sender):
    client, sent = sender
    client.send_voice("http://h", {"speed": 120, "volume": 40})
    assert st.live["speed"] == 120 and st.live["volume"] == 40
    assert sent[0][1] == "/voice"


def test_the_station_refuses_a_tampered_body_or_the_settings_purpose(hub, sender):
    from filament_kit.station import page
    client, sent = sender
    client.send_voice("http://h", {"speed": 120})
    _, path, headers, body = sent[0]
    st.live.clear()
    status, _, _ = hub.call("POST", path, headers=headers, raw=body.replace(b"120", b"200"))
    assert status in (401, 403) and "speed" not in st.live
    other = {page.PROOF_HEADER: page.make_proof(hub.key, "settings", body, now=hub.clock())}
    status, _, _ = hub.call("POST", path, headers=other, raw=body)
    assert status in (401, 403) and "speed" not in st.live
    voice = {page.PROOF_HEADER: page.make_proof(hub.key, "voice", body, now=hub.clock())}   # control: the right purpose
    assert hub.call("POST", path, headers=voice, raw=body)[0] == 200 and st.live["speed"] == 120


# ---- the page's gate stays with the handler when the unit lets the home ranges through for a Wi-Fi panel ----

def _page_server(allow, page_from, ranges):
    import http.client
    import threading as _threading
    from http.server import ThreadingHTTPServer
    from filament_kit.station import http as station_http
    from test_ev3_station_page import view
    handler = station_http.make_handler(view, allow, page_ranges=" ".join(ranges), page_from=page_from)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    _threading.Thread(target=server.serve_forever, daemon=True).start()

    def get(path):
        c = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        try:
            c.request("GET", path)
            r = c.getresponse()
            r.read()
            return r.status
        finally:
            c.close()
    return server, get


@pytest.mark.parametrize("page_from", ["allowed", "home", "subnets"])   # the allow-list only / home ranges / own networks
def test_the_page_listener_keeps_page_from_whatever_the_unit_lets_through(page_from):
    from filament_kit import apply
    from filament_kit.station import guard
    cfg = json.loads(json.dumps(config.DEFAULTS))
    cfg["station"].update(page_from=page_from, panel_link="wifi")
    unit = apply.station_dropin(cfg, ["127.0.0.1"], subnets=lambda: ["10.20.30.0/24"])
    allow_line = [l for l in unit.splitlines() if l.startswith("IPAddressAllow=")][0]
    assert "192.168.0.0/16" in allow_line                      # the unit's door is open to the home ranges either way
    _, ranges = apply.page_ranges(cfg, lambda: ["10.20.30.0/24"])     # what the service is handed as STATION_PAGE_RANGES
    nets = guard.page_networks(" ".join(ranges))
    listed = {"203.0.113.9"}
    assert guard.caller_class("192.168.1.50", listed, nets) == ("home" if page_from == "home" else "outside")
    assert guard.caller_class("10.20.30.7", listed, nets) == ("outside" if page_from == "allowed" else "home")
    assert guard.caller_class("203.0.113.9", listed, nets) == "listed"


def test_with_wifi_on_and_page_from_allowed_the_page_refuses_a_home_address_and_the_panel_port_still_takes_the_token():
    from filament_kit import apply
    cfg = json.loads(json.dumps(config.DEFAULTS))
    cfg["station"].update(page_from="allowed", panel_link="wifi")
    _, ranges = apply.page_ranges(cfg)
    assert ranges == []                                        # STATION_PAGE_RANGES is empty: no address is a home address
    server, get = _page_server({"203.0.113.9"}, "allowed", ranges)     # the caller (loopback) is off the allow-list
    try:
        assert get("/state") == 403 and get("/") == 403       # refused before anything else, the page's files too
        listed_server, get2 = _page_server({"127.0.0.1"}, "allowed", ranges)
        try:
            assert get2("/") == 200                            # control: the same request from the allow-list is let in
        finally:
            listed_server.shutdown()
            listed_server.server_close()
    finally:
        server.shutdown()
        server.server_close()
    connect(token=ps.stored_token() or set_up())              # the panel port's gate is the token and the pin
    assert link() is not None
