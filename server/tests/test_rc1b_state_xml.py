"""RC1b (R85 section 1): the Tag Reader's pending setup answers (`reader-setup`) and a kept failed read's note (`meta`)
as XML. The moves of the other RC1b files (pairing, reads, station requests, the Label Reader's record and plan) wait
for the shared helper to take a top-level list and a root-only store (see the step's handoff)."""
import json
import logging
import os
import stat
import sys
import time

import pytest

from filament_kit import config, intake, reader_flash as rf
from filament_kit.profiles import statexml

ANSWERS = {"ssid": "Home & <Net>", "pass": "12345678", "buzzer": "high", "port": "/dev/ttyACM0", "name": "bench"}


# ---------- reader-setup ----------

def test_the_answers_are_xml_and_come_back_as_they_went_in():
    rf.write_pending(ANSWERS)
    path = rf.pending_path()
    assert path.name == "reader-setup.xml" and not path.with_name("reader-setup.json").exists()
    text = path.read_text(encoding="utf-8")
    assert text.startswith("<?xml") and "<reader-setup " in text and "{" not in text
    assert rf.take_pending() == ANSWERS


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_the_answers_file_is_root_only():
    rf.write_pending(ANSWERS)
    assert stat.S_IMODE(os.stat(rf.pending_path()).st_mode) == 0o600


def test_taking_deletes_both_forms_and_a_second_take_has_nothing():
    rf.write_pending(ANSWERS)
    assert rf.take_pending() is not None
    assert not rf.pending_path().exists() and rf.take_pending() is None


def test_writing_again_replaces_the_answers_and_an_old_json_file():
    old = rf.pending_path().with_name("reader-setup.json")
    old.write_text(json.dumps({"ssid": "stale"}), encoding="utf-8")
    rf.write_pending(ANSWERS)
    assert not old.exists()
    assert rf.take_pending()["ssid"] == ANSWERS["ssid"]


def test_answers_left_in_the_old_json_form_are_taken_once():
    old = rf.pending_path().with_name("reader-setup.json")
    old.write_text(json.dumps(ANSWERS), encoding="utf-8")
    assert rf.take_pending() == ANSWERS
    assert not old.exists() and rf.take_pending() is None


def test_the_xml_form_wins_over_an_old_json_file():
    rf.write_pending(ANSWERS)
    rf.pending_path().with_name("reader-setup.json").write_text(json.dumps({"ssid": "stale"}), encoding="utf-8")
    assert rf.take_pending() == ANSWERS


def test_old_answers_are_dropped():
    rf.write_pending(ANSWERS)
    old = time.time() - rf.PENDING_MAX_AGE - 5
    os.utime(rf.pending_path(), (old, old))
    assert rf.take_pending() is None and not rf.pending_path().exists()


def test_damaged_answers_never_crash_log_the_reason_not_the_password_and_are_deleted(caplog):
    rf.pending_path().write_text("<reader-setup pass='12345678'><oops", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="filament_kit.reader_flash"):
        assert rf.take_pending() is None
    assert "reader-setup.xml could not be read" in caplog.text and "12345678" not in caplog.text
    assert not rf.pending_path().exists()


def test_answers_of_another_root_element_are_not_taken():
    rf.pending_path().write_bytes(statexml.dumps("something-else", ANSWERS))
    assert rf.take_pending() is None


# ---------- a kept read's meta ----------

def _kept(tmp_path, monkeypatch):
    monkeypatch.setenv("FK_FAILED_DIR", str(tmp_path / "failed"))
    intake._keep("r1", [b"\xff\xd8x"], "note\nwith a break & <angle>", "04:A2:B3")
    return tmp_path / "failed" / "r1"


def test_a_kept_read_has_its_note_as_xml_and_reads_it_back(tmp_path, monkeypatch):
    d = _kept(tmp_path, monkeypatch)
    assert (d / "meta.xml").read_text(encoding="utf-8").startswith("<?xml") and not (d / "meta.json").exists()
    assert intake._kept_meta(d) == {"hint": "note\nwith a break & <angle>", "tag_uid": "04:A2:B3"}


def test_a_kept_read_without_a_tag_keeps_the_empty_value(tmp_path, monkeypatch):
    monkeypatch.setenv("FK_FAILED_DIR", str(tmp_path / "failed"))
    intake._keep("r2", [b"x"], "", None)
    assert intake._kept_meta(tmp_path / "failed" / "r2") == {"hint": "", "tag_uid": None}


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
def test_the_note_is_owner_only(tmp_path, monkeypatch):
    d = _kept(tmp_path, monkeypatch)
    assert stat.S_IMODE(os.stat(d / "meta.xml").st_mode) == 0o600


def test_a_read_kept_before_the_move_still_retries_with_its_json_note(tmp_path, monkeypatch):
    d = _kept(tmp_path, monkeypatch)
    (d / "meta.xml").unlink()
    (d / "meta.json").write_text(json.dumps({"hint": "old", "tag_uid": "AA"}), encoding="utf-8")
    assert intake._kept_meta(d) == {"hint": "old", "tag_uid": "AA"}


def test_a_damaged_note_logs_and_falls_back_to_the_json_one_then_to_nothing(tmp_path, monkeypatch, caplog):
    d = _kept(tmp_path, monkeypatch)
    (d / "meta.xml").write_text("<meta><broken", encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"hint": "old"}), encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger="filament_kit.intake"):
        assert intake._kept_meta(d) == {"hint": "old"}
        (d / "meta.json").write_text("{nope", encoding="utf-8")
        assert intake._kept_meta(d) == {}
    assert "meta.xml could not be read" in caplog.text


def test_retry_passes_the_xml_note_on(tmp_path, monkeypatch):
    d = _kept(tmp_path, monkeypatch)
    seen = {}
    monkeypatch.setattr(intake, "read_labels", lambda sm, images, hint, tag_uid, **k: seen.update(hint=hint, tag=tag_uid) or {})
    intake.retry("r1", None)
    assert seen == {"hint": "note\nwith a break & <angle>", "tag": "04:A2:B3"}
