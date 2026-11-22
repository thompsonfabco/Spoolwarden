"""RC1e: the Bambu Lab filament-used log is one XML file per month per printer (`usage/<yyyy-mm>.xml`): the round trip,
the old usage.jsonl read once and left alone, a damaged month file, undo and its events, modes, and the reach of the
reads (analytics reads every month)."""
import errno
import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from filament_kit import config, event_sink
from filament_kit.bridge import usage
from filament_kit.profiles import statexml
from filament_kit.spoolman import SpoolmanError

SERIAL = "0948ZZ0A0000001"
NOW = 1_795_000_000.0       # 2026-11-18T10:26:40Z


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FK_STATE", str(tmp_path / "state"))
    monkeypatch.setattr(usage, "_now", lambda: NOW)
    statexml._unreadable.clear()


def _dir():
    return config.paths().state / "printers" / SERIAL


def _entry(eid, at, **kw):
    return {"id": eid, "at": at, "job": "job", "grams": 12.5, "spool_id": 7, "slot": "A1", "observe": False, **kw}


E1 = "a" * 32
E2 = "1" * 32       # all digits: must come back as text
E3 = "c" * 32


class FakeSpoolman:
    def __init__(self, fail=False):
        self.fail, self.used = fail, []

    def get(self, path):
        return {"registered": "2026-01-01T00:00:00Z"}

    def use(self, spool_id, grams):
        if self.fail:
            raise SpoolmanError("down", 503)
        self.used.append((spool_id, grams))


def test_append_writes_one_xml_file_per_month_and_reads_back():
    r = usage.append(SERIAL, {"job": "123", "grams": 3.0, "spool_id": None, "slot": None, "observe": True,
                              "material": "PLA", "vendor": "V", "name": "Basic", "color_hex": "123456",
                              "printer_name": "P", "outcome": "finished"})
    f = _dir() / "usage" / "2026-11.xml"
    assert f.is_file()
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 or os.name == "nt"
    assert b"<usage " in f.read_bytes()
    [e] = usage.entries(SERIAL, days=30)
    assert e["id"] == r["id"] and e["job"] == "123" and e["grams"] == 3.0 and e["color_hex"] == "123456"
    assert e["observe"] is True and e["outcome"] == "finished" and e["spool_id"] is None and e["undone"] is False


def test_months_are_separate_files_and_all_are_read(monkeypatch):
    for at, eid in (("2026-09-30T23:00:00Z", E1), ("2026-11-01T00:00:01Z", E2)):
        usage._write_line(SERIAL, _entry(eid, at))
    names = sorted(p.name for p in (_dir() / "usage").iterdir() if not p.name.startswith("."))
    assert names == ["2026-09.xml", "2026-11.xml"]
    got = usage.entries(SERIAL, days=10_000, by="month")["months"]
    assert [m["month"] for m in got] == ["2026-11", "2026-09"]
    assert [e["id"] for e in usage.entries(SERIAL, days=30)] == [E2]        # the days limit still applies


def test_ids_that_look_like_numbers_survive():
    usage._write_line(SERIAL, _entry(E2, "2026-11-02T00:00:00Z", job="007"))
    [e] = usage.entries(SERIAL, days=30)
    assert e["id"] == E2 and e["job"] == "007"


def test_old_jsonl_is_read_once_moved_and_left_unchanged():
    d = _dir()
    d.mkdir(parents=True)
    lines = [_entry(E1, "2026-10-30T10:00:00Z"), _entry(E2, "2026-11-10T10:00:00Z"),
             {"undo": E2, "at": "2026-11-11T10:00:00Z"}, _entry(E3, "2026-11-12T10:00:00Z"),
             {"undo": E3, "at": "2026-11-12T11:00:00Z"}, {"undo_failed": E3, "at": "2026-11-12T11:00:05Z"}]
    text = "".join(json.dumps(x) + "\n" for x in lines) + '{"id": "torn'
    (d / "usage.jsonl").write_text(text, encoding="utf-8")
    got = {e["id"]: e for e in usage.entries(SERIAL, days=10_000)}
    assert set(got) == {E1, E2, E3} and got[E2]["undone"] and not got[E3]["undone"] and not got[E1]["undone"]
    assert (d / "usage.jsonl").read_text(encoding="utf-8") == text          # the old file is as it was
    assert sorted(p.name for p in (d / "usage").iterdir() if p.name != ".lock") == [".migrated", "2026-10.xml", "2026-11.xml"]
    usage.append(SERIAL, {"job": "new"})
    assert len(usage.entries(SERIAL, days=10_000)) == 4                      # read once: no entry twice
    assert len(usage.entries(SERIAL, days=10_000)) == 4


def test_a_stop_half_way_through_the_move_is_finished_without_doubles(monkeypatch):
    d = _dir()
    d.mkdir(parents=True)
    (d / "usage.jsonl").write_text(json.dumps(_entry(E1, "2026-11-10T10:00:00Z")) + "\n", encoding="utf-8")
    real = usage._write_atomic

    def broken(path, *a, **kw):
        if path.name == usage.MIGRATED:
            raise OSError("disk full")
        return real(path, *a, **kw)
    monkeypatch.setattr(usage, "_write_atomic", broken)
    assert [e["id"] for e in usage.entries(SERIAL, days=10_000)] == [E1]     # read from memory meanwhile
    monkeypatch.setattr(usage, "_write_atomic", real)
    assert [e["id"] for e in usage.entries(SERIAL, days=10_000)] == [E1]
    assert (d / "usage" / usage.MIGRATED).exists()


def test_damaged_month_file_is_read_as_empty_kept_and_reported():
    usage._write_line(SERIAL, _entry(E1, "2026-11-02T00:00:00Z"))
    f = _dir() / "usage" / "2026-11.xml"
    f.write_bytes(b"<usage version='1'><entries as='list'><item id=")
    assert usage.entries(SERIAL, days=30) == []                                      # no crash
    assert any("2026-11.xml" in p for p in statexml.problems())
    usage._write_line(SERIAL, _entry(E3, "2026-11-03T00:00:00Z"))            # the next write keeps the damaged file
    assert (f.with_name("2026-11.xml.unreadable")).read_bytes().startswith(b"<usage version='1'>")
    assert [e["id"] for e in usage.entries(SERIAL, days=30)] == [E3]


def test_undo_goes_to_the_entry_month_and_emits_the_event(monkeypatch):
    seen = []
    monkeypatch.setattr(event_sink, "emit", lambda *a, **k: seen.append((a, k)))
    usage._write_line(SERIAL, _entry(E1, "2026-11-14T10:00:00Z"))
    sm = FakeSpoolman()
    e = usage.undo(E1, sm)
    assert e["undone"] and sm.used == [(7, -12.5)]
    assert seen and seen[0][0][0] == "usage.undone"
    assert [x["undone"] for x in usage.entries(SERIAL)] == [True]
    with pytest.raises(usage.AlreadyUndone):
        usage.undo(E1, sm)


def test_a_failed_credit_takes_the_undo_back():
    usage._write_line(SERIAL, _entry(E1, "2026-11-14T10:00:00Z"))
    with pytest.raises(SpoolmanError):
        usage.undo(E1, FakeSpoolman(fail=True))
    assert [x["undone"] for x in usage.entries(SERIAL)] == [False]
    usage.undo(E1, FakeSpoolman())
    assert [x["undone"] for x in usage.entries(SERIAL)] == [True]


def test_undo_of_an_entry_in_the_previous_month():
    at = "2026-11-12T10:00:00Z"
    usage._write_line(SERIAL, _entry(E1, at))
    usage._write_line(SERIAL, {"undo": E1, "at": "2026-12-01T00:00:00Z"})   # an undo dated in the next month
    data = statexml.loads((_dir() / "usage" / "2026-11.xml").read_bytes(), "usage")
    assert data["undone"] == [{"entry": E1, "at": "2026-12-01T00:00:00Z"}]
    assert not (_dir() / "usage" / "2026-12.xml").exists()


def test_a_byte_that_is_not_utf8_in_the_old_log_does_not_stop_appends():
    d = _dir()
    d.mkdir(parents=True)
    (d / "usage.jsonl").write_bytes(json.dumps(_entry(E1, "2026-11-10T10:00:00Z")).encode() + b"\n\xff\xfe\n")
    r = usage.append(SERIAL, {"job": "new"})                 # was: UnicodeDecodeError out of the move, every time
    got = {e["id"] for e in usage.entries(SERIAL, days=30)}
    assert got == {E1, r["id"]}
    assert (d / "usage" / usage.MIGRATED).exists()


def test_a_damaged_month_file_is_logged_once_and_stays_noted_after_it_is_kept(caplog):
    usage._write_line(SERIAL, _entry(E1, "2026-11-02T00:00:00Z"))
    f = _dir() / "usage" / "2026-11.xml"
    f.write_bytes(b"<usage version='1'><entries as='list'><item id=")
    with caplog.at_level("WARNING"):
        usage.entries(SERIAL, days=30)
        usage.entries(SERIAL, days=30)
    assert len([r for r in caplog.records if f"usage: {f}:" in r.getMessage()]) == 1       # once, not per read
    assert any(p.startswith(str(f) + " (") for p in statexml.problems())
    usage._write_line(SERIAL, _entry(E3, "2026-11-03T00:00:00Z"))
    kept = str(f) + ".unreadable"
    assert any(p.startswith(kept + " (") and "kept aside" in p for p in statexml.problems())     # the owner still sees it
    assert not any(p.startswith(str(f) + " (") for p in statexml.problems())


def test_a_failed_move_is_logged_once_and_noted_until_it_works(monkeypatch, caplog):
    d = _dir()
    d.mkdir(parents=True)
    (d / "usage.jsonl").write_text(json.dumps(_entry(E1, "2026-11-10T10:00:00Z")) + "\n", encoding="utf-8")
    real = usage._write_atomic

    def broken(path, *a, **kw):
        if path.name == usage.MIGRATED:
            raise OSError("disk full")
        return real(path, *a, **kw)
    monkeypatch.setattr(usage, "_write_atomic", broken)
    with caplog.at_level("WARNING"):
        for _ in range(3):
            usage.entries(SERIAL, days=10_000)
    assert len([r for r in caplog.records if "usage.jsonl" in r.getMessage()]) == 1
    [p] = [p for p in statexml.problems() if "usage.jsonl" in p]
    assert "could not be converted" in p and "disk full" in p
    monkeypatch.setattr(usage, "_write_atomic", real)
    usage.entries(SERIAL, days=10_000)
    assert not any("usage.jsonl" in p for p in statexml.problems())            # moved: nothing to report


try:
    import fcntl
except ImportError:
    fcntl = None
needs_flock = pytest.mark.skipif(fcntl is None, reason="flock: Linux only")


def _sub_env():
    import filament_kit
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join([str(Path(filament_kit.__file__).parent.parent), env.get("PYTHONPATH", "")])
    return env


@needs_flock
def test_a_move_waits_for_another_process_that_holds_the_usage_lock():
    d = _dir()
    d.mkdir(parents=True)
    (d / "usage.jsonl").write_text(json.dumps(_entry(E1, "2026-11-10T10:00:00Z")) + "\n", encoding="utf-8")
    (d / "usage").mkdir()
    holder = subprocess.Popen(
        [sys.executable, "-I", "-c",
         "import fcntl,sys;f=open(sys.argv[1],'a');fcntl.flock(f,fcntl.LOCK_EX);print('held',flush=True);sys.stdin.read()",
         str(d / "usage" / ".lock")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        done = threading.Event()
        worker = threading.Thread(target=lambda: (usage.append(SERIAL, {"job": "new"}), done.set()), daemon=True)
        worker.start()
        assert not done.wait(0.7)                         # was: moved and wrote at once, under no cross-process lock
        assert not (d / "usage" / usage.MIGRATED).exists()
    finally:
        holder.stdin.close()
        holder.wait(10)
    worker.join(10)
    assert done.is_set() and (d / "usage" / usage.MIGRATED).exists()


@needs_flock
def test_two_processes_making_the_first_appends_lose_nothing():
    d = _dir()
    d.mkdir(parents=True)
    (d / "usage.jsonl").write_text(json.dumps(_entry(E1, "2026-11-10T10:00:00Z")) + "\n", encoding="utf-8")
    code = ("import sys\nfrom filament_kit.bridge import usage\n"
            "for i in range(30): usage.append('%s', {'job': sys.argv[1] + str(i), 'at': '2026-11-12T10:00:00Z'})\n" % SERIAL)
    procs = [subprocess.Popen([sys.executable, "-c", code, tag], env=_sub_env()) for tag in ("p", "q")]
    assert [p.wait(60) for p in procs] == [0, 0]
    recs, _ = usage._read(SERIAL)
    assert sorted(r["job"] for r in recs if r["id"] != E1) == sorted(t + str(i) for t in "pq" for i in range(30))
    assert len({r["id"] for r in recs}) == 61


def _v062_read(serial):
    """The release before (v0.6.2) read usage.jsonl like this: entries in file order, {undone id: time}."""
    try:
        text = (_dir() / "usage.jsonl").read_text(encoding="utf-8")
    except FileNotFoundError:
        return [], {}
    out, undone = [], {}
    for line in text.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if not isinstance(rec, dict):
            continue
        if "undo" in rec:
            undone.setdefault(str(rec["undo"]), rec.get("at"))
        elif "undo_failed" in rec:
            undone.pop(str(rec["undo_failed"]), None)
        elif isinstance(rec.get("id"), str):
            out.append(rec)
    return out, undone


def test_the_old_log_is_kept_current_for_a_rollback():
    r = usage.append(SERIAL, {"job": "a", "grams": 4.0, "spool_id": 7, "slot": "A1", "observe": False})
    recs, undone = _v062_read(SERIAL)
    assert [x["id"] for x in recs] == [r["id"]] and recs[0]["grams"] == 4.0 and undone == {}
    usage.undo(r["id"], FakeSpoolman())
    recs, undone = _v062_read(SERIAL)
    assert list(undone) == [r["id"]]                       # an older release shows it undone, and cannot debit twice
    with pytest.raises(SpoolmanError):
        r2 = usage.append(SERIAL, {"job": "b", "grams": 1.0, "spool_id": 7, "slot": "A1", "observe": False})
        usage.undo(r2["id"], FakeSpoolman(fail=True))
    recs, undone = _v062_read(SERIAL)
    assert [x["job"] for x in recs] == ["a", "b"] and list(undone) == [r["id"]]      # the failed credit took its undo back


def test_the_old_log_after_the_move_gets_the_new_entries_too_and_is_not_read_twice():
    d = _dir()
    d.mkdir(parents=True)
    (d / "usage.jsonl").write_text(json.dumps(_entry(E1, "2026-11-10T10:00:00Z")) + "\n", encoding="utf-8")
    r = usage.append(SERIAL, {"job": "new"})
    assert [x["id"] for x in _v062_read(SERIAL)[0]] == [E1, r["id"]]
    assert sorted(e["id"] for e in usage.entries(SERIAL, days=10_000)) == sorted([E1, r["id"]])      # no entry twice


def _v062_line(rec):
    """What v0.6.2's _write_line appends to usage.jsonl."""
    with open(_dir() / "usage.jsonl", "ab") as fh:
        fh.write(json.dumps(rec, separators=(",", ":")).encode() + b"\n")


def _new_entry_and_undo_by_the_older_release():
    r = usage.append(SERIAL, {"job": "a", "grams": 4.0, "spool_id": 7, "slot": "A1", "observe": False})
    _v062_line({"undo": r["id"], "at": usage._iso(usage._now())})
    return r


def test_an_undo_made_by_the_older_release_is_seen_after_updating_again():
    r = _new_entry_and_undo_by_the_older_release()
    sm = FakeSpoolman()
    with pytest.raises(usage.AlreadyUndone):
        usage.undo(r["id"], sm)
    assert sm.used == []


def test_entries_and_undo_marks_the_older_release_wrote_after_a_rollback_are_taken_in_once():
    r = usage.append(SERIAL, {"job": "a", "grams": 4.0, "spool_id": 7, "slot": "A1", "observe": False})
    _v062_line(_entry(E1, "2026-11-15T10:00:00Z", job="rolled-back"))
    _v062_line({"undo": r["id"], "at": "2026-11-15T11:00:00Z"})
    _v062_line({"undo": E1, "at": "2026-11-15T12:00:00Z"})
    _v062_line({"undo_failed": E1, "at": "2026-11-15T12:00:05Z"})
    for _ in range(3):
        got = {e["id"]: e for e in usage.entries(SERIAL, days=30)}
        assert set(got) == {r["id"], E1} and got[r["id"]]["undone"] and not got[E1]["undone"]
    assert got[E1]["job"] == "rolled-back"
    assert sum(1 for e in usage.entries(SERIAL, days=30) if e["id"] == E1) == 1
    usage.append(SERIAL, {"job": "b"})                     # our own line after theirs: not read back as new
    assert len(usage.entries(SERIAL, days=30)) == 3
    assert usage.undo(E1, FakeSpoolman())["undone"]
    assert [e["undone"] for e in usage.entries(SERIAL, days=30) if e["id"] == E1] == [True]


def test_a_steady_state_read_does_not_open_the_old_log(monkeypatch):
    usage.append(SERIAL, {"job": "a"})
    calls = []
    monkeypatch.setattr(usage, "_read_old", lambda serial: calls.append(serial))
    usage.entries(SERIAL, days=30)
    assert calls == []


def test_a_truncated_old_log_is_read_again_from_its_start_without_doubles():
    r = usage.append(SERIAL, {"job": "a", "grams": 4.0, "spool_id": 7, "slot": "A1", "observe": False})
    usage.append(SERIAL, {"job": "b"})
    f = _dir() / "usage.jsonl"
    f.write_bytes(f.read_bytes().splitlines(True)[0])       # the second line is gone: shorter than what was taken in
    _v062_line(_entry(E1, "2026-11-15T10:00:00Z"))
    _v062_line({"undo": r["id"], "at": "2026-11-15T11:00:00Z"})
    got = {e["id"]: e for e in usage.entries(SERIAL, days=30)}
    assert len(usage.entries(SERIAL, days=30)) == 3 and set(got) >= {r["id"], E1} and got[r["id"]]["undone"]


def test_a_rewritten_old_log_of_the_same_length_is_read_again_without_doubles():
    r = usage.append(SERIAL, {"job": "a"})
    f = _dir() / "usage.jsonl"
    raw = f.read_bytes()
    other = _entry(E3, "2026-11-15T10:00:00Z")
    f.write_bytes(json.dumps(other).encode() + b"\n" + raw)       # longer, and the first bytes are not the ones taken in
    got = [e["id"] for e in usage.entries(SERIAL, days=30)]
    assert sorted(got) == sorted([r["id"], E3])


def test_the_marker_counts_the_bytes_taken_in():
    usage.append(SERIAL, {"job": "a"})
    raw = (_dir() / "usage.jsonl").read_bytes()
    text = (_dir() / "usage" / usage.MIGRATED).read_text()
    assert text == f"usage.jsonl taken in: {len(raw)} {hashlib.sha256(raw).hexdigest()}\n"


def test_a_marker_of_the_earlier_form_is_read_as_nothing_taken_in():
    d = _dir()
    (d / "usage").mkdir(parents=True)
    (d / "usage" / usage.MIGRATED).write_text("moved from usage.jsonl\n")
    (d / "usage.jsonl").write_text(json.dumps(_entry(E1, "2026-11-10T10:00:00Z")) + "\n", encoding="utf-8")
    assert [e["id"] for e in usage.entries(SERIAL, days=10_000)] == [E1]
    assert usage.entries(SERIAL, days=10_000) and (d / "usage" / usage.MIGRATED).read_text().startswith("usage.jsonl taken in: ")


class _NoSpaceOs:
    """usage.os with an fsync that fails (the disk is full)."""
    def __getattr__(self, name):
        return getattr(os, name)

    def fsync(self, fd):
        raise OSError(errno.ENOSPC, "No space left on device")


def test_a_failed_append_to_the_old_log_is_logged_once_and_noted(monkeypatch, caplog):
    with monkeypatch.context() as m, caplog.at_level("WARNING"):
        m.setattr(usage, "os", _NoSpaceOs())
        for i in range(3):
            usage.append(SERIAL, {"job": f"j{i}"})
    assert len([r for r in caplog.records if "usage.jsonl" in r.getMessage()]) == 1
    [p] = [p for p in statexml.problems() if "usage.jsonl" in p]
    assert "No space left" in p and "rollback" in p
    usage.append(SERIAL, {"job": "ok"})                    # the entries were kept in the XML meanwhile
    assert len(usage.entries(SERIAL, days=30)) == 4


def test_on_a_fresh_old_log_the_line_is_written_before_the_marker(monkeypatch):
    real = usage._write_atomic

    def broken(path, *a, **kw):
        if path.name == usage.MIGRATED:
            raise OSError("disk full")
        return real(path, *a, **kw)
    monkeypatch.setattr(usage, "_write_atomic", broken)
    r = usage.append(SERIAL, {"job": "a"})
    assert [x["id"] for x in _v062_read(SERIAL)[0]] == [r["id"]]        # the rollback copy has the entry
    monkeypatch.setattr(usage, "_write_atomic", real)
    assert [e["id"] for e in usage.entries(SERIAL, days=30)] == [r["id"]]      # and a lost marker costs no double


def _holder(d):
    """A process that holds usage/.lock until its stdin is closed."""
    (d / "usage").mkdir(parents=True, exist_ok=True)
    h = subprocess.Popen(
        [sys.executable, "-I", "-c",
         "import fcntl,sys;f=open(sys.argv[1],'a');fcntl.flock(f,fcntl.LOCK_EX);print('held',flush=True);sys.stdin.read()",
         str(d / "usage" / ".lock")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert h.stdout.readline().strip() == "held"
    return h


def _release(h):
    h.stdin.close()
    h.wait(10)


def _in_thread(fn, *a):
    out = {}
    t = threading.Thread(target=lambda: out.update(r=fn(*a)), daemon=True)
    t.start()
    return t, out


@needs_flock
def test_a_lock_held_too_long_keeps_the_record_pending_and_the_next_locked_write_appends_it(monkeypatch):
    d = _dir()
    d.mkdir(parents=True)
    (d / "usage.jsonl").write_text(json.dumps(_entry(E1, "2026-11-10T10:00:00Z")) + "\n", encoding="utf-8")
    monkeypatch.setattr(usage, "LOCK_WAIT", 0.3, raising=False)
    holder = _holder(d)
    try:
        t, out = _in_thread(usage.append, SERIAL, {"job": "new"})
        t.join(5)
        assert "r" in out                                  # was: waited for the holder with no limit
        assert sorted(e["id"] for e in usage.entries(SERIAL, days=10_000)) == sorted([E1, out["r"]["id"]])
        assert [x["id"] for x in _v062_read(SERIAL)[0]] == [E1]              # not in usage.jsonl yet ...
        assert (d / "usage" / usage.PENDING).is_file()                       # ... but kept, in a file that survives a restart
        assert any("usage.jsonl" in p and "rollback" in p for p in statexml.problems())
    finally:
        _release(holder)
    later = usage.append(SERIAL, {"job": "later"})
    assert [x["id"] for x in _v062_read(SERIAL)[0]] == [E1, out["r"]["id"], later["id"]]        # both lines, in order
    assert not (d / "usage" / usage.PENDING).exists() and not (d / "usage" / usage.DRAINING).exists()
    assert not any("usage.jsonl" in p for p in statexml.problems())
    assert sorted(e["id"] for e in usage.entries(SERIAL, days=10_000)) == sorted([E1, out["r"]["id"], later["id"]])


@needs_flock
def test_an_undo_made_while_the_lock_was_busy_reaches_usage_jsonl_and_the_note_stays_until_then(monkeypatch):
    r = usage.append(SERIAL, {"job": "a", "grams": 4.0, "spool_id": 7, "slot": "A1", "observe": False})
    monkeypatch.setattr(usage, "LOCK_WAIT", 0.3, raising=False)
    holder = _holder(_dir())
    sm = FakeSpoolman()
    try:
        t, out = _in_thread(usage.undo, r["id"], sm)
        t.join(5)
        assert out["r"]["undone"] and sm.used == [(7, -4.0)]
        with pytest.raises(usage.AlreadyUndone):           # the pending undo is in force at once
            usage.undo(r["id"], sm)
        assert [e["undone"] for e in usage.entries(SERIAL, days=30)] == [True]
        assert r["id"] not in _v062_read(SERIAL)[1]
    finally:
        _release(holder)
    usage.append(SERIAL, {"job": "b"})
    assert r["id"] in _v062_read(SERIAL)[1]                # a rollback now shows the entry undone
    assert not any("rollback" in p for p in statexml.problems())
    assert sm.used == [(7, -4.0)]


@needs_flock
def test_a_pending_record_survives_a_restart_and_is_not_lost_when_the_append_keeps_failing(monkeypatch):
    d = _dir()
    monkeypatch.setattr(usage, "LOCK_WAIT", 0.3, raising=False)
    holder = _holder(d)
    try:
        t, out = _in_thread(usage.append, SERIAL, {"job": "p1"})
        t.join(5)
    finally:
        _release(holder)
    p1 = out["r"]["id"]
    with monkeypatch.context() as m:                       # usage.jsonl cannot be appended to (disk full): nothing goes through
        m.setattr(usage, "_old_form", lambda serial, rec: False)
        p2 = usage.append(SERIAL, {"job": "p2"})["id"]
    assert (d / "usage" / usage.DRAINING).is_file() or (d / "usage" / usage.PENDING).is_file()
    assert [x["id"] for x in _v062_read(SERIAL)[0]] == []
    assert any("rollback" in p for p in statexml.problems())
    p3 = usage.append(SERIAL, {"job": "p3"})["id"]
    assert [x["id"] for x in _v062_read(SERIAL)[0]] == [p1, p2, p3]          # in the order they were made
    assert sorted(e["id"] for e in usage.entries(SERIAL, days=30)) == sorted([p1, p2, p3])
    assert not any("rollback" in p for p in statexml.problems())


@needs_flock
def test_a_busy_lock_does_not_rewrite_the_month_file_under_the_holder(monkeypatch):
    usage.append(SERIAL, {"job": "a"})
    month = _dir() / "usage" / "2026-11.xml"
    before = month.read_bytes()
    monkeypatch.setattr(usage, "LOCK_WAIT", 0.3, raising=False)
    holder = _holder(_dir())
    try:
        t, out = _in_thread(usage.append, SERIAL, {"job": "b"})
        t.join(5)
        assert "r" in out and month.read_bytes() == before        # was: rewritten without the lock (a stopped CLI's _move then undoes it)
    finally:
        _release(holder)
