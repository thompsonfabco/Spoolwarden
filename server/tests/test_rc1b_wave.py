"""RC1b, the files that waited for the list API (R85 section 1): the pairing files (`devices`, `pending`, `spent`),
`station-requests`, the label read cap (`reads`) and the Label Reader's record and plan (`installed`, `plan`) as XML."""
import errno
import json
import os
import stat
import sys
import time

import pytest

from filament_kit import config, intake, local_intake_install as lii, pairing, station_requests as sr
from filament_kit.profiles import statexml

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes")
DEVICES = [
    {"id": "d1", "name": "Kitchen & <phone>", "hash": "a" * 64, "paired": 1700000000.5, "last_seen": 1700000100.0,
     "expires": None, "claimed": 1700000001.0},
    {"id": "d2", "name": "Bench station", "hash": "0" * 64, "paired": 1.0, "last_seen": 2.0, "expires": 0,
     "claimed": 1.0, "scope": "station", "role": "viewer", "fp": "ab:cd", "port": 8443, "version": "0.6.3",
     "plain_reason": None, "hello_at": 5.0},
]


def state(name):
    return config.paths().state / name


@pytest.fixture(autouse=True)
def clean_problems():
    config.paths().state.mkdir(parents=True, exist_ok=True)
    pairing.make_cert("kit", [], config.paths().etc)
    statexml._unreadable.clear()
    yield
    statexml._unreadable.clear()


# ---------- the pairing files ----------

def test_the_pairing_files_are_xml_and_come_back_as_they_went_in():
    pairing._write("devices.json", DEVICES)
    pairing._write("pending.json", [{"code": "123456", "device": "d1", "expires": 9.5, "attempts": 0}])
    pairing._write("spent.json", [{"hash": "b" * 64, "at": 7.0}])
    for xml, tag in (("devices.xml", "<devices "), ("pending.xml", "<pending "), ("spent.xml", "<spent ")):
        text = state(xml).read_text(encoding="utf-8")
        assert text.startswith("<?xml") and tag in text and 'as="list"' in text
    assert pairing._read("devices.json") == DEVICES
    assert pairing._read("pending.json") == [{"code": "123456", "device": "d1", "expires": 9.5, "attempts": 0}]
    assert pairing._read("spent.json") == [{"hash": "b" * 64, "at": 7.0}]


def test_every_pairing_file_is_also_written_as_json():
    pairing._write("devices.json", DEVICES)
    pairing._write("pending.json", [])
    pairing._write("spent.json", [])
    assert json.loads(state("devices.json").read_text(encoding="utf-8")) == DEVICES      # for `update --rollback`
    assert json.loads(state("pending.json").read_text(encoding="utf-8")) == []
    assert json.loads(state("spent.json").read_text(encoding="utf-8")) == []


def test_a_later_save_keeps_both_forms_equal():
    pairing._write("devices.json", DEVICES)
    pairing._write("devices.json", DEVICES[:1])
    assert json.loads(state("devices.json").read_text(encoding="utf-8")) == DEVICES[:1]
    assert pairing._read("devices.json") == DEVICES[:1]


def test_old_json_files_are_read_and_moved_once_and_left_as_they_were():
    for name, rows in (("devices.json", DEVICES),
                       ("pending.json", [{"code": "1", "device": "d", "expires": 1, "attempts": 2}]),
                       ("spent.json", [{"hash": "c" * 64, "at": 3}])):
        state(name).write_text(json.dumps(rows), encoding="utf-8")
        before = state(name).read_bytes()
        assert pairing._read(name) == rows
        assert state(name.replace(".json", ".xml")).is_file() and state(name).read_bytes() == before
    assert statexml.problems() == []


def test_the_xml_wins_over_a_json_file_that_was_left_behind():
    state("pending.json").write_text(json.dumps([{"code": "old"}]), encoding="utf-8")
    pairing._write("pending.json", [{"code": "new"}])
    assert pairing._read("pending.json") == [{"code": "new"}]


def test_a_damaged_xml_without_a_json_file_reads_empty_is_reported_and_is_never_overwritten():
    state("devices.xml").write_text("<devices version=\"1\" as=\"list\"><item", encoding="utf-8")
    assert pairing._read("devices.json") == []
    assert [p for p in statexml.problems() if "devices.xml" in p]
    pairing._write("devices.json", DEVICES[:1])
    assert state("devices.xml.unreadable").read_text(encoding="utf-8").startswith("<devices")
    assert pairing._read("devices.json") == DEVICES[:1] and statexml.problems() == []


def test_a_damaged_xml_with_a_json_file_reads_the_json():
    state("devices.json").write_text(json.dumps(DEVICES), encoding="utf-8")
    state("devices.xml").write_text("not xml", encoding="utf-8")
    assert pairing._read("devices.json") == DEVICES
    assert state("devices.xml").read_text(encoding="utf-8") == "not xml"       # not replaced by the move


@POSIX
def test_every_pairing_file_is_root_only():
    pairing._write("devices.json", DEVICES)
    pairing._write("pending.json", [])
    pairing._write("spent.json", [])
    pairing._write(sr.FILE, [])
    for name in ("devices.xml", "devices.json", "pending.xml", "spent.xml", "station-requests.xml"):
        assert stat.S_IMODE(os.stat(state(name)).st_mode) == 0o600, name


@POSIX
def test_a_moved_pairing_file_is_root_only_too():
    state("spent.json").write_text("[]", encoding="utf-8")
    os.chmod(state("spent.json"), 0o644)
    pairing._read("spent.json")
    assert stat.S_IMODE(os.stat(state("spent.xml")).st_mode) == 0o600


@POSIX
def test_a_state_file_that_is_a_link_is_never_followed(tmp_path):
    secret = tmp_path / "secret.xml"
    secret.write_text('<devices version="1" as="list"/>', encoding="utf-8")
    os.symlink(secret, state("devices.xml"))
    with pytest.raises(OSError):
        pairing._read("devices.json")


def test_pairing_itself_still_works_over_the_xml_files():
    p = pairing.issue("phone")
    assert pairing.check(p.token)
    code = pairing.issue("b")
    pairing.redeem_code(code.code)
    assert state("devices.xml").is_file() and state("pending.xml").is_file() and state("spent.xml").is_file()
    assert len(pairing.devices()) == 2


# ---------- station requests ----------

def test_station_requests_are_xml_with_their_secrets_root_only():
    rows = [{"id": "0123456789ab", "request_id": "e" * 64, "name": "bench", "token": "t" * 43, "state": "approved",
             "expires": time.time() + 600}]
    pairing._write(sr.FILE, rows)
    assert state("station-requests.xml").is_file() and not state("station-requests.json").exists()
    assert pairing._read(sr.FILE) == rows
    if sys.platform != "win32":
        assert stat.S_IMODE(os.stat(state("station-requests.xml")).st_mode) == 0o600


# ---------- the label read cap ----------

def test_the_cap_is_kept_as_reads_xml_and_counts_across_restarts():
    f = state("reads.json")
    intake.ReadCap(f, 3).take("d1")
    ticket = intake.ReadCap(f, 3).take()
    assert state("reads.xml").is_file() and f.is_file()
    rows = statexml.read_list(f)
    assert [sorted(r) for r in rows] == [["device", "t", "ticket"], ["device", "t", "ticket"]]
    assert rows[1]["device"] is None and intake.reads_today(f) == 2
    intake.ReadCap(f, 3).refund(ticket, None)
    assert intake.reads_today(f) == 1


def test_an_old_reads_json_with_bare_stamps_and_tickets_is_moved_once():
    f = state("reads.json")
    now = time.time()
    f.write_text(json.dumps([now - 10, {"t": now - 5, "ticket": "h", "device": "d"}, now - 90000]), encoding="utf-8")
    assert intake.reads_today(f) == 2
    assert state("reads.xml").is_file() and json.loads(f.read_text(encoding="utf-8"))[0] == now - 10
    cap = intake.ReadCap(f, 2)
    with pytest.raises(intake.CapReached):
        cap.take()


@POSIX
def test_the_cap_file_is_world_readable_for_the_web_unit():
    intake.ReadCap(state("reads.json"), 2).take()
    assert stat.S_IMODE(os.stat(state("reads.xml")).st_mode) == 0o644


def test_a_damaged_reads_xml_counts_nothing_and_is_kept():
    f = state("reads.json")
    state("reads.xml").write_text("<reads", encoding="utf-8")
    assert intake.reads_today(f) == 0
    intake.ReadCap(f, 2).take()
    assert state("reads.xml.unreadable").read_text(encoding="utf-8") == "<reads"
    assert intake.reads_today(f) == 1


# ---------- the Label Reader's record and plan (root's store) ----------

@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FK_LOCAL_INTAKE_STORE", str(tmp_path / "store"))
    return tmp_path / "store"


RECORD = {"version": "0.2.1", "tier": "cpu", "port": 8765, "at": 1700000000, "previous": None}
PLAN = {"at": 1700000000.0, "plan": {"version": "0.2.1", "tier": "cpu", "installed": None,
                                       "tiers": {"cpu": {"minutes": None, "disk_mb": 12}}},
        "text": ["Install the local label reader 0.2.1, cpu tier."]}


def test_the_record_is_xml_with_its_release_number_and_stays_as_json_for_the_uninstaller(store):
    lii._write_state(RECORD)
    text = (store / "installed.xml").read_text(encoding="utf-8")
    assert text.startswith("<?xml") and "<installed " in text and 'release="0.2.1"' in text
    assert json.loads((store / "installed.json").read_text(encoding="utf-8")) == RECORD
    assert lii.installed() == RECORD


def test_the_plan_is_xml_and_json_and_comes_back(store):
    lii._write_json(lii.PLAN, PLAN)
    assert (store / "plan.xml").is_file() and json.loads((store / "plan.json").read_text(encoding="utf-8")) == PLAN
    d = lii.saved_plan(now=1700000010.0)
    assert d["plan"] == PLAN["plan"] and d["fresh"] is True and d["text"] == PLAN["text"]


def test_removing_the_record_removes_both_forms(store):
    lii._write_state(RECORD)
    lii._write_state(None)
    assert not list(store.iterdir()) and lii.installed() is None


def test_an_old_json_record_is_read_and_moved_once_by_root(store):
    store.mkdir()
    (store / "installed.json").write_text(json.dumps(RECORD), encoding="utf-8")
    assert lii.installed() == RECORD
    assert 'release="0.2.1"' in (store / "installed.xml").read_text(encoding="utf-8")
    assert json.loads((store / "installed.json").read_text(encoding="utf-8")) == RECORD


def test_a_reader_with_no_write_permission_reads_the_json_and_records_no_problem(store, monkeypatch):
    store.mkdir()
    (store / "installed.json").write_text(json.dumps(RECORD), encoding="utf-8")
    (store / "plan.json").write_text(json.dumps(PLAN), encoding="utf-8")

    def refuse(path, data, mode, state_owned):
        raise PermissionError(errno.EACCES, "Permission denied")
    monkeypatch.setattr(statexml, "_put", refuse)
    assert lii.installed() == RECORD and lii.saved_plan(now=1700000010.0)["plan"] == PLAN["plan"]
    assert statexml.problems() == [] and not (store / "installed.xml").exists() and not (store / "plan.xml").exists()


@POSIX
@pytest.mark.skipif(getattr(os, "geteuid", lambda: 1)() == 0, reason="root can write anywhere")
def test_a_real_read_only_store_gives_no_false_could_not_be_converted_row(store):
    store.mkdir()
    (store / "installed.json").write_text(json.dumps(RECORD), encoding="utf-8")
    os.chmod(store, 0o555)
    try:
        assert lii.installed() == RECORD
    finally:
        os.chmod(store, 0o755)
    assert statexml.problems() == []


def test_a_damaged_record_xml_falls_back_to_the_json_form_and_is_reported(store):
    store.mkdir()
    (store / "installed.json").write_text(json.dumps(RECORD), encoding="utf-8")
    (store / "installed.xml").write_text("<installed", encoding="utf-8")
    assert lii.installed() == RECORD
    assert [p for p in statexml.problems() if "installed.xml" in p]
    assert (store / "installed.xml").read_text(encoding="utf-8") == "<installed"


@POSIX
def test_the_record_and_plan_are_readable_by_the_service_user(store):
    lii._write_state(RECORD)
    lii._write_json(lii.PLAN, PLAN)
    for name in ("installed.xml", "installed.json", "plan.xml"):
        assert stat.S_IMODE(os.stat(store / name).st_mode) == 0o644, name
