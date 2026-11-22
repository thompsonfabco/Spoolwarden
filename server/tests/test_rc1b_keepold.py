"""RC1b wave, the old (JSON) form kept beside the XML one for one release: every file the wave moved to XML (pairing
devices/pending/spent, station-requests, reads, the Label Reader's record and plan, reader-setup, a kept read's meta)
has its JSON written in the shape v0.6.2 wrote it, kept current, and a change an older release makes to it wins once."""
import hashlib
import json
import os
import stat
import sys
import time

import pytest

from filament_kit import config, intake, local_intake_install as lii, pairing, reader_flash as rf
from filament_kit.profiles import statexml

DEVICES = [{"id": "d1", "name": "Kitchen", "hash": "a" * 64, "paired": 1700000000.5, "last_seen": 1.0,
            "expires": None, "claimed": 1700000001.0}]
RECORD = {"version": "0.2.1", "tier": "cpu", "port": 8765, "at": 1700000000, "previous": None}
PLAN = {"at": 1700000000.0, "plan": {"version": "0.2.1", "tier": "cpu"}, "text": ["Install 0.2.1."]}
ANSWERS = {"ssid": "Home & <Net>", "pass": "12345678", "buzzer": "high", "port": "/dev/ttyACM0", "name": "bench"}


def state(name):
    return config.paths().state / name


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


@pytest.fixture(autouse=True)
def clean():
    config.paths().state.mkdir(parents=True, exist_ok=True)
    pairing.make_cert("kit", [], config.paths().etc)
    statexml._unreadable.clear()
    yield
    statexml._unreadable.clear()


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("FK_LOCAL_INTAKE_STORE", str(tmp_path / "store"))
    return tmp_path / "store"


def sha_ok(path):
    side = path.with_name(path.name + ".sha256").read_text(encoding="ascii").strip()
    return side == hashlib.sha256(path.read_bytes()).hexdigest()


# ---------- pairing and station requests ----------

@pytest.mark.parametrize("name,rows", [
    ("devices.json", DEVICES),
    ("pending.json", [{"code": "123456", "device": "d1", "expires": 9.5, "attempts": 0}]),
    ("spent.json", [{"hash": "b" * 64, "at": 7.0}]),
    ("station-requests.json", [{"id": "r1", "at": 5.0}])])
def test_each_pairing_file_has_its_json_as_v062_wrote_it(name, rows):
    pairing._write(name, rows)
    assert state(name).read_bytes() == json.dumps(rows, indent=2).encode("utf-8")    # indent 2, no newline
    assert sha_ok(state(name))
    if sys.platform != "win32":
        assert mode(state(name)) == 0o600


def test_the_json_follows_every_save():
    pairing._write("devices.json", DEVICES)
    pairing._write("devices.json", [])
    assert json.loads(state("devices.json").read_text(encoding="utf-8")) == []
    assert sha_ok(state("devices.json"))


def test_a_pairing_an_older_release_added_wins_once():
    pairing._write("devices.json", DEVICES)
    newer = DEVICES + [{"id": "d9", "name": "Added after rollback", "hash": "c" * 64, "paired": 2.0, "last_seen": 2.0,
                        "expires": None, "claimed": 2.0}]
    state("devices.json").write_text(json.dumps(newer, indent=2), encoding="utf-8")      # v0.6.2's _write
    assert pairing._read("devices.json") == newer
    assert "Added after rollback" in state("devices.xml").read_text(encoding="utf-8")
    assert pairing._read("devices.json") == newer                                        # once: now the XML is current
    pairing._write("devices.json", DEVICES)
    assert pairing._read("devices.json") == DEVICES


# ---------- reads ----------

def test_the_reads_json_is_v062s_compact_list():
    f = state("reads.json")
    cap = intake.ReadCap(f, 5)
    cap.take()
    ticket = cap.take("d1")
    raw = json.loads(f.read_text(encoding="utf-8"))
    assert isinstance(raw[0], dict) and set(raw[0]) >= {"t", "ticket"}      # a ticketed stamp stays a dict, as v0.6.2
    assert f.read_bytes() == json.dumps(raw).encode("utf-8") and sha_ok(f)
    assert ticket and intake.reads_today(f) == 2
    if sys.platform != "win32":
        assert mode(f) == 0o644


def test_a_stamp_with_no_ticket_is_a_bare_number_in_the_json():
    f = state("reads.json")
    statexml.write_list(f, [{"t": time.time()}], 0o644, old_format=intake._old_stamps)
    assert isinstance(json.loads(f.read_text(encoding="utf-8"))[0], float)


def test_a_read_an_older_release_counted_is_in_the_cap():
    f = state("reads.json")
    intake.ReadCap(f, 2).take()
    stamps = json.loads(f.read_text(encoding="utf-8")) + [time.time()]
    f.write_text(json.dumps(stamps), encoding="utf-8")                   # v0.6.2 took one more read
    assert intake.reads_today(f) == 2
    with pytest.raises(intake.CapReached):
        intake.ReadCap(f, 2).take()


# ---------- the Label Reader's record and plan ----------

def test_the_record_json_has_its_own_version_key_in_v062s_shape(store):
    lii._write_state(RECORD)
    assert (store / "installed.json").read_bytes() == json.dumps(RECORD, indent=2).encode("utf-8")
    assert sha_ok(store / "installed.json") and "release" not in json.loads((store / "installed.json").read_text("utf-8"))
    if sys.platform != "win32":
        assert mode(store / "installed.json") == 0o644
    lii._write_state({**RECORD, "version": "0.2.2", "previous": "0.2.1"})
    assert json.loads((store / "installed.json").read_text("utf-8"))["version"] == "0.2.2"
    assert sha_ok(store / "installed.json")


def test_the_plan_json_is_v062s_shape(store):
    lii._write_json(lii.PLAN, PLAN)
    assert (store / "plan.json").read_bytes() == json.dumps(PLAN, indent=2).encode("utf-8") and sha_ok(store / "plan.json")


def test_a_record_an_older_release_changed_wins_once(store):
    lii._write_state(RECORD)
    older = {**RECORD, "version": "0.2.0", "previous": None, "at": 1700009999}      # an `update --rollback` reinstall
    (store / "installed.json").write_text(json.dumps(older, indent=2), encoding="utf-8")
    assert lii.installed() == older
    assert 'release="0.2.0"' in (store / "installed.xml").read_text(encoding="utf-8")
    assert sha_ok(store / "installed.json")
    assert lii.installed() == older
    lii._write_state(RECORD)
    assert lii.installed() == RECORD


def test_an_old_json_record_moved_by_root_records_its_hash_so_a_later_change_wins(store):
    store.mkdir()
    (store / "installed.json").write_text(json.dumps(RECORD, indent=2), encoding="utf-8")
    before = (store / "installed.json").read_bytes()
    assert lii.installed() == RECORD
    assert (store / "installed.json").read_bytes() == before and sha_ok(store / "installed.json")
    (store / "installed.json").write_text(json.dumps({**RECORD, "version": "0.2.5"}, indent=2), encoding="utf-8")
    assert lii.installed()["version"] == "0.2.5"


def test_the_plan_an_older_release_wrote_wins_once(store):
    lii._write_json(lii.PLAN, PLAN)
    (store / "plan.json").write_text(json.dumps({**PLAN, "text": ["older"]}, indent=2), encoding="utf-8")
    assert lii.saved_plan(now=1700000010.0)["text"] == ["older"]


# ---------- reader-setup and a kept read's meta ----------

def test_the_setup_answers_have_the_compact_json_v062_wrote():
    rf.write_pending(ANSWERS)
    old = rf.pending_path().with_name("reader-setup.json")
    assert old.read_bytes() == json.dumps(ANSWERS).encode("utf-8")
    if sys.platform != "win32":
        assert mode(old) == 0o600
    assert rf.take_pending() == ANSWERS
    assert not old.exists() and not rf.pending_path().exists()


def test_a_kept_read_has_its_meta_json_as_v062_wrote_it(tmp_path, monkeypatch):
    monkeypatch.setenv("FK_FAILED_DIR", str(tmp_path / "failed"))
    intake._keep("r1", [b"\xff\xd8x"], "a note", "04:A2")
    d = tmp_path / "failed" / "r1"
    assert (d / "meta.json").read_bytes() == json.dumps({"hint": "a note", "tag_uid": "04:A2"}).encode("utf-8")
    if sys.platform != "win32":
        assert mode(d / "meta.json") == 0o600
