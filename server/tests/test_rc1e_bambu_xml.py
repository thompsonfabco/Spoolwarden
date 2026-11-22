"""RC1e: the Bambu Lab per-printer files (slotmap, mapping status, slot snapshot) as XML (R85 section 1)."""
import json
import os
import threading

import pytest

from filament_kit import config
from filament_kit.bridge import ams, mapping, snapshot
from filament_kit.profiles import statexml

SERIAL = "SER1"
SLOTS = {"A1": {"ams_id": 0, "tray_id": 0, "global_id": 0}, "HT-A": {"ams_id": 128, "tray_id": 0, "global_id": 128}}


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "paths", lambda: type("P", (), {"state": tmp_path, "etc": tmp_path})())
    statexml._unreadable.clear()
    yield tmp_path
    statexml._unreadable.clear()


def _dir(state):
    d = state / "printers" / SERIAL
    d.mkdir(parents=True, exist_ok=True)
    return d


def _full_map():
    return ams.SlotMap(slots=SLOTS, calibrated=True, observe=False, fingerprint=[[0, "ams", 4], [255, None, 1]],
                       stale=True, scheme="shift8", learned={"7": "A1"}, mismatch_streak=1, suspect=True,
                       by_user={"A1": {"by": "user", "at": 1762800000.5}}, job_file="project")


# ---------------------------------------------------------------- the slot map

def test_slotmap_round_trips_in_xml_with_the_json_beside(state):
    m = _full_map()
    ams.save_map(SERIAL, m)
    d = state / "printers" / SERIAL
    assert (d / "slotmap.xml").is_file() and (d / "slotmap.json").is_file()
    assert (d / "slotmap.xml").read_bytes().startswith(b"<?xml")
    back = ams.load_map(SERIAL)
    for name in ("slots", "calibrated", "observe", "updated", "fingerprint", "stale", "scheme", "learned",
                 "mismatch_streak", "suspect", "by_user", "job_file"):
        assert getattr(back, name) == getattr(m, name), name


@pytest.mark.skipif(os.name == "nt", reason="file modes are Linux's")
def test_slotmap_file_is_owner_only(state):
    ams.save_map(SERIAL, _full_map())
    assert (state / "printers" / SERIAL / "slotmap.xml").stat().st_mode & 0o777 == 0o600


def test_old_slotmap_json_is_read_moved_and_left_as_it_was(state):
    old = {"slots": SLOTS, "calibrated": True, "observe": False, "updated": 1762800000.25,
           "fingerprint": [[0, "ams", 4]], "stale": False, "scheme": None, "learned": {}, "mismatch_streak": 0,
           "suspect": False, "by_user": {}, "job_file": None}
    f = _dir(state) / "slotmap.json"
    f.write_text(json.dumps(old, indent=2), encoding="utf-8")
    before = f.read_bytes()
    m = ams.load_map(SERIAL)
    assert m.slots == SLOTS and m.calibrated and not m.observe and m.updated == 1762800000.25
    assert (f.parent / "slotmap.xml").is_file()             # moved at the first read
    assert f.read_bytes() == before                         # the rollback finds its settings
    m.suspect = True                                        # from now on the XML is the one in use
    ams.save_map(SERIAL, m)
    assert f.read_bytes() == before and ams.load_map(SERIAL).suspect is True


def test_damaged_slotmap_xml_is_the_default_map_and_is_reported(state):
    x = _dir(state) / "slotmap.xml"
    x.write_text("<slotmap version=\"1\"><slots", encoding="utf-8")
    m = ams.load_map(SERIAL)                                # a start never fails for it
    assert m.slots == {} and m.calibrated is False and m.observe is True
    assert any("slotmap.xml" in p for p in statexml.problems())
    ams.save_map(SERIAL, _full_map())                       # never overwritten: kept aside
    assert (x.parent / "slotmap.xml.unreadable").read_text(encoding="utf-8").startswith("<slotmap")
    assert ams.load_map(SERIAL).slots == SLOTS


def test_damaged_slotmap_xml_falls_back_to_the_json_form(state):
    d = _dir(state)
    (d / "slotmap.json").write_text(json.dumps({"slots": SLOTS, "calibrated": True}), encoding="utf-8")
    (d / "slotmap.xml").write_text("not xml at all", encoding="utf-8")
    assert ams.load_map(SERIAL).slots == SLOTS


def test_save_map_if_compares_the_version_through_the_xml(state):
    ams.save_map(SERIAL, _full_map())
    m = ams.load_map(SERIAL)
    assert ams.save_map_if(SERIAL, m, m.updated) is True
    assert ams.save_map_if(SERIAL, m, m.updated - 1) is False


@pytest.mark.skipif(os.name == "nt", reason="Windows refuses a replace over a file being read: the service runs on Linux")
def test_concurrent_saves_and_loads_never_see_a_torn_file(state):
    ams.save_map(SERIAL, _full_map())
    errors, stop = [], threading.Event()

    def writer():
        for _ in range(60):
            m = _full_map()
            ams.save_map(SERIAL, m)

    def reader():
        while not stop.is_set():
            try:
                if ams.load_map(SERIAL).slots != SLOTS:
                    errors.append("torn")
            except Exception as e:  # noqa: BLE001
                errors.append(repr(e))

    ts = [threading.Thread(target=writer) for _ in range(2)] + [threading.Thread(target=reader) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts[:2]:
        t.join(30)
    stop.set()
    for t in ts[2:]:
        t.join(10)
    assert errors == []
    assert not list((state / "printers" / SERIAL).glob("*.tmp"))


# ---------------------------------------------------------------- the mapping status

class _Job:
    id, state, step, reason = "12345", "done", "Mapping finished", "none"
    problems = ["Ext-L: no slot", "1.0"]
    notes = ["a & b <c>"]
    scheme_unsettled, report_wanted = False, None
    trail = [{"try": 1, "ok": True}, "plain"]
    started, finished = 1762800000.5, 1762800060.25


def test_mapping_status_round_trips_in_xml_with_the_json_beside(state):
    mapping._save_status(SERIAL, _Job)
    d = state / "printers" / SERIAL
    assert (d / "mapping.xml").is_file() and (d / "mapping.json").is_file()
    v = mapping._load_status(SERIAL)
    assert v["state"] == "done" and v["job_id"] == "12345" and v["step"] == "Mapping finished"
    assert v["problems"] == ["Ext-L: no slot", "1.0"] and v["notes"] == ["a & b <c>"]
    assert v["trail"] == [{"try": 1, "ok": True}, "plain"] and v["finished"] == 1762800060.25


@pytest.mark.skipif(os.name == "nt", reason="file modes are Linux's")
def test_mapping_status_file_is_owner_only(state):
    mapping._save_status(SERIAL, _Job)
    assert (state / "printers" / SERIAL / "mapping.xml").stat().st_mode & 0o777 == 0o600


def test_old_mapping_json_is_read_moved_and_left(state):
    f = _dir(state) / "mapping.json"
    f.write_text(json.dumps({"job_id": "x", "state": "failed", "step": "", "problems": ["p"], "finished": 5.0}),
                 encoding="utf-8")
    before = f.read_bytes()
    v = mapping._load_status(SERIAL)
    assert v["state"] == "failed" and v["problems"] == ["p"] and v["finished"] == 5.0
    assert (f.parent / "mapping.xml").is_file() and f.read_bytes() == before


def test_damaged_mapping_xml_reads_as_none(state):
    (_dir(state) / "mapping.xml").write_text("<mapping><", encoding="utf-8")
    assert mapping._load_status(SERIAL) is None
    assert any("mapping.xml" in p for p in statexml.problems())


def test_a_status_that_is_not_a_known_state_reads_as_none(state):
    statexml.write(_dir(state) / "mapping.json", {"state": "weird"}, mode=0o600)
    assert mapping._load_status(SERIAL) is None


# ---------------------------------------------------------------- the slot snapshot

SNAP_SLOTS = [{"label": "A1", "ams_id": 0, "tray_id": 0, "setting": {"tray_info_idx": "GFA00", "tray_color": "00000000",
                                                                      "tray_type": "PLA", "nozzle_temp_min": 190}},
              {"label": "A2", "ams_id": 0, "tray_id": 1, "setting": {}}]


def test_snapshot_round_trips_in_xml_only(state):
    snapshot.save(SERIAL, "slottest", SNAP_SLOTS, ["FA0100ff"])
    d = state / "printers" / SERIAL
    assert (d / "slottest-snapshot.xml").is_file() and not (d / "slottest-snapshot.json").exists()
    assert snapshot.pending(SERIAL) and snapshot.pending_serials() == [SERIAL]
    got = snapshot.load(SERIAL)
    assert got == {"kind": "slottest", "slots": SNAP_SLOTS, "test_colours": ["FA0100FF"]}


@pytest.mark.skipif(os.name == "nt", reason="file modes are Linux's")
def test_snapshot_file_is_owner_only(state):
    snapshot.save(SERIAL, "slottest", SNAP_SLOTS)
    assert (state / "printers" / SERIAL / "slottest-snapshot.xml").stat().st_mode & 0o777 == 0o600


def test_old_snapshot_json_is_still_a_pending_restore(state):
    f = _dir(state) / "slottest-snapshot.json"
    f.write_text(json.dumps({"kind": "calibration", "at": 1.0, "slots": [
        {"label": "A1", "ams_id": 0, "tray_id": 0, "setting": {"tray_type": "PLA"}}], "test_colours": ["FA0100FF"]}),
        encoding="utf-8")
    assert snapshot.pending(SERIAL) and snapshot.pending_serials() == [SERIAL]
    got = snapshot.load(SERIAL)
    assert got["kind"] == "calibration" and got["slots"][0]["setting"] == {"tray_type": "PLA"}
    assert got["test_colours"] == ["FA0100FF"]


def test_clearing_a_snapshot_removes_both_forms(state):
    f = _dir(state) / "slottest-snapshot.json"
    f.write_text(json.dumps({"kind": "slottest", "slots": [], "test_colours": []}), encoding="utf-8")
    snapshot.load(SERIAL)                                   # moves it to XML
    assert (f.parent / "slottest-snapshot.xml").is_file()
    snapshot.clear(SERIAL)
    assert not f.exists() and not (f.parent / "slottest-snapshot.xml").exists()
    assert not snapshot.pending(SERIAL) and snapshot.pending_serials() == [] and snapshot.load(SERIAL) is None
    snapshot.clear(SERIAL)                                  # nothing there: no error


def test_damaged_snapshot_xml_is_still_pending_and_named_in_the_message(state):
    x = _dir(state) / "slottest-snapshot.xml"
    x.write_text("<slottest-snapshot", encoding="utf-8")
    assert snapshot.pending(SERIAL)
    assert snapshot.load(SERIAL)["corrupt"] is True


def test_forget_drops_a_slot_through_the_xml(state):
    snapshot.save(SERIAL, "slottest", SNAP_SLOTS, ["FA0100FF"])
    snapshot.forget(SERIAL, 0, 0)
    assert [s["label"] for s in snapshot.load(SERIAL)["slots"]] == ["A2"]
    snapshot.forget(SERIAL, 0, 1)
    assert not snapshot.pending(SERIAL)
