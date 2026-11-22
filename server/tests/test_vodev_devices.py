"""The Devices page shows a station's Speak and Volume as the Hub-set rows they are (VO1 readonly + note) and answers the
station's 409 `set_on_hub` with its own refusal, not a 502."""
from __future__ import annotations

import json
import re
import subprocess

from filament_kit import station_client
from filament_kit.setup_console import device_tab  # noqa: F401

from test_hub_device_tab import env_hub, stub, SETTINGS  # noqa: F401
from test_setup_page import env, helper, manager  # noqa: F401
import test_setup_page as SP

NOTE = "Speaking and volume are set on the Hub: Settings > Weigh Station voice."
SENTENCE = "Speaking and volume are set on the Hub."


def _locked_rows():
    return [{"id": "speech", "label": "Speak", "kind": "switch", "value": True, "readonly": True, "note": NOTE},
            {"id": "volume", "label": "Volume", "kind": "number", "value": 60, "min": 0, "max": 100, "readonly": True, "note": NOTE},
            {"id": "beep", "label": "Beep", "kind": "switch", "value": True}]


def test_the_cleaned_setting_keeps_readonly_and_a_bounded_note():
    kept, dropped = station_client.clean_settings(_locked_rows())
    assert dropped is False
    assert kept[0]["readonly"] is True and kept[0]["note"] == NOTE and kept[1]["readonly"] is True
    assert "readonly" not in kept[2] and "note" not in kept[2]
    odd = [{"id": "a", "label": "A", "kind": "text", "value": "x", "readonly": "yes", "note": 7},
           {"id": "b", "label": "B", "kind": "text", "value": "x", "readonly": True, "note": "n" * 500}]
    kept, _ = station_client.clean_settings(odd)
    assert "readonly" not in kept[0] and "note" not in kept[0]            # only the bool True and a string pass
    assert kept[1]["readonly"] is True and len(kept[1]["note"]) == station_client.MAX_TEXT


def test_the_page_route_carries_readonly_and_the_note(env_hub):
    c, st = env_hub
    st.settings = _locked_rows()
    one = c.get("/setup/api/device/d3").json()
    rows = {s["id"]: s for s in one["settings"]}
    assert rows["speech"]["readonly"] is True and rows["speech"]["note"] == NOTE
    assert rows["volume"]["readonly"] is True and rows["volume"]["note"] == NOTE
    assert "readonly" not in rows["beep"] and "settings_dropped" not in one


def test_the_station_409_set_on_hub_is_its_own_refusal_not_a_502(env_hub):
    c, st = env_hub
    st.fault["post"] = (409, {"error": "set_on_hub", "message": SENTENCE})
    r = SP.post(c, "device/d3/settings", {"volume": 50})
    assert r.status_code == 409 and r.json() == {"error": SENTENCE, "code": "set_on_hub"}        # the page's shape
    st.fault["post"] = (409, {"error": "busy", "message": "x"})
    assert SP.post(c, "device/d3/settings", {"volume": 50}).status_code == 502        # any other 409 is as before


def _node_field(rows):
    from test_hub_devices import _missing, _node
    node = _node()
    if not node:
        _missing("needs Node 22 or later")
    src = (SP.STATIC / "setup_devices.js").read_text(encoding="utf-8")
    fn = re.search(r"^function devSettingField\(s\) \{.*?^\}", src, re.M | re.S).group(0)
    prog = ("const SW = { t: (k) => k, field: (o) => o }; const devT = (k) => k;" + fn
            + ";console.log(JSON.stringify(" + json.dumps(rows) + ".map(devSettingField)))")
    run = subprocess.run([node, "-e", prog], capture_output=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def test_a_readonly_row_is_drawn_disabled_with_its_note_and_the_others_are_not():
    speak, volume, beep = _node_field(_locked_rows())
    for o, name in ((speak, "speech"), (volume, "volume")):
        assert o["name"] == name and o["disabled"] is True and o["hint"] == NOTE
    assert not beep.get("disabled") and not beep.get("hint")


def test_the_form_reads_only_the_error_sentence_not_a_message_field():
    src = (SP.STATIC / "setup_devices.js").read_text(encoding="utf-8")
    assert "e.data.message" not in src


# ---- the flash notice: shown on the Weigh Station's Devices row while the station's /state carries it ----
LOST = ("The panel was flashed from scratch, so its Wi-Fi set-up is gone. "
        "Run: sudo spoolwarden weigh-station panel-wifi")


def _fake_state(monkeypatch, answer):
    monkeypatch.setattr(station_client, "_panel_seen", {})

    class Fake:
        def __init__(self, *a, **k):
            pass

        def state(self):
            if isinstance(answer, Exception):
                raise answer
            return answer
    monkeypatch.setattr(station_client, "StationClient", Fake)


def test_the_flash_notice_is_the_catalogue_sentence_while_the_station_carries_one(monkeypatch):
    _fake_state(monkeypatch, {"panel": None, "flash_notice": "x from the station"})
    assert station_client.station_flash_notice("http://h") == LOST
    for answer in ({"panel": "usb", "flash_notice": None}, {"panel": "usb"}, {"flash_notice": "  "}, {"flash_notice": 7},
                   station_client.StationUnreachable("x")):
        _fake_state(monkeypatch, answer)
        assert station_client.station_flash_notice("http://h") is None
    assert station_client.station_flash_notice(None) is None


def test_the_devices_row_of_a_station_shows_the_notice_only_when_the_state_carries_it(env, monkeypatch):
    from filament_kit import config
    monkeypatch.setattr(device_tab, "_pairings", lambda: [{"id": "s1", "name": "Bench", "type": "station"}])
    monkeypatch.setattr(device_tab, "_readers", lambda: [])
    monkeypatch.setattr(station_client, "station_reach", lambda devs, url: {})
    monkeypatch.setattr(config, "load", lambda: {"station": {"url": "http://127.0.0.1:5003", "panel_link": "wifi"}})
    _fake_state(monkeypatch, {"panel": None, "flash_notice": "sentence"})
    assert device_tab.rows()[0]["panel_notice"] == LOST
    _fake_state(monkeypatch, {"panel": None, "flash_notice": None})
    assert "panel_notice" not in device_tab.rows()[0]


def test_the_devices_page_draws_the_notice_line_on_the_row():
    src = (SP.STATIC / "setup_devices.js").read_text(encoding="utf-8")
    assert "panel_notice: r.panel_notice" in src and "d.panel_notice" in src


def test_the_devices_page_draws_the_panel_text_and_nothing_when_it_is_null():
    from test_hub_devices import _missing, _node
    node = _node()
    if not node:
        _missing("needs Node 22 or later")
    src = (SP.STATIC / "setup_devices.js").read_text(encoding="utf-8")
    fn = re.search(r"^function devPanelLine\(d\) \{.*?^\}", src, re.M | re.S).group(0)
    rows = [{"panel": "Panel: on Wi-Fi"}, {"panel": "Panel: on USB"}, {"panel": None}, {}]
    prog = ("const h = (tag, o) => ({ tag, ...o });" + fn
            + ";console.log(JSON.stringify(" + json.dumps(rows) + ".map(devPanelLine)))")
    run = subprocess.run([node, "-e", prog], capture_output=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    wifi, usb, null, absent = json.loads(run.stdout)
    assert wifi["textContent"] == "Panel: on Wi-Fi" and usb["textContent"] == "Panel: on USB"
    assert null is None and absent is None
    assert "panel: r.panel," in src and "devPanelLine(d)" in src
