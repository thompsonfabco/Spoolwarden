"""Statexml extensions (R85 section 1, step RC1c follow-up): list files, mode and owner, read-only reads, tag=,
quarantine. The same tests run on the Hub copy and the Slicer Sync copy."""
import errno
import json
import sys

import pytest

from fksync import statexml


def _mode(p):
    return p.stat().st_mode & 0o777


LISTS = [[], [{"id": "a", "at": 1.5, "ok": True}], ["x", "12", "", "a\nb", " pad "],
         [{"Bambu PLA @X": {"k": ["1"]}}, None, 3], [{"fingerprint": "123", "name": "n&<>\""}, {}, []]]


@pytest.mark.parametrize("items", LISTS)
def test_a_list_comes_back_as_it_went_in(items):
    assert statexml.loads_list(statexml.dumps_list("reads", items), "reads") == items


def test_the_list_form():
    text = statexml.dumps_list("devices", [{"id": "a", "n": 2}, {"id": "b", "n": 3}]).decode("utf-8")
    assert text == ('<?xml version="1.0" encoding="UTF-8"?>\n<devices version="1" as="list">\n'
                    '  <item id="a" n="2"/>\n  <item id="b" n="3"/>\n</devices>\n')
    assert statexml.dumps_list("spent", []).decode("utf-8").endswith('<spent version="1" as="list"/>\n')


def test_a_dict_file_is_not_a_list_and_a_list_file_is_not_a_dict():
    with pytest.raises(statexml.StateXmlError):
        statexml.loads_list(statexml.dumps("reads", {"a": 1}), "reads")
    with pytest.raises(statexml.StateXmlError):
        statexml.loads_list(statexml.dumps_list("reads", [1]), "other")
    with pytest.raises(statexml.StateXmlError):
        statexml.dumps_list("reads", {"a": 1})


def test_a_list_json_is_read_once_moved_and_the_xml_wins(tmp_path):
    p = tmp_path / "reads.json"
    p.write_text(json.dumps([{"id": "a"}, {"id": "b"}]), encoding="utf-8")
    assert statexml.read_list(p) == [{"id": "a"}, {"id": "b"}]
    assert (tmp_path / "reads.xml").is_file() and p.is_file() and not list(tmp_path.glob("*.moving"))
    p.write_text(json.dumps([{"id": "stale"}]), encoding="utf-8")
    assert statexml.read_list(p) == [{"id": "a"}, {"id": "b"}]
    statexml.write_list(p, [{"id": "new"}])
    assert statexml.read_list(p) == [{"id": "new"}]
    assert statexml.read_list(tmp_path / "none.json") is None


def test_a_json_object_is_not_a_list_file(tmp_path):
    p = tmp_path / "reads.json"
    p.write_text(json.dumps({"a": 1}), encoding="utf-8")
    assert statexml.read_list(p) is None and not (tmp_path / "reads.xml").exists()


def test_a_damaged_list_xml_is_kept_and_recorded(tmp_path):
    p = tmp_path / "devices.json"
    (tmp_path / "devices.xml").write_bytes(b"<devices version=")
    assert statexml.read_list(p) is None
    assert any("devices.xml" in x for x in statexml.problems())
    statexml.write_list(p, [{"id": "y"}])
    assert (tmp_path / "devices.xml.unreadable").read_bytes() == b"<devices version="
    assert statexml.read_list(p) == [{"id": "y"}]
    assert not any("devices.xml" in x for x in statexml.problems())


def test_migrate_list_only_writes_when_there_is_no_xml(tmp_path):
    p = tmp_path / "spent.json"
    statexml.migrate_list(p, [{"a": 1}])
    statexml.migrate_list(p, [{"a": 2}])
    assert statexml.read_list(p) == [{"a": 1}]


def test_mode_is_the_files_own(tmp_path):
    if sys.platform == "win32":
        pytest.skip("no POSIX modes")
    statexml.write_list(tmp_path / "a.json", [1], 0o600)
    statexml.write(tmp_path / "b.json", {"x": 1}, mode=0o600)
    (tmp_path / "c.json").write_text("[1]", encoding="utf-8")
    statexml.read_list(tmp_path / "c.json", mode=0o600)
    (tmp_path / "d.json").write_text('{"x": 1}', encoding="utf-8")
    statexml.read(tmp_path / "d.json", mode=0o600)
    statexml.migrate(tmp_path / "e.json", {"x": 1}, 0o600)
    statexml.write(tmp_path / "f.json", {"x": 1})
    assert [_mode(tmp_path / f"{n}.xml") for n in "abcde"] == [0o600] * 5
    assert _mode(tmp_path / "f.xml") == statexml._DEFAULT_MODE


def test_the_owner_argument_is_taken_everywhere(tmp_path):
    statexml.write(tmp_path / "a.json", {"x": 1}, state_owned=False)
    statexml.write_list(tmp_path / "b.json", [1], state_owned=False)
    (tmp_path / "c.json").write_text("[1]", encoding="utf-8")
    assert statexml.read_list(tmp_path / "c.json", state_owned=False) == [1]
    statexml.migrate(tmp_path / "d.json", {"x": 1}, state_owned=False)
    statexml.migrate_list(tmp_path / "e.json", [1], state_owned=False)
    assert all((tmp_path / f"{n}.xml").is_file() for n in "abcde")


@pytest.mark.parametrize("code", [errno.EACCES, errno.EPERM, errno.EROFS])
def test_a_read_only_store_reads_the_old_form_and_records_nothing(tmp_path, monkeypatch, code):
    (tmp_path / "plan.json").write_text('{"a": 1}', encoding="utf-8")
    (tmp_path / "reads.json").write_text("[1]", encoding="utf-8")

    def refuse(*a, **k):
        raise OSError(code, "no write permission")
    monkeypatch.setattr(statexml, "_put", refuse)
    before = statexml.problems()
    assert statexml.read(tmp_path / "plan.json") == {"a": 1}
    assert statexml.read_list(tmp_path / "reads.json") == [1]
    statexml.migrate(tmp_path / "plan.json", {"a": 1})
    assert statexml.problems() == before
    assert not list(tmp_path.glob("*.xml")) and not list(tmp_path.glob("*.moving"))


def test_another_failed_move_is_still_a_problem(tmp_path, monkeypatch):
    (tmp_path / "plan2.json").write_text('{"a": 1}', encoding="utf-8")

    def refuse(*a, **k):
        raise OSError(errno.ENOSPC, "disk full")
    monkeypatch.setattr(statexml, "_put", refuse)
    assert statexml.read(tmp_path / "plan2.json") == {"a": 1}
    assert any("plan2.json" in x and "could not be converted" in x for x in statexml.problems())
    statexml.clear_problem(tmp_path / "plan2.json")


def test_a_month_file_takes_its_tag_not_the_stem(tmp_path):
    p = tmp_path / "2026-11.json"
    statexml.write_list(p, [{"id": 1}], tag="events")
    assert b"<events " in (tmp_path / "2026-11.xml").read_bytes()
    assert statexml.read_list(p, tag="events") == [{"id": 1}]
    assert statexml.read_list(p) is None                                  # the stem is the wrong tag
    statexml.clear_problem(tmp_path / "2026-11.xml")
    q = tmp_path / "2026-12.json"
    statexml.write(q, {"n": 1}, tag="usage")
    assert statexml.read(q, tag="usage") == {"n": 1}
    r = tmp_path / "2027-01.json"
    r.write_text('{"n": 2}', encoding="utf-8")
    assert statexml.read(r, tag="usage") == {"n": 2}
    assert statexml.loads((tmp_path / "2027-01.xml").read_bytes(), "usage") == {"n": 2}
    statexml.migrate(tmp_path / "2027-02.json", {"n": 3}, tag="usage")
    assert statexml.read(tmp_path / "2027-02.json", tag="usage") == {"n": 3}


def test_quarantine_keeps_the_file_and_forgets_the_problem(tmp_path):
    p = tmp_path / "2026-11.json"
    assert statexml.quarantine(p) is False
    (tmp_path / "2026-11.xml").write_bytes(b"<events version=")
    assert statexml.read(p, tag="events") is None
    assert any("2026-11.xml" in x for x in statexml.problems())
    assert statexml.quarantine(p) is True
    assert (tmp_path / "2026-11.xml.unreadable").read_bytes() == b"<events version="
    assert not (tmp_path / "2026-11.xml").exists()
    assert not any("2026-11.xml" in x for x in statexml.problems())
    (tmp_path / "x.xml").write_bytes(b"junk")
    assert statexml.quarantine(tmp_path / "x.xml") is True                  # the XML name works too


def test_write_still_takes_the_old_calls(tmp_path):
    statexml.write(tmp_path / "s.json", {"a": 1})
    statexml.write(tmp_path / "t.json", {"a": 1}, 0o600)
    assert statexml.read(tmp_path / "s.json") == {"a": 1} == statexml.read(tmp_path / "t.json")
