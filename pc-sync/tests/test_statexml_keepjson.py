"""The keep-old-form write (R85 1.5, orchestrator ruling 2026-11-22): every write of a state file also leaves the JSON form
as the older release wrote it, so `update --rollback` finds files that are current. The same tests run on the Hub copy and
the Slicer Sync copy (the Hub's file also holds the test that the shared block of the two is identical)."""
import json
import sys
from pathlib import Path

import pytest

from fksync import statexml

DATA = {"zeta": 1, "alpha": "é", "name": "n&<>\"", "nested": {"b": [1, 2], "a": None}}


def _old(obj):
    """What the older release wrote: json.dumps(indent=2, sort_keys=True) + newline (Sync: store.py, sync.py)."""
    return (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _mode(p):
    return p.stat().st_mode & 0o777


def test_write_leaves_both_forms_and_the_json_has_the_old_shape(tmp_path):
    statexml.write(tmp_path / "lines.json", DATA)
    assert (tmp_path / "lines.json").read_bytes() == _old(DATA)
    assert statexml.read(tmp_path / "lines.json") == DATA
    assert (tmp_path / "lines.xml").is_file()


def test_write_list_leaves_both_forms(tmp_path):
    items = [{"id": "a"}, {"id": "é"}, 3]
    statexml.write_list(tmp_path / "reads.json", items)
    assert (tmp_path / "reads.json").read_bytes() == _old(items)
    assert statexml.read_list(tmp_path / "reads.json") == items


def test_the_json_is_written_again_at_every_save(tmp_path):
    statexml.write(tmp_path / "a.json", {"n": 1})
    statexml.write(tmp_path / "a.json", {"n": 2})
    assert json.loads((tmp_path / "a.json").read_text(encoding="utf-8")) == {"n": 2}
    assert statexml.read(tmp_path / "a.json") == {"n": 2}


def test_modes_of_the_json_and_the_xml_are_the_same(tmp_path):
    if sys.platform == "win32":
        pytest.skip("no POSIX modes")
    statexml.write(tmp_path / "a.json", {"x": 1}, 0o600)
    statexml.write_list(tmp_path / "b.json", [1], 0o640)
    statexml.write(tmp_path / "c.json", {"x": 1})
    for n, m in (("a", 0o600), ("b", 0o640), ("c", statexml._DEFAULT_MODE)):
        assert _mode(tmp_path / f"{n}.json") == _mode(tmp_path / f"{n}.xml") == m


def test_the_owner_argument_reaches_the_json_write(tmp_path, monkeypatch):
    seen = []
    real = statexml._put
    monkeypatch.setattr(statexml, "_put", lambda p, d, m, so: (seen.append((Path(p).name, m, so)), real(p, d, m, so))[1])
    statexml.write(tmp_path / "a.json", {"x": 1}, 0o600, state_owned=False)
    assert [(n, m, so) for n, m, so in seen if n in ("a.xml", "a.json")] == [("a.xml", 0o600, False), ("a.json", 0o600, False)]


def test_the_json_is_written_after_the_xml_and_a_stop_leaves_the_xml_as_the_truth(tmp_path, monkeypatch):
    statexml.write(tmp_path / "a.json", {"n": 1})
    order = []
    real = statexml._put

    def stop_at_json(p, d, m, so):
        order.append(Path(p).name)
        if Path(p).name == "a.json":
            raise OSError(5, "stop")
        real(p, d, m, so)

    monkeypatch.setattr(statexml, "_put", stop_at_json)
    statexml.write(tmp_path / "a.json", {"n": 2})              # the XML is saved; the old form is a rollback aid
    assert order[:2] == ["a.xml", "a.json"]
    assert json.loads((tmp_path / "a.json").read_text(encoding="utf-8")) == {"n": 1}
    assert statexml.read(tmp_path / "a.json") == {"n": 2}      # the XML is the truth, the stale JSON does not win
    assert any("a.json" in x for x in statexml.problems())
    statexml.clear_problem(tmp_path / "a.json")


def test_a_stop_at_the_xml_leaves_the_json_untouched(tmp_path, monkeypatch):
    statexml.write(tmp_path / "a.json", {"n": 1})
    real = statexml._put

    def stop(p, d, m, so):
        if Path(p).name == "a.xml":
            raise OSError(5, "stop")
        real(p, d, m, so)

    monkeypatch.setattr(statexml, "_put", stop)
    with pytest.raises(OSError):
        statexml.write(tmp_path / "a.json", {"n": 2})
    assert json.loads((tmp_path / "a.json").read_text(encoding="utf-8")) == {"n": 1}


def test_the_opt_out_writes_no_json_and_no_hash(tmp_path):
    d = tmp_path / "s"
    statexml.write(d / "slots.json", {"n": 1}, keep_old_form=False)
    statexml.write_list(d / "ids.json", [1], keep_old_form=False)
    assert sorted(p.name for p in d.iterdir()) == ["ids.xml", "slots.xml"]
    assert statexml.read(d / "slots.json", keep_old_form=False) == {"n": 1}


def test_old_name_is_the_file_that_is_kept(tmp_path):
    statexml.write(tmp_path / "station.json", {"n": 1}, old_name="station_server.json")
    assert not (tmp_path / "station.json").exists()
    assert (tmp_path / "station_server.json").read_bytes() == _old({"n": 1})
    assert (tmp_path / "station.xml").is_file()
    # a JSON-only old file under the old name is moved
    (tmp_path / "old_name.json").write_text('{"k": 2}', encoding="utf-8")
    assert statexml.read(tmp_path / "old.json", old_name="old_name.json") == {"k": 2}
    assert (tmp_path / "old.xml").is_file() and (tmp_path / "old_name.json").read_text(encoding="utf-8") == '{"k": 2}'


def test_old_format_is_the_keyword_arguments_or_a_function(tmp_path):
    statexml.write(tmp_path / "a.json", {"b": 1, "a": "é"}, old_format={"sort_keys": True})
    assert (tmp_path / "a.json").read_bytes() == b'{"a": "\\u00e9", "b": 1}'      # compact: no newline, as json.dumps(obj)
    statexml.write_list(tmp_path / "usage.json", [{"a": 1}, {"a": 2}], old_name="usage.jsonl",
                        old_format=lambda items: "".join(json.dumps(i) + "\n" for i in items).encode())
    assert (tmp_path / "usage.jsonl").read_bytes() == b'{"a": 1}\n{"a": 2}\n'
    assert statexml.read_list(tmp_path / "usage.json", old_name="usage.jsonl") == [{"a": 1}, {"a": 2}]


def test_migrate_after_a_move_keeps_the_json(tmp_path):
    statexml.migrate(tmp_path / "a.json", {"n": 1})
    statexml.migrate_list(tmp_path / "b.json", [1, 2])
    assert (tmp_path / "a.json").read_bytes() == _old({"n": 1}) and (tmp_path / "b.json").read_bytes() == _old([1, 2])
    statexml.migrate(tmp_path / "c.json", {"n": 1}, keep_old_form=False)
    assert not (tmp_path / "c.json").exists() and (tmp_path / "c.xml").is_file()


def test_a_json_only_file_that_is_moved_stays_byte_for_byte(tmp_path):
    raw = '{"n":1,"s":"x"}'
    (tmp_path / "a.json").write_text(raw, encoding="utf-8")
    assert statexml.read(tmp_path / "a.json") == {"n": 1, "s": "x"}
    assert (tmp_path / "a.json").read_text(encoding="utf-8") == raw and (tmp_path / "a.xml").is_file()


def test_a_json_that_an_older_release_changed_wins_once(tmp_path):
    p = tmp_path / "a.json"
    statexml.write(p, {"n": 1, "keep": "x"})
    p.write_text(json.dumps({"n": 7, "keep": "x"}), encoding="utf-8")            # the older release after a rollback
    assert statexml.read(p) == {"n": 7, "keep": "x"}
    assert statexml.loads((tmp_path / "a.xml").read_bytes(), "a") == {"n": 7, "keep": "x"}      # written to the XML
    assert p.read_text(encoding="utf-8") == json.dumps({"n": 7, "keep": "x"})                  # its bytes were not touched
    # once: the next change of the XML form is not overwritten by that JSON
    statexml.write(p, {"n": 8})
    assert statexml.read(p) == {"n": 8}
    p.write_text(json.dumps({"n": 9}), encoding="utf-8")                          # changed again: wins again, once
    assert statexml.read(p) == {"n": 9} and statexml.read(p) == {"n": 9}


def test_the_changed_json_wins_for_a_list_and_with_the_version_kept(tmp_path):
    statexml.write_list(tmp_path / "r.json", [1])
    (tmp_path / "r.json").write_text("[1, 2]", encoding="utf-8")
    assert statexml.read_list(tmp_path / "r.json") == [1, 2]
    statexml.write(tmp_path / "v.json", {"a": 1})
    (tmp_path / "v.json").write_text('{"a": 2}', encoding="utf-8")
    assert statexml.read(tmp_path / "v.json", keep_version=True) == {"version": 1, "a": 2}


def test_a_json_with_no_record_or_equal_data_or_unreadable_does_not_win(tmp_path):
    p = tmp_path / "a.json"
    statexml.write(p, {"n": 1})
    (tmp_path / "a.json.sha256").unlink()                                          # nothing recorded: the XML wins
    p.write_text('{"n": 5}', encoding="utf-8")
    assert statexml.read(p) == {"n": 1}
    statexml.write(p, {"n": 1})
    p.write_text('{"n":1}', encoding="utf-8")                                      # other bytes, same data
    assert statexml.read(p) == {"n": 1}
    p.write_text("{not json", encoding="utf-8")                                    # unreadable
    assert statexml.read(p) == {"n": 1}
    p.write_text("[1]", encoding="utf-8")                                          # not an object
    assert statexml.read(p) == {"n": 1}


def test_the_opt_out_read_ignores_a_changed_json(tmp_path):
    p = tmp_path / "a.json"
    statexml.write(p, {"n": 1})
    p.write_text('{"n": 5}', encoding="utf-8")
    assert statexml.read(p, keep_old_form=False) == {"n": 1}


def test_a_changed_json_under_the_old_name_wins_once(tmp_path):
    p = tmp_path / "station.json"
    statexml.write(p, {"n": 1}, old_name="station_server.json")
    (tmp_path / "station_server.json").write_text('{"n": 4}', encoding="utf-8")
    assert statexml.read(p, old_name="station_server.json") == {"n": 4}
    assert statexml.read(p, old_name="station_server.json") == {"n": 4}


def test_a_read_that_cannot_write_still_returns_the_newer_json(tmp_path, monkeypatch):
    p = tmp_path / "a.json"
    statexml.write(p, {"n": 1})
    p.write_text('{"n": 5}', encoding="utf-8")

    def refuse(*a, **k):
        raise PermissionError(13, "no")

    monkeypatch.setattr(statexml, "_put", refuse)
    assert statexml.read(p) == {"n": 5}
    assert not any("a.json" in x for x in statexml.problems())


def test_unlink_takes_the_hash_too(tmp_path):
    d = tmp_path / "s"
    statexml.write(d / "a.json", {"n": 1})
    statexml.unlink(d / "a.json")
    assert list(d.iterdir()) == []


def test_a_json_an_older_release_deleted_takes_the_xml_with_it(tmp_path):
    p = tmp_path / "pair-aside.json"
    statexml.write(p, {"n": 1})
    p.unlink()                                      # the older release said "none" by deleting the JSON
    assert statexml.read(p) is None
    assert not (tmp_path / "pair-aside.xml").exists()
    assert (tmp_path / "pair-aside.xml.unreadable").is_file()          # kept for the user
    assert not (tmp_path / "pair-aside.json.sha256").exists()
    assert statexml.read(p) is None
    statexml.write(p, {"n": 2})
    assert statexml.read(p) == {"n": 2}

def test_a_function_format_file_is_compared_by_hash_only_and_the_docs_say_so():
    assert "not read back" in statexml._old_bytes.__doc__