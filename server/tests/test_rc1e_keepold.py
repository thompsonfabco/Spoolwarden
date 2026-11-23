"""the Bambu per-printer files (slot map, mapping status, slot snapshot) keep the old JSON form current for a rollback
(v0.6.2's exact bytes, mode and key order), and a read that rewrites the XML form keeps its mode and the save lock."""
import json
import os
import threading

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


def _older_release_changes(f, **changes):
    d = json.loads(f.read_text(encoding="utf-8"))
    d.update(changes)
    f.write_text(json.dumps(d, indent=2), encoding="utf-8")


def _v062_slotmap_bytes(m):
    """v0.6.2 ams.save_map's bytes: this key order, json.dumps indent 2 (ASCII escapes), no newline."""
    data = {'slots': m.slots, 'calibrated': m.calibrated, 'observe': m.observe, 'updated': m.updated,
            'fingerprint': m.fingerprint, 'stale': m.stale, 'scheme': m.scheme, 'learned': m.learned,
            'mismatch_streak': m.mismatch_streak, 'suspect': m.suspect, 'by_user': m.by_user, 'job_file': m.job_file}
    return json.dumps(data, indent=2).encode("utf-8")


def test_the_slotmap_json_bytes_are_v062s_for_non_ascii_values_and_key_order(state):
    m = ams.SlotMap(slots={"Ä1": {"ams_id": 0, "tray_id": 0, "global_id": 0}, "A1": {"ams_id": 0, "tray_id": 1}},
                    calibrated=True, scheme="schéma", learned={"7": "Ä1"}, mismatch_streak=1, suspect=True)
    ams.save_map(SERIAL, m)
    raw = (state / "slotmap.json").read_bytes()
    assert raw == _v062_slotmap_bytes(m)
    assert rb"\u00c4" in raw and "Ä".encode() not in raw                                     # ASCII escapes
    assert list(json.loads(raw)) == ["slots", "calibrated", "observe", "updated", "fingerprint", "stale", "scheme",
                                     "learned", "mismatch_streak", "suspect", "by_user", "job_file"]
    assert list(json.loads(raw)["slots"]) == ["Ä1", "A1"]                                     # insertion order, not sorted


def test_the_mapping_status_json_bytes_are_v062s_for_non_ascii_values_and_key_order(state):
    job = mapping._Job(id="j1", state="done")
    job.notes, job.problems = ["Zürich"], ["Ä"]
    mapping._save_status(SERIAL, job)
    raw = (state / "mapping.json").read_bytes()
    data = {"kit": mapping.__version__, "job_id": job.id, "state": job.state, "step": job.step, "problems": job.problems,
            "notes": job.notes, "scheme_unsettled": job.scheme_unsettled,
            "report_wanted": job.report_wanted, "reason": job.reason, "trail": job.trail, "started": job.started,
            "finished": job.finished}
    assert raw == json.dumps(data, indent=2).encode("utf-8")
    assert rb"\u00fc" in raw and "ü".encode() not in raw
    assert list(json.loads(raw)) == list(data)


@pytest.mark.skipif(os.name == "nt", reason="file modes are Linux's")
def test_a_slotmap_rewritten_from_an_older_releases_json_stays_owner_only(state):
    ams.save_map(SERIAL, ams.SlotMap(slots=SLOTS, scheme="shift8"))
    _older_release_changes(state / "slotmap.json", scheme="shift4")
    assert ams.load_map(SERIAL).scheme == "shift4"
    assert (state / "slotmap.xml").stat().st_mode & 0o777 == 0o600


@pytest.mark.skipif(os.name == "nt", reason="file modes are Linux's")
def test_a_mapping_status_rewritten_from_an_older_releases_json_stays_owner_only(state):
    mapping._save_status(SERIAL, mapping._Job(id="j1", state="done"))
    _older_release_changes(state / "mapping.json", state="failed")
    assert mapping._load_status(SERIAL)["state"] == "failed"
    assert (state / "mapping.xml").stat().st_mode & 0o777 == 0o600


def test_a_read_that_rewrites_the_slotmap_waits_for_a_save_in_progress(state):
    ams.save_map(SERIAL, ams.SlotMap(slots=SLOTS, scheme="shift8"))
    _older_release_changes(state / "slotmap.json", scheme="shift4")
    holding, release, done = threading.Event(), threading.Event(), threading.Event()

    def saver():
        with ams._SAVE_LOCK:
            holding.set()
            release.wait(10)

    t = threading.Thread(target=saver, daemon=True)
    t.start()
    assert holding.wait(5)
    reader = threading.Thread(target=lambda: (ams.load_map(SERIAL), done.set()), daemon=True)
    reader.start()
    try:
        assert not done.wait(0.5)                      # was: rewrote slotmap.xml while a save held the lock
    finally:
        release.set()
    assert done.wait(5)
    t.join(5)
    reader.join(5)


def _v062_snapshot_bytes(d):
    """v0.6.2 snapshot.save's bytes: this key order, json.dumps indent 2, no newline."""
    data = {"kind": d["kind"], "at": d["at"],
            "slots": [{"label": x["label"], "ams_id": x["ams_id"], "tray_id": x["tray_id"], "setting": x["setting"]}
                      for x in d["slots"]],
            "test_colours": d["test_colours"]}
    return json.dumps(data, indent=2).encode("utf-8")


def test_the_snapshot_json_is_kept_in_v062s_shape_and_clear_removes_both_forms(state):
    slots = [{"label": "Ä1", "ams_id": 0, "tray_id": 1, "setting": {"tray_info_idx": "GFA00", "tray_color": "FF0000FF"}}]
    snapshot.save(SERIAL, "slottest", slots, test_colours=["ff0000"])
    f = state / "slottest-snapshot.json"
    assert (state / "slottest-snapshot.xml").is_file() and f.is_file() and (state / "slottest-snapshot.json.sha256").is_file()
    raw = f.read_bytes()
    assert raw == _v062_snapshot_bytes(json.loads(raw)) and rb"\u00c4" in raw
    assert list(json.loads(raw)) == ["kind", "at", "slots", "test_colours"]
    assert snapshot.load(SERIAL)["kind"] == "slottest" and snapshot.pending(SERIAL)
    snapshot.clear(SERIAL)
    assert not any(state.glob("slottest-snapshot*")) and not snapshot.pending(SERIAL)


def test_a_snapshot_the_older_release_cleared_after_a_rollback_is_not_a_pending_restore(state):
    snapshot.save(SERIAL, "slottest", [], test_colours=["FF0000"])
    (state / "slottest-snapshot.json").unlink()            # v0.6.2 deletes the JSON when its restore is confirmed
    assert not snapshot.pending(SERIAL) and snapshot.load(SERIAL) is None
    assert not any(state.glob("slottest-snapshot*"))


def test_a_snapshot_the_older_release_rewrote_is_read_once_and_kept(state):
    snapshot.save(SERIAL, "slottest", [], test_colours=["FF0000"])
    _older_release_changes(state / "slottest-snapshot.json", kind="slottest2")          # v0.6.2 `forget` / a new slot test
    assert snapshot.load(SERIAL)["kind"] == "slottest2"
    assert snapshot.load(SERIAL)["kind"] == "slottest2"
    assert "slottest2" in (state / "slottest-snapshot.xml").read_text(encoding="utf-8")


@pytest.mark.skipif(os.name == "nt", reason="file modes are Linux's")
def test_a_snapshot_rewritten_from_an_older_releases_json_stays_owner_only(state):
    snapshot.save(SERIAL, "slottest", [], test_colours=["FF0000"])
    _older_release_changes(state / "slottest-snapshot.json", kind="slottest2")
    snapshot.load(SERIAL)
    assert (state / "slottest-snapshot.xml").stat().st_mode & 0o777 == 0o600
    assert (state / "slottest-snapshot.json.sha256").stat().st_mode & 0o777 == 0o600
