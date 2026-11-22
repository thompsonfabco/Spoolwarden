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


def test_devices_says_how_the_panel_is_connected():
    rows = list(ps.panel_rows(_cfg("usb"), {"panel": "/dev/ttyACM0"}))
    assert [(r.group, r.text, r.state) for r in rows] == [("Devices", "Panel: on USB", status.INFO)]
    rows = list(ps.panel_rows(_cfg("wifi"), {"panel": "wifi"}))
    assert [(r.group, r.text) for r in rows] == [("Devices", "Panel: on Wi-Fi")]
    assert list(ps.panel_rows({}, None)) == []        # no Weigh Station: nothing to say


def test_a_wifi_panel_that_is_not_there_needs_a_look():
    rows = list(ps.panel_rows(_cfg("wifi"), {"panel": None}))
    fail = [r for r in rows if r.state == status.FAIL]
    assert len(fail) == 1 and fail[0].text == "The Weigh Station's panel is not connected."
    assert fail[0].group == "Weigh Station" and fail[0].key == "weigh-station.panel" and "panel-wifi --off" in fail[0].fix


def test_no_checks_row_for_usb_a_present_panel_or_a_station_that_did_not_answer():
    assert not [r for r in ps.panel_rows(_cfg("usb"), {"panel": None}) if r.state == status.FAIL]
    assert not [r for r in ps.panel_rows(_cfg("wifi"), {"panel": "wifi"}) if r.state == status.FAIL]
    assert not [r for r in ps.panel_rows(_cfg("wifi"), None) if r.state == status.FAIL]


def test_the_devices_list_row_of_a_station_names_the_panel_link(env, monkeypatch):
    from filament_kit import station_client
    from filament_kit.setup_console import device_tab
    monkeypatch.setattr(device_tab, "_pairings", lambda: [{"id": "s1", "name": "Bench", "type": "station"}])
    monkeypatch.setattr(device_tab, "_readers", lambda: [])
    monkeypatch.setattr(station_client, "station_reach", lambda devs, url: {})
    row = device_tab.rows()[0]
    assert row["kind"] == "station" and row["panel"] == "Panel: on USB"
    ps._set_link("wifi")
    assert device_tab.rows()[0]["panel"] == "Panel: on Wi-Fi"


# ---- the answers, the version, the caps (R88 03:10) ----

def test_unasked_lines_before_the_answer_are_skipped(env):
    panel = Panel()
    panel.reply = ["AIR: 1", "P H", "L 0 0", "TAG 04aa", "NFC ok", "HELLO intake-panel 13 esp32c3", "OK", "AIR: 2", "OK"]
    ps.enable(panel, **GOOD)
    assert len(panel.lines) == 2 and panel.reply == []


def test_an_err_answer_has_its_own_sentence_that_repeats_nothing(env):
    with pytest.raises(ps.PanelSetupError) as e:
        ps.enable(Panel("ERR bad password " + PASSWORD), **GOOD)
    assert str(e.value) == "The panel did not accept the Wi-Fi settings."
    assert ps.stored_token() is None


def test_a_silent_panel_times_out_with_its_own_sentence(env):
    with pytest.raises(ps.PanelSetupError) as e:
        ps.enable(Panel(""), **GOOD)
    assert str(e.value) == "The panel did not answer in time."
    with pytest.raises(ps.PanelSetupError) as e:       # only unasked lines, never an answer
        ps.enable(Panel(["AIR: 1"]), **GOOD)
    assert str(e.value) == "The panel did not answer in time."
    assert ps.stored_token() is None and config.load()["station"]["panel_link"] == "usb"


def test_an_old_firmware_is_refused_before_anything_is_sent(env):
    panel = Panel()
    with pytest.raises(ps.PanelSetupError) as e:
        ps.enable(panel, **{**GOOD, "version": "12"})
    assert str(e.value) == ("This panel's firmware is too old for Wi-Fi. "
                            "Flash it first: sudo spoolwarden weigh-station flash-display")
    assert panel.lines == []
    ps.enable(panel, **{**GOOD, "version": "14"})


def test_the_cap_sentences(env):
    with pytest.raises(ps.PanelSetupError) as e:
        ps.enable(Panel(), **{**GOOD, "password": "p" * 64})
    assert str(e.value) == "A 64-digit key cannot be used. Type the Wi-Fi password instead."
    with pytest.raises(ps.PanelSetupError) as e:
        ps.enable(Panel(), **{**GOOD, "address": "h" * 65})
    assert str(e.value) == "The address is too long (64 characters at most)."
    ps.enable(Panel(), **{**GOOD, "address": "h" * 64})
    assert len(ps.setup_lines(**{k: v for k, v in GOOD.items() if k != "version"}, token="t" * 32)[1]) <= 256


def test_off_also_skips_unasked_lines(env):
    panel = Panel()
    panel.reply = ["AIR: 1", "OK"]
    assert ps.disable(panel)["panel_forgotten"] is True
