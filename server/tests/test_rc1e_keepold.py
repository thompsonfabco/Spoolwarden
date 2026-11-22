"""RC1e: the Bambu per-printer files keep the old JSON form current for a rollback (v0.6.2's shapes); the slot snapshot is a
cache and does not."""
import json
import os

import pytest

from filament_kit import config
from filament_kit.bridge import ams, mapping, snapshot
from filament_kit.profiles import statexml

SERIAL = "SER1"
SLOTS = {"A1": {"ams_id": 0, "tray_id": 0, "global_id": 0}}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "paths", lambda: type("P", (), {"state": tmp_path, "etc": tmp_path})())
    statexml._unreadable.clear()
    d = tmp_path / "printers" / SERIAL
    d.mkdir(parents=True)
    yield d
    statexml._unreadable.clear()


def _v062_load_map(f):
    """What v0.6.2's ams.load_map reads from slotmap.json (keys and types it relies on)."""
    d = json.loads(f.read_text(encoding="utf-8"))
    return d["slots"], d.get("calibrated"), d.get("scheme"), d.get("learned"), d.get("mismatch_streak"), d.get("suspect")


def test_the_slotmap_json_is_kept_current_in_the_old_shape(state):
    m = ams.SlotMap(slots=SLOTS, calibrated=True, scheme="shift8", learned={"7": "A1"}, mismatch_streak=2, suspect=True)
    ams.save_map(SERIAL, m)
    f = state / "slotmap.json"
    assert f.read_bytes() == json.dumps(json.loads(f.read_bytes()), indent=2).encode("utf-8")      # indent 2, no newline
    assert _v062_load_map(f) == (SLOTS, True, "shift8", {"7": "A1"}, 2, True)
    m.scheme, m.suspect = "shift4", False
    ams.save_map(SERIAL, m)                                                                      # kept current
    assert _v062_load_map(f)[2:] == ("shift4", {"7": "A1"}, 2, False)
    assert (state / "slotmap.json.sha256").is_file()


@pytest.mark.skipif(os.name == "nt", reason="file modes are Linux's")
def test_the_slotmap_json_is_owner_only(state):
    ams.save_map(SERIAL, ams.SlotMap(slots=SLOTS))
    assert (state / "slotmap.json").stat().st_mode & 0o777 == 0o600


def test_a_slotmap_json_changed_by_an_older_release_wins_once(state):
    ams.save_map(SERIAL, ams.SlotMap(slots=SLOTS, scheme="shift8"))
    f = state / "slotmap.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    d["scheme"] = "shift4"
    f.write_text(json.dumps(d, indent=2), encoding="utf-8")
    assert ams.load_map(SERIAL).scheme == "shift4"
    ams.save_map(SERIAL, ams.SlotMap(slots=SLOTS, scheme="shift8"))
    assert ams.load_map(SERIAL).scheme == "shift8"                                                # once, not for ever


def test_the_mapping_status_json_is_kept_current_in_the_old_shape(state):
    job = mapping._Job(id="j1", state="done")
    mapping._save_status(SERIAL, job)
    f = state / "mapping.json"
    raw = f.read_bytes()
    d = json.loads(raw)
    assert raw == json.dumps(d, indent=2).encode("utf-8")
    assert d["job_id"] == "j1" and d["state"] == "done"
    job.state = "failed"
    mapping._save_status(SERIAL, job)
    assert json.loads(f.read_bytes())["state"] == "failed"
    assert mapping._load_status(SERIAL)["state"] == "failed"


def test_a_mapping_status_json_changed_by_an_older_release_wins_once(state):
    job = mapping._Job(id="j1", state="done")
    mapping._save_status(SERIAL, job)
    f = state / "mapping.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    d["state"] = "failed"
    f.write_text(json.dumps(d, indent=2), encoding="utf-8")
    assert mapping._load_status(SERIAL)["state"] == "failed"


def test_the_snapshot_is_a_cache_and_keeps_no_json(state):
    snapshot.save(SERIAL, "slottest", [], test_colours=["FF0000"])
    assert (state / "slottest-snapshot.xml").is_file()
    assert not (state / "slottest-snapshot.json").exists() and not (state / "slottest-snapshot.json.sha256").exists()
    assert snapshot.load(SERIAL)["kind"] == "slottest"
