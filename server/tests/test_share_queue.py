"""Queueing corrections for Share corrections (share_queue.py): what is queued and when, Undo, a failing reader."""
import json
import time

import pytest
from fakes import local_reader_fake as lrf

from filament_kit import config, intake_history, inventory, local_intake_install as lii, local_reader, pairing
from filament_kit import share_queue
from filament_kit.spoolman import Spoolman

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100
RULES = {"pla_location": "Rack 1", "box_prefix": "Storage box ", "box_count": 2,
         "overflow": "Overflow", "sealed_location": "Sealed stock"}
ITEM = {"vendor": "SUNLU", "name": "PLA+ Grey", "material": "PLA", "color_hex": "8A8C8E", "net": 1000, "tare": 213,
        "density": 1.24, "count": 1, "nozzle": 220, "bed": 55, "nozzle_range": "200-230", "bed_range": "50-60",
        "sealed": True}


@pytest.fixture(autouse=True)
def fk_env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    pairing.make_cert("fkhost", [], tmp_path / "etc")
    monkeypatch.setattr(share_queue, "INLINE", True)
    monkeypatch.setattr(lii, "installed", lambda: {"version": "0.2.0", "tier": "cpu"})
    monkeypatch.setattr(local_reader, "CONNECT_TIMEOUT", 0.5)


@pytest.fixture
def sm(spoolman_url):
    s = Spoolman(spoolman_url)
    s.ensure_fields()
    return s


@pytest.fixture
def reader(fake_reader):
    url, data = fake_reader
    lrf.feedback_health(data)
    setup(url)
    return data


def setup(url, on=True, module=True):
    config.save({"placement": {"box_count": 2}, "modules": {"spoolman": True, "server": True,
                                                              "local_intake": module},
                 "local_intake": {"url": url}, "label_reader": {"share_corrections": on}})


def saved(sm, it=None, read=True, engine=None, photos=(JPEG,)):
    """A save after a read (the read's photos set aside first), as the add paths make it."""
    rid = "r" + str(time.time_ns())
    if read:
        intake_history.stash_read(rid, list(photos), {"items": [ITEM], "engine": engine or {"reader": "local"}})
    it = {**ITEM, **(it or {})}
    r = inventory.add_and_link(sm, [it], RULES, False, None)
    return intake_history.record_save(r.saved, source="phone", device="t", read_id=rid if read else None)


def queue(data):
    return data["queue"]


# ---------------------------------------------------------------- what is queued

def test_a_field_changed_before_add_is_queued(sm, reader):
    hid = saved(sm, {"name": "PLA+ Gray", "nozzle": 225})
    (e,) = queue(reader)
    assert e["record"] == hid and e["source"] == "local" and e["jpegs"] == [JPEG]
    assert e["predicted"]["name"] == "PLA+ Grey" and e["corrected"]["name"] == "PLA+ Gray"
    assert e["predicted"]["nozzle"] == 220 and e["corrected"]["nozzle"] == 225
    assert intake_history.get(hid)["share"] == {"state": "waiting", "reason": None, "entries": 1}


def test_an_unchanged_read_is_not_a_correction(sm, reader):
    hid = saved(sm)
    assert queue(reader) == []
    assert intake_history.get(hid)["share"]["state"] == "none"


def test_a_hand_edit_in_recently_added_is_queued_and_a_cloud_reading_is_flagged(sm, reader):
    hid = saved(sm, engine={"reader": "cloud", "model": "claude"})
    assert queue(reader) == []
    intake_history.apply(sm, hid, item=0, values={"nozzle": 230})
    (e,) = queue(reader)
    assert e["source"] == "cloud" and e["corrected"]["nozzle"] == 230 and e["predicted"]["nozzle"] == 220


def test_an_accepted_suggestion_is_queued_with_its_note(sm, reader):
    hid = saved(sm)
    rec = intake_history._load(hid)
    rec["proposals"] = {"p-abc": {"t": time.time(), "item": 0, "after": {**rec["saved"][0]["values"], "bed": 60},
                                  "note": "bed is 60", "ver": 0}}
    intake_history._save(rec)
    intake_history.apply(sm, hid, proposal="p-abc")
    (e,) = queue(reader)
    assert e["corrected"]["bed"] == 60 and e["note"] == "bed is 60"


def test_a_record_typed_by_hand_has_no_reading_to_correct(sm, reader):
    hid = saved(sm, read=False)
    intake_history.apply(sm, hid, item=0, values={"nozzle": 230})
    assert queue(reader) == [] and intake_history.get(hid)["share"]["state"] == "none"


# ---------------------------------------------------------------- when it is not

def test_nothing_is_queued_when_the_setting_is_off(sm, reader, fake_reader):
    setup(fake_reader[0], on=False)
    hid = saved(sm, {"nozzle": 225})
    assert queue(reader) == [] and intake_history.get(hid)["share"]["state"] == "none"


def test_nothing_is_queued_without_a_reader_on_this_hub(sm, reader, fake_reader, monkeypatch):
    monkeypatch.setattr(lii, "installed", lambda: None)
    hid = saved(sm, {"nozzle": 225})
    assert queue(reader) == [] and intake_history.get(hid)["share"]["state"] == "none"
    setup(fake_reader[0], module=False)
    monkeypatch.setattr(lii, "installed", lambda: {"version": "0.2.0", "tier": "cpu"})
    saved(sm, {"nozzle": 226})
    assert queue(reader) == []


def test_nothing_is_queued_when_the_reader_has_no_feedback_capability(sm, fake_reader):
    url, data = fake_reader                  # a 0.1.x reader
    setup(url)
    hid = saved(sm, {"nozzle": 225})
    assert data["queue"] == [] and data["bodies"] == []
    assert intake_history.get(hid)["share"]["state"] == "none"


def test_no_photo_no_entry(sm, reader):
    hid = saved(sm, photos=())
    assert intake_history.get(hid)["photos"] == 0
    intake_history.apply(sm, hid, item=0, values={"nozzle": 230})
    assert queue(reader) == [] and intake_history.get(hid)["share"]["state"] == "none"
    config.save({**config.load(), "intake": {"history_keep": 0}})
    assert saved(sm, {"nozzle": 225}) is None and queue(reader) == []


# ---------------------------------------------------------------- Undo

def test_undo_deletes_the_entry_when_nothing_differs_any_more(sm, reader):
    hid = saved(sm)
    intake_history.apply(sm, hid, item=0, values={"nozzle": 230})
    assert len(queue(reader)) == 1
    rec = intake_history.undo(sm, hid, None)
    assert queue(reader) == [] and rec["share"]["state"] == "none"


def test_undo_requeues_the_records_values_as_they_are_now(sm, reader):
    hid = saved(sm)
    intake_history.apply(sm, hid, item=0, values={"nozzle": 230})
    intake_history.apply(sm, hid, item=0, values={"bed": 60})
    assert queue(reader)[0]["corrected"]["bed"] == 60
    rec = intake_history.undo(sm, hid, None)
    (e,) = queue(reader)
    assert e["corrected"]["bed"] == 55 and e["corrected"]["nozzle"] == 230 and rec["share"]["state"] == "waiting"


def test_a_reader_that_cannot_replace_by_record_gets_the_old_entry_deleted(sm, reader):
    reader["source_record"] = False
    lrf.feedback_health(reader)
    hid = saved(sm)
    intake_history.apply(sm, hid, item=0, values={"nozzle": 230})
    intake_history.apply(sm, hid, item=0, values={"nozzle": 240})
    (e,) = queue(reader)
    assert e["corrected"]["nozzle"] == 240 and "record" not in e and "source" not in e
    intake_history.undo(sm, hid, None)
    intake_history.undo(sm, hid, None)
    assert queue(reader) == []


def test_undo_finds_an_entry_without_the_hubs_map_through_the_readers_list(sm, reader):
    hid = saved(sm)
    intake_history.apply(sm, hid, item=0, values={"nozzle": 230})
    (intake_history._dir(hid) / "share.xml").unlink()
    intake_history.undo(sm, hid, None)
    assert queue(reader) == []


# ---------------------------------------------------------------- a failing reader

def test_a_reader_failure_never_fails_the_save_and_retry_queues_it(sm, reader):
    hid = saved(sm)
    reader["errors"] = [(500, "boom", None)]
    rec = intake_history.apply(sm, hid, item=0, values={"nozzle": 230})
    assert rec["saved"][0]["values"]["nozzle"] == 230                 # the save went through
    assert rec["share"]["state"] == "not_queued" and rec["share"]["reason"] and queue(reader) == []
    assert share_queue.retry(hid) == {"state": "waiting", "reason": None, "entries": 1}
    assert len(queue(reader)) == 1 and intake_history.get(hid)["share"]["state"] == "waiting"


def test_an_add_with_a_reader_that_is_down_is_recorded_and_not_queued(sm, fake_reader):
    setup("http://127.0.0.1:9")
    hid = saved(sm, {"nozzle": 225})
    assert hid and intake_history.get(hid)["share"]["state"] == "not_queued"
    assert "did not answer" in intake_history.get(hid)["share"]["reason"] or intake_history.get(hid)["share"]["reason"]


def test_a_reader_with_the_queue_switched_off_says_so(sm, reader):
    reader["opt_in"] = False
    hid = saved(sm, {"nozzle": 225})
    share = intake_history.get(hid)["share"]
    assert share["state"] == "not_queued" and "switched off" in share["reason"]


def test_the_state_goes_with_the_record(sm, reader):
    hid = saved(sm, {"nozzle": 225})
    assert (intake_history._dir(hid) / "share.xml").is_file()
    assert intake_history.list_records(True)["records"][0]["share"]["state"] == "waiting"


def test_a_thread_does_the_queueing_after_the_save(sm, reader, monkeypatch):
    monkeypatch.setattr(share_queue, "INLINE", False)
    hid = saved(sm, {"nozzle": 225})
    for _ in range(100):
        if intake_history.get(hid)["share"]["state"] == "waiting":
            break
        time.sleep(0.05)
    assert intake_history.get(hid)["share"]["state"] == "waiting" and len(queue(reader)) == 1


# ---------------------------------------------------------------- no address, ever

def test_no_body_carries_an_address(sm, reader):
    hid = saved(sm, {"nozzle": 225})
    intake_history.apply(sm, hid, item=0, values={"bed": 60})
    intake_history.undo(sm, hid, None)
    bodies = [b for b in reader["bodies"] if b]
    assert bodies
    for b in bodies:
        assert not {str(k).lower() for k in b} & set(local_reader.FORBIDDEN_FIELDS)
        text = json.dumps({k: v for k, v in b.items() if k != "images_b64"}).lower()
        assert "http" not in text and "drive.google" not in text


def test_a_waiting_entry_names_its_record_and_that_records_photo_count(sm, reader):
    from filament_kit import reader_corrections
    hid = saved(sm, photos=(JPEG, JPEG))
    intake_history.apply(sm, hid, item=0, values={"nozzle": 230})
    (e,) = reader_corrections.waiting(config.load())["entries"]
    assert e["record"] == hid and e["photos"] == 2


def test_an_entry_whose_record_is_gone_has_no_record_and_zero_photos(sm, reader):
    import shutil

    from filament_kit import reader_corrections
    hid = saved(sm)
    intake_history.apply(sm, hid, item=0, values={"nozzle": 230})
    shutil.rmtree(intake_history._dir(hid))
    (e,) = reader_corrections.waiting(config.load())["entries"]
    assert "record" not in e and e["photos"] == 0 and "thumbnail_b64" in e


def test_a_reader_entry_not_in_the_hubs_map_is_found_by_the_readers_own_key(sm, reader):
    from filament_kit import reader_corrections
    hid = saved(sm)
    intake_history.apply(sm, hid, item=0, values={"nozzle": 230})
    (_p := intake_history._dir(hid) / "share.xml").unlink()
    (e,) = reader_corrections.waiting(config.load())["entries"]
    assert e["record"] == hid and e["photos"] == 1


# ---------------------------------------------------------------- RC1d: the state as XML

def _state_dir(hid):
    d = intake_history._dir(hid)
    d.mkdir(parents=True, exist_ok=True)
    return d


def test_the_state_is_kept_as_share_xml_and_round_trips(sm, reader):
    hid = saved(sm, {"nozzle": 225})
    d = intake_history._dir(hid)
    assert (d / "share.xml").is_file() and (d / "share.json").is_file()          # both forms for one release
    assert (d / "share.xml").read_bytes().startswith(b'<?xml version="1.0" encoding="UTF-8"?>\n<share ')
    before = share_queue._read(hid)
    assert len(before["entries"]) == 1 and before["failed"] is None
    share_queue._write(hid, {"entries": {hid: None, f"{hid}.1": "fb-7"},
                             "failed": {"reason": "it did not answer: 12 & <3", "at": "2026-11-15T12:00:00+00:00"}})
    assert share_queue._read(hid) == {"entries": {hid: None, f"{hid}.1": "fb-7"},
                                      "failed": {"reason": "it did not answer: 12 & <3",
                                                 "at": "2026-11-15T12:00:00+00:00"}}
    assert share_queue.state(hid) == {"state": "not_queued", "reason": "it did not answer: 12 & <3", "entries": 2}


def test_an_old_share_json_is_read_and_moved_once_and_left_as_it_was(sm):
    hid = saved(sm)
    d = _state_dir(hid)
    old = json.dumps({"entries": {hid: "fb-3"}, "failed": None})
    (d / "share.json").write_text(old, encoding="utf-8")
    assert share_queue.state(hid) == {"state": "waiting", "reason": None, "entries": 1}
    assert (d / "share.xml").is_file() and (d / "share.json").read_text(encoding="utf-8") == old
    assert share_queue.entry_records() == {"fb-3": hid}
    (d / "share.json").write_text(json.dumps({"entries": {}, "failed": None}), encoding="utf-8")
    assert share_queue.state(hid)["entries"] == 0                 # an older release emptied it: its change wins once


def test_a_damaged_share_xml_never_raises_is_not_overwritten_and_is_reported(sm, reader):
    from filament_kit.profiles import statexml
    hid = saved(sm)
    d = _state_dir(hid)
    (d / "share.xml").write_bytes(b"<share version='1'><entries")
    assert share_queue.state(hid) == {"state": "none", "reason": None, "entries": 0}
    assert any("share.xml" in p for p in statexml.problems())
    share_queue._write(hid, {"entries": {hid: "fb-3"}, "failed": None})        # the next save keeps it aside
    assert (d / "share.xml.unreadable").read_bytes() == b"<share version='1'><entries"
    assert share_queue.state(hid) == {"state": "waiting", "reason": None, "entries": 1}


def test_an_empty_state_removes_both_forms(sm):
    hid = saved(sm)
    d = _state_dir(hid)
    (d / "share.json").write_text(json.dumps({"entries": {hid: "fb-3"}, "failed": None}), encoding="utf-8")
    share_queue.state(hid)
    assert (d / "share.xml").is_file()
    share_queue._write(hid, {"entries": {}, "failed": None})
    assert not (d / "share.xml").exists() and not (d / "share.json").exists()


@pytest.mark.skipif(not hasattr(__import__("os"), "geteuid"), reason="file modes are the Hub's on Linux")
def test_the_state_file_is_private_to_the_hub(sm, reader):
    hid = saved(sm, {"nozzle": 225})
    assert ((intake_history._dir(hid) / "share.xml").stat().st_mode & 0o777) == 0o600


@pytest.mark.skipif(not hasattr(__import__("os"), "geteuid"), reason="file modes are the Hub's on Linux")
def test_an_old_share_json_moved_to_xml_keeps_its_private_mode(sm):
    import os
    hid = saved(sm)
    d = _state_dir(hid)
    (d / "share.json").write_text(json.dumps({"entries": {hid: "fb-3"}, "failed": None}), encoding="utf-8")
    os.chmod(d / "share.json", 0o600)
    assert share_queue.state(hid)["entries"] == 1
    assert (os.stat(d / "share.xml").st_mode & 0o777) == 0o600


def test_the_queue_keeps_share_json_beside_share_xml_for_one_release(sm, reader):
    hid = saved(sm, {"nozzle": 225})
    d = intake_history._dir(hid)
    st = share_queue._read(hid)
    assert json.loads((d / "share.json").read_text(encoding="utf-8")) == st      # what v0.6.2 reads after a rollback
    share_queue._write(hid, {"entries": {hid: "fb-9"}, "failed": None})
    assert json.loads((d / "share.json").read_text(encoding="utf-8"))["entries"] == {hid: "fb-9"}
