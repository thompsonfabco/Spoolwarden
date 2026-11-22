"""RC1e: the Bambu Lab filament-used log is one XML file per month per printer (`usage/<yyyy-mm>.xml`): the round trip,
the old usage.jsonl read once and left alone, a damaged month file, undo and its events, modes, and the reach of the
reads (analytics reads every month)."""
import json
import os
import stat

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
    assert f.is_file() and not (_dir() / "usage.jsonl").exists()
    assert stat.S_IMODE(f.stat().st_mode) == 0o600 or os.name == "nt"
    assert b"<usage " in f.read_bytes()
    [e] = usage.entries(SERIAL)
    assert e["id"] == r["id"] and e["job"] == "123" and e["grams"] == 3.0 and e["color_hex"] == "123456"
    assert e["observe"] is True and e["outcome"] == "finished" and e["spool_id"] is None and e["undone"] is False


def test_months_are_separate_files_and_all_are_read(monkeypatch):
    for at, eid in (("2026-09-30T23:00:00Z", E1), ("2026-11-01T00:00:01Z", E2)):
        usage._write_line(SERIAL, _entry(eid, at))
    names = sorted(p.name for p in (_dir() / "usage").iterdir())
    assert names == ["2026-09.xml", "2026-11.xml"]
    got = usage.entries(SERIAL, days=10_000, by="month")["months"]
    assert [m["month"] for m in got] == ["2026-11", "2026-09"]
    assert [e["id"] for e in usage.entries(SERIAL, days=30)] == [E2]        # the days limit still applies


def test_ids_that_look_like_numbers_survive():
    usage._write_line(SERIAL, _entry(E2, "2026-11-02T00:00:00Z", job="007"))
    [e] = usage.entries(SERIAL)
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
    assert sorted(p.name for p in (d / "usage").iterdir()) == [".migrated", "2026-10.xml", "2026-11.xml"]
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
    assert usage.entries(SERIAL) == []                                      # no crash
    assert any("2026-11.xml" in p for p in statexml.problems())
    usage._write_line(SERIAL, _entry(E3, "2026-11-03T00:00:00Z"))            # the next write keeps the damaged file
    assert (f.with_name("2026-11.xml.unreadable")).read_bytes().startswith(b"<usage version='1'>")
    assert [e["id"] for e in usage.entries(SERIAL)] == [E3]


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
