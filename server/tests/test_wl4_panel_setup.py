"""WL4 (R88 section 4.4 and 4.5): the panel set-up functions, the keys and the drop-in, the Devices and Checks rows.
The panel is a stub: `send` takes a line and answers one."""
from __future__ import annotations

import json
import logging
import re

import pytest

from filament_kit import apply, config, status
from filament_kit.profiles import statexml
from filament_kit.station import panel_setup as ps

PASSWORD = "hunter2-wifi-pass"
SSID = "Workshop net"
FP = "ab" * 32
GOOD = dict(version="13", ssid=SSID, password=PASSWORD, address="192.168.1.20", port=5004, fingerprint=FP)


@pytest.fixture
def env(tmp_path, monkeypatch):
    etc, state = tmp_path / "etc", tmp_path / "station-state"
    etc.mkdir()
    state.mkdir()
    monkeypatch.setenv("FK_ETC", str(etc))
    monkeypatch.setattr(ps.st, "state_dir", lambda: state)    # not STATE_DIRECTORY: state_dir splits it at ":", a drive letter's too
    monkeypatch.setattr(statexml, "_unreadable", {})
    return etc, state


class Panel:
    def __init__(self, reply=ps.REPLY_OK):
        self.lines, self.reply = [], reply     # reply: one line for every call, or a list of lines read in turn

    def __call__(self, line):
        if line is not None:
            self.lines.append(line)
        if isinstance(self.reply, Exception):
            raise self.reply
        if isinstance(self.reply, list):
            return self.reply.pop(0) if self.reply else ""
        return self.reply


def _tree_text(*folders):
    return "".join(p.read_bytes().decode("utf-8", "replace") for f in folders for p in f.rglob("*") if p.is_file())


# ---- the keys and the drop-in ----

def test_the_keys_have_their_defaults():
    st = config.DEFAULTS["station"]
    assert st["panel_link"] == "usb" and st["panel_port"] == 5004
    assert config.panel_link({}) == "usb" and config.panel_port({}) == 5004
    assert config.panel_link({"station": {"panel_link": "wifi"}}) == "wifi"
    assert config.panel_link({"station": {"panel_link": "carrier pigeon"}}) == "usb"
    assert config.panel_port({"station": {"panel_port": 70000}}) == 5004


def _dropin(**st):
    cfg = json.loads(json.dumps(config.DEFAULTS))
    cfg["station"].update(st)
    return apply.station_dropin(cfg, ["127.0.0.1"])


def test_the_drop_in_carries_the_link_and_the_port():
    text = _dropin()
    assert "STATION_PANEL_LINK=usb" in text and "STATION_PANEL_PORT=5004" in text
    text = _dropin(panel_link="wifi", panel_port=5010)
    assert "STATION_PANEL_LINK=wifi" in text and "STATION_PANEL_PORT=5010" in text


def test_a_low_listener_port_gets_the_bind_capability_only_with_wifi():
    assert "CAP_NET_BIND_SERVICE" not in _dropin(panel_port=443)
    assert "CAP_NET_BIND_SERVICE" in _dropin(panel_link="wifi", panel_port=443)


@pytest.mark.parametrize("st", [{"panel_link": "bluetooth"}, {"panel_port": 0}, {"panel_port": "5004"}])
def test_a_bad_link_or_port_stops_the_apply(st):
    with pytest.raises(apply.ModuleError):
        _dropin(**st)


# ---- set up ----

def test_set_up_writes_the_two_lines_keeps_the_token_and_switches_to_wifi(env):
    etc, state = env
    panel = Panel()
    out = ps.enable(panel, **GOOD)
    assert out == {"panel_link": "wifi", "panel_port": 5004}
    token = ps.stored_token()
    assert re.fullmatch(r"[0-9a-f]{32}", token)
    assert panel.lines == [f"CFG WIFI {SSID.encode().hex()} {PASSWORD.encode().hex()}",
                           f"CFG LINK 192.168.1.20 5004 {FP} {token}"]
    cfg = config.load()
    assert cfg["station"]["panel_link"] == "wifi" and cfg["station"]["panel_port"] == 5004
    assert statexml.loads((etc / "config.xml").read_bytes(), "config")["station"]["panel_link"] == "wifi"
    assert json.loads((etc / "config.json").read_text(encoding="utf-8"))["station"]["panel_link"] == "wifi"
    # the new token is new each time
    ps.enable(Panel(), **GOOD)
    assert ps.stored_token() != token


def test_the_password_is_never_stored_or_logged(env, caplog):
    etc, state = env
    caplog.set_level(logging.DEBUG)
    ps.enable(Panel(), **GOOD)
    for form in (PASSWORD, PASSWORD.encode().hex()):
        assert form not in _tree_text(etc, state)
        assert form not in caplog.text
    with pytest.raises(ps.PanelSetupError) as e:
        ps.enable(Panel("ERR bad password " + PASSWORD), **GOOD)
    assert PASSWORD not in str(e.value) and PASSWORD not in caplog.text
    with pytest.raises(ps.PanelSetupError) as e:
        ps.enable(Panel(), **{**GOOD, "ssid": ""})
    assert PASSWORD not in str(e.value)


def test_a_panel_that_refuses_changes_nothing(env):
    with pytest.raises(ps.PanelSetupError, match="The panel did not accept the Wi-Fi settings."):
        ps.enable(Panel("ERR no"), **GOOD)
    assert ps.stored_token() is None and config.load()["station"]["panel_link"] == "usb"


def test_a_panel_that_does_not_answer_changes_nothing(env):
    with pytest.raises(ps.PanelSetupError, match="did not answer over USB"):
        ps.enable(Panel(OSError("gone")), **GOOD)
    assert ps.stored_token() is None and config.load()["station"]["panel_link"] == "usb"


@pytest.mark.parametrize("change", [{"ssid": ""}, {"ssid": "x" * 33}, {"password": "short"}, {"address": "a b"}, {"address": "h" * 65}, {"password": "p" * 64}, {"version": "12"}, {"version": None},
                                    {"port": 0}, {"port": 70000}, {"fingerprint": "12"}])
def test_values_the_panel_cannot_take_are_refused_before_anything_is_sent(env, change):
    panel = Panel()
    with pytest.raises(ps.PanelSetupError):
        ps.enable(panel, **{**GOOD, **change})
    assert panel.lines == [] and config.load()["station"]["panel_link"] == "usb"


def test_an_open_network_and_a_spaced_name_travel_as_hex(env):
    panel = Panel()
    ps.enable(panel, **{**GOOD, "password": ""})
    assert panel.lines[0] == f"CFG WIFI {SSID.encode().hex()} -"


def test_the_fingerprint_may_be_given_in_groups(env):
    panel = Panel()
    ps.enable(panel, **{**GOOD, "fingerprint": ":".join(["AB"] * 32)})
    assert f" {FP} " in panel.lines[1]


# ---- off ----

def test_off_goes_back_to_usb_and_removes_the_token(env):
    ps.enable(Panel(), **GOOD)
    panel = Panel()
    assert ps.disable(panel) == {"panel_link": "usb", "panel_forgotten": True}
    assert panel.lines == ["CFG CLEAR"]
    assert ps.stored_token() is None and config.load()["station"]["panel_link"] == "usb"


def test_off_with_no_panel_on_usb_still_switches(env):
    ps.enable(Panel(), **GOOD)
    assert ps.disable(None)["panel_forgotten"] is False
    assert ps.disable(Panel(OSError("gone")))["panel_forgotten"] is False
    assert ps.stored_token() is None and config.load()["station"]["panel_link"] == "usb"


# ---- the Devices and Checks rows ----

def _cfg(link, url="http://127.0.0.1:5003"):
    return {"station": {"url": url, "panel_link": link}}


def test_devices_says_how_the_panel_is_connected_from_the_stations_state():
    rows = list(ps.panel_rows(_cfg("wifi"), {"panel": "usb"}))        # the station's word, not the setting
    assert [(r.group, r.text, r.state) for r in rows] == [("Devices", "Panel: on USB", status.INFO)]
    rows = list(ps.panel_rows(_cfg("usb"), {"panel": "wifi"}))
    assert [(r.group, r.text) for r in rows] == [("Devices", "Panel: on Wi-Fi")]
    assert list(ps.panel_rows({}, None)) == []        # no Weigh Station: nothing to say
    assert list(ps.panel_rows(_cfg("wifi"), None)) == []       # a station that does not answer: nothing, not the setting
    assert not [r for r in ps.panel_rows(_cfg("usb"), {"panel": None}) if r.group == "Devices"]


def test_a_wifi_panel_that_is_not_there_needs_a_look():
    rows = list(ps.panel_rows(_cfg("wifi"), {"panel": None}))
    fail = [r for r in rows if r.state == status.FAIL]
    assert len(fail) == 1 and fail[0].text == "The Weigh Station's panel is not connected."
    assert fail[0].group == "Weigh Station" and fail[0].key == "weigh-station.panel" and "panel-wifi --off" in fail[0].fix


def test_no_checks_row_for_usb_a_present_panel_or_a_station_that_did_not_answer():
    assert not [r for r in ps.panel_rows(_cfg("usb"), {"panel": None}) if r.state == status.FAIL]
    assert not [r for r in ps.panel_rows(_cfg("wifi"), {"panel": "wifi"}) if r.state == status.FAIL]
    assert not [r for r in ps.panel_rows(_cfg("wifi"), None) if r.state == status.FAIL]


def test_the_devices_list_row_of_a_station_names_the_panel_link_from_its_state(env, monkeypatch):
    from filament_kit import station_client
    from filament_kit.setup_console import device_tab
    monkeypatch.setattr(device_tab, "_pairings", lambda: [{"id": "s1", "name": "Bench", "type": "station"}])
    monkeypatch.setattr(device_tab, "_readers", lambda: [])
    monkeypatch.setattr(station_client, "station_reach", lambda devs, url: {})
    cfg = {"station": {"url": "http://127.0.0.1:5003", "panel_link": "usb"}}
    monkeypatch.setattr(config, "load", lambda: cfg)
    monkeypatch.setattr(station_client, "station_panel", lambda url: "wifi")      # the setting says usb: the state wins
    row = device_tab.rows()[0]
    assert row["kind"] == "station" and row["panel"] == "Panel: on Wi-Fi"
    monkeypatch.setattr(station_client, "station_panel", lambda url: "usb")
    assert device_tab.rows()[0]["panel"] == "Panel: on USB"
    monkeypatch.setattr(station_client, "station_panel", lambda url: None)        # silent station or no panel
    assert "panel" not in device_tab.rows()[0]


def test_station_panel_reads_the_state_and_says_nothing_when_it_is_silent(monkeypatch):
    from filament_kit import station_client as sc
    monkeypatch.setattr(sc, "_panel_seen", {})
    answers = [{"panel": "wifi"}, sc.StationUnreachable("x")]

    class Fake:
        def __init__(self, *a, **k):
            pass

        def state(self):
            r = answers.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
    monkeypatch.setattr(sc, "StationClient", Fake)
    assert sc.station_panel("http://h") == "wifi"
    assert sc.station_panel("http://h") == "wifi"         # kept for a while: no second call
    monkeypatch.setattr(sc, "_panel_seen", {})
    assert sc.station_panel("http://h") is None and sc.station_panel(None) is None


# ---- R614: the panel's port, a damaged token file, CRLF lines, the unit's allow lines ----

def test_the_panel_port_must_differ_from_the_stations_own(env):
    panel = Panel()
    with pytest.raises(ps.PanelSetupError) as e:
        ps.enable(panel, **{**GOOD, "port": 5003})
    assert str(e.value) == "The panel's port must differ from the Weigh Station's own port (5003)."
    assert panel.lines == [] and ps.stored_token() is None
    with pytest.raises(apply.ModuleError, match="panel_port"):
        _dropin(panel_link="wifi", panel_port=5003)


def test_a_damaged_token_file_is_kept_aside_and_noted_never_overwritten(env):
    _, state = env
    (state / ps.TOKEN_FILE).write_text("this is not xml", encoding="utf-8")
    assert ps.stored_token() is None and any(ps.TOKEN_FILE in p for p in statexml.problems())
    statexml._unreadable.clear()
    ps.enable(Panel(), **GOOD)
    kept = state / (ps.TOKEN_FILE + ".unreadable")
    assert kept.read_text(encoding="utf-8") == "this is not xml"
    assert ps.stored_token() and any(".unreadable" in p for p in statexml.problems())


def test_a_panel_whose_lines_end_in_crlf_is_understood(env):
    panel = Panel()
    panel.reply = ["AIR: 1\r", "P H\r", "OK\r", "OK\r"]
    ps.enable(panel, **GOOD)
    assert panel.reply == []
    with pytest.raises(ps.PanelSetupError, match="did not accept"):
        ps.enable(Panel("ERR long\r"), **GOOD)


def test_the_sentences_are_the_catalogues_with_the_english_as_the_fallback():
    assert ps.TEXT_TIMEOUT == "The panel did not answer in time."
    assert ps.TEXT_SSID == "The Wi-Fi name must be 1 to 32 characters."


def test_the_unit_admits_the_home_ranges_only_while_the_link_is_wifi():
    usb = _dropin(page_from="allowed")
    assert "192.168.0.0/16" not in usb
    wifi = _dropin(page_from="allowed", panel_link="wifi")
    allow = [l for l in wifi.splitlines() if l.startswith("IPAddressAllow=")][0]
    for r in apply.HOME_RANGES:
        assert r in allow
    assert allow.count("192.168.0.0/16") == 1
    home = _dropin(panel_link="wifi")                    # page_from=home already holds them: no duplicate
    assert [l for l in home.splitlines() if l.startswith("IPAddressAllow=")][0].count("10.0.0.0/8") == 1
    assert _dropin(page_from="allowed", panel_link="usb") == usb          # --off: back to the first text


def test_a_settings_rewrite_keeps_the_allow_lines(env):
    ps.enable(Panel(), **GOOD)
    cfg = config.load()
    first = apply.station_dropin(cfg, ["127.0.0.1"])
    cfg["station"]["beep"] = not cfg["station"]["beep"]           # an unrelated settings rewrite, then the next apply
    config.save(cfg)
    again = apply.station_dropin(config.load(), ["127.0.0.1"])
    assert "192.168.0.0/16" in again and "STATION_PANEL_LINK=wifi" in again
    assert [l for l in first.splitlines() if l.startswith("IPAddressAllow=")] == \
           [l for l in again.splitlines() if l.startswith("IPAddressAllow=")]
    ps.disable(None)
    assert "STATION_PANEL_LINK=usb" in apply.station_dropin(config.load(), ["127.0.0.1"])
