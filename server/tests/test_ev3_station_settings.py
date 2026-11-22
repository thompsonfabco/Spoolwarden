"""EV3 (a): the station's own settings file (station/settings.py), the lamp mapping (flow.lamp_modes) and where the
station keeps its state (state.state_dir)."""
from __future__ import annotations

import os
import stat
import tempfile
import time
from pathlib import Path

import pytest

from filament_kit.station import flow, settings
from filament_kit.station import state as st


def make(tmp_path, **installed):
    target = {"beep": True, "speech": True, "tts": "auto", "speaker_volume": 50, "panel_swap": False, **installed}
    lamps: dict = {}
    told: list = []
    store = settings.Store(tmp_path / "state" / "settings.xml", target, lamps, after=told.append)
    return store, target, lamps, told


def value(store, id_):
    return next(s["value"] for s in store.listing() if s["id"] == id_)


def test_the_list_has_the_contract_ids_and_one_lamp_per_step(tmp_path):
    store, *_ = make(tmp_path)
    ids = [s["id"] for s in store.listing()]
    assert ids[:5] == ["beep", "speech", "voice", "volume", "panel_swap"]
    assert ids[5:] == [f"lamp.{step}" for step in flow.STEP_LAMPS]
    for s in store.listing():
        assert s["kind"] in ("switch", "choice", "number") and s["label"]
        assert ("choices" in s) == (s["kind"] == "choice") and ("min" in s) == (s["kind"] == "number")


def test_with_no_file_the_installed_values_stand_and_nothing_changed(tmp_path):
    store, target, lamps, _ = make(tmp_path, speaker_volume=30, beep=False)
    assert value(store, "volume") == 30 and value(store, "beep") is False
    assert store.changed is False and lamps == {} and target["speaker_volume"] == 30
    assert value(store, "lamp.lookup") == "both" and value(store, "lamp.idle") == "off"      # today's table
    assert not (tmp_path / "state" / "settings.xml").exists()


def test_a_change_wins_over_the_installed_value_is_applied_and_survives_a_restart(tmp_path):
    store, target, lamps, told = make(tmp_path)
    out = store.change({"volume": 80, "beep": False, "voice": "espeak", "lamp.weigh": "red", "panel_swap": True})
    assert {s["id"]: s["value"] for s in out}["volume"] == 80
    assert target["speaker_volume"] == 80 and target["beep"] is False and target["tts"] == "espeak"
    assert target["panel_swap"] is True and lamps == {"weigh": "red"} and store.changed
    assert told == [{"volume", "beep", "voice", "lamp.weigh", "panel_swap"}]
    again, target2, lamps2, _ = make(tmp_path)               # a new process: the installer's values, the file over them
    assert target2["speaker_volume"] == 80 and target2["tts"] == "espeak" and lamps2 == {"weigh": "red"}
    assert again.changed and value(again, "lamp.weigh") == "red"


def test_a_value_equal_to_the_installed_one_is_not_kept(tmp_path):
    store, *_ = make(tmp_path)
    store.change({"volume": 80})
    store.change({"volume": 50})
    assert store.changed is False and not (tmp_path / "state" / "settings.xml").exists()


def test_back_to_the_installed_values_removes_the_file(tmp_path):
    store, target, lamps, told = make(tmp_path)
    store.change({"volume": 80, "lamp.done": "off"})
    told.clear()
    out = store.reset()
    assert {s["id"]: s["value"] for s in out}["volume"] == 50 and target["speaker_volume"] == 50 and lamps == {}
    assert not (tmp_path / "state" / "settings.xml").exists() and store.changed is False
    assert told == [{"volume", "lamp.done"}]


@pytest.mark.parametrize("body", [{"volume": 101}, {"volume": -1}, {"volume": True}, {"volume": "loud"}, {"beep": "maybe"},
                                  {"voice": "klingon"}, {"lamp.idle": "green"}, {"nope": 1}, {"lamp.nostep": "red"}, {}, []])
def test_a_bad_value_refuses_the_whole_change(tmp_path, body):
    store, target, *_ = make(tmp_path)
    with pytest.raises(settings.SettingsError):
        store.change(body)
    assert target["speaker_volume"] == 50 and store.changed is False


def test_one_bad_value_among_good_ones_changes_nothing(tmp_path):
    store, target, *_ = make(tmp_path)
    with pytest.raises(settings.SettingsError):
        store.change({"volume": 70, "voice": "klingon"})
    assert target["speaker_volume"] == 50 and not (tmp_path / "state" / "settings.xml").exists()


def test_the_file_is_xml_and_private(tmp_path):
    store, *_ = make(tmp_path)
    store.change({"volume": 70, "lamp.lookup": "yellow"})
    path = tmp_path / "state" / "settings.xml"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("<?xml") and '<setting id="volume" value="70"' in text and 'id="lamp.lookup" value="yellow"' in text
    assert not text.lstrip().startswith("{")
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600 and stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert [p.name for p in path.parent.iterdir()] == ["settings.xml"]       # no temp file left behind


def test_a_broken_file_is_ignored_with_a_reason_and_the_installed_values_stand(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "settings.xml").write_text("<settings><setting id='volume' value='9'", encoding="utf-8")
    store, target, *_ = make(tmp_path)
    assert target["speaker_volume"] == 50 and store.problem and "could not be read" in store.problem
    store.change({"volume": 60})                                # a good save replaces it
    assert make(tmp_path)[1]["speaker_volume"] == 60


def test_unknown_and_out_of_range_entries_in_the_file_are_ignored(tmp_path):
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / "settings.xml").write_text(
        '<settings version="1"><setting id="volume" value="900"/><setting id="beep" value="off"/>'
        '<setting id="who" value="x"/><setting id="lamp.idle" value="mauve"/></settings>', encoding="utf-8")
    store, target, lamps, _ = make(tmp_path)
    assert target["speaker_volume"] == 50 and target["beep"] is False and lamps == {}


def test_a_link_at_the_file_name_is_not_followed_when_reading_or_writing(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    secret = tmp_path / "elsewhere.xml"
    secret.write_text('<settings><setting id="volume" value="11"/></settings>', encoding="utf-8")
    try:
        (state / "settings.xml").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("no symbolic links here")
    if not getattr(os, "O_NOFOLLOW", 0):
        pytest.skip("this platform has no O_NOFOLLOW")
    store, target, *_ = make(tmp_path)
    assert target["speaker_volume"] == 50                       # the linked file was not read
    store.change({"volume": 70})
    assert "11" in secret.read_text(encoding="utf-8") and not (state / "settings.xml").is_symlink()   # replaced, not written through
    assert make(tmp_path)[1]["speaker_volume"] == 70


def test_a_file_that_cannot_be_written_refuses_and_changes_nothing(tmp_path):
    blocker = tmp_path / "state"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    store, target, *_ = make(tmp_path)
    with pytest.raises(settings.SettingsError, match="could not be saved"):
        store.change({"volume": 70})
    assert target["speaker_volume"] == 50 and store.changed is False


def test_the_speaker_volume_is_sent_to_the_panel_after_a_change(monkeypatch):
    sent = []
    from filament_kit.station import panel
    monkeypatch.setattr(panel, "send_volume", lambda: sent.append(st.settings["speaker_volume"]))
    monkeypatch.setitem(st.settings, "speaker_volume", 33)
    settings.after_change({"beep"})
    settings.after_change({"volume"})
    assert sent == [33]


def test_the_state_folder_comes_from_the_state_directory_variable(monkeypatch):
    monkeypatch.setenv("STATE_DIRECTORY", "/var/lib/x:/var/lib/y")
    assert st.state_dir().as_posix() == "/var/lib/x" and st.tls_dir().as_posix() == "/var/lib/x/tls"


# ---- the lamp mapping

def test_the_default_choice_for_each_step_is_exactly_the_old_table():
    for step, (y, r) in flow.STEP_LAMPS.items():
        assert flow.lamp_modes(step) == (y, r) == flow.lamp_modes(step, flow.lamp_default(step))
        assert flow.lamp_modes(step, "nonsense") == (y, r)


def test_one_lamp_shows_the_steps_pattern_and_the_other_is_dark():
    assert flow.lamp_modes("lookup", "yellow") == (4, 0) and flow.lamp_modes("lookup", "red") == (0, 4)
    assert flow.lamp_modes("pairing", "red") == (0, 2)           # the table lights only yellow here: red takes its pattern
    assert flow.lamp_modes("phone", "yellow") == (1, 0)
    assert flow.lamp_modes("idle", "both") == (0, 0) and flow.lamp_modes("idle", "yellow") == (0, 0)
    assert flow.lamp_modes("done", "both") == (3, 3) and flow.lamp_modes("weigh", "off") == (0, 0)


def test_the_panel_gets_the_mapped_lamps_and_a_cancel_still_blinks_red(monkeypatch):
    monkeypatch.setitem(flow.flow, "step", "weigh")
    monkeypatch.setitem(st.state, "blink_until", 0)
    monkeypatch.setattr(st, "lamp_map", {"weigh": "red"})
    assert flow.lamps(time.time()) == (0, 2)
    monkeypatch.setitem(st.state, "blink_until", time.time() + 5)
    assert flow.lamps(time.time()) == (0, 3)
    monkeypatch.setattr(st, "lamp_map", {})
    assert flow.lamps(time.time()) == (2, 3)


def test_a_store_changes_the_lamps_the_flow_reads(monkeypatch):
    monkeypatch.setitem(flow.flow, "step", "ready")
    monkeypatch.setitem(st.state, "blink_until", 0)
    monkeypatch.setattr(st, "lamp_map", {})
    assert flow.lamps(time.time()) == (1, 1)
    with tempfile.TemporaryDirectory() as d:
        store = settings.Store(Path(d) / "settings.xml", dict(st.settings), st.lamp_map)
        store.change({"lamp.ready": "yellow"})
        assert flow.lamps(time.time()) == (1, 0)
        store.reset()
        assert flow.lamps(time.time()) == (1, 1)
