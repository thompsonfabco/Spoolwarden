"""the slicer-preset state and the build marker files as XML, on the Hub.

The codec, the file moves (JSON read once and written as XML, XML wins, an unreadable XML file is never written over),
the build zip in either form, and the state a build leaves behind.
"""
import io
import json
import random
import sys
import zipfile
from pathlib import Path

import pytest

from filament_kit import config
from filament_kit.profiles import builds, lines as lines_mod, statexml

import test_builds as TB
import test_generate as TG
from test_builds import env  # noqa: F401  (the fixture)

REPO = Path(__file__).resolve().parents[2]


def _random_value(rnd: random.Random, depth: int = 0):
    r = rnd.random()
    if depth > 3 or r < 0.3:
        return rnd.choice(["", "a", "12", "1.50", "true", "x y", "a\nb", " pad ", "é&<>\"'", None, True, False, 0, -5,
                           3.5, 1e-7, 123456789012, "00", "tab\there"])
    if r < 0.6:
        return [_random_value(rnd, depth + 1) for _ in range(rnd.randint(0, 3))]
    keys = rnd.choice([["a", "b_c", "d"], ["Bambu PLA @X1C", "k", "key"], ["as", "x"], []])
    return {k: _random_value(rnd, depth + 1) for k in rnd.sample(keys, rnd.randint(0, len(keys)))}


def test_every_value_comes_back_as_it_went_in():
    rnd = random.Random(7)
    for _ in range(1500):
        obj = {k: _random_value(rnd) for k in rnd.sample(["stamp", "orca_at", "written", "inapp", "x"], rnd.randint(0, 5))}
        assert statexml.loads(statexml.dumps("t", obj), "t") == obj, obj


def test_the_form():
    obj = {"stamp": "20261117-120000", "at": 1.5, "ok": True, "orca_stamp": "20261117-110000", "num_text": "123",
           "note": "a\nb", "gone": None, "written": ["A", "B"], "inapp": {"Bambu PLA @X": {"k": ["1"]}}, "empty": {}}
    text = statexml.dumps("installed", obj).decode("utf-8")
    assert text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<installed version="1" stamp="20261117-120000" '
                           'at="1.5" ok="true" orca-stamp="20261117-110000"')
    assert "<num-text>123</num-text>" in text         # text that would read back as a number is element text
    assert "<note>a\nb</note>" in text
    assert '<gone as="null"/>' in text and '<empty as="dict"/>' in text
    assert '<written as="list">' in text and "<item>A</item>" in text
    assert '<inapp as="map">' in text and '<entry key="Bambu PLA @X">' in text
    assert statexml.loads(text.encode("utf-8"), "installed") == obj


def test_the_files_own_version_is_the_roots():
    data = {"version": 2, "lines": [{"line_id": "a"}]}
    xml = statexml.dumps("lines", data)
    assert b'<lines version="2"' in xml
    assert statexml.loads(xml, "lines", keep_version=True) == data
    assert statexml.loads(xml, "lines") == {"lines": [{"line_id": "a"}]}


def test_top_level_keys_that_are_not_plain_names_make_the_root_a_map():
    obj = {"Weird Key": 1, "x": [1], "as": "y"}
    assert statexml.loads(statexml.dumps("t", obj), "t") == obj


@pytest.mark.parametrize("bad", [
    b"<?xml version='1.0'?><!DOCTYPE t [<!ENTITY a 'b'>]><t>&a;</t>",
    b'<?xml version="1.0"?><!DOCTYPE t SYSTEM "file:///etc/passwd"><t/>',
    b"<other/>", b"<t", b"not xml at all", b'<t version="two"/>'])
def test_a_file_that_is_not_one_is_refused(bad):
    with pytest.raises(statexml.StateXmlError):
        statexml.loads(bad, "t")


def test_a_value_that_is_not_its_type_is_refused():
    with pytest.raises(statexml.StateXmlError):
        statexml.loads(b'<t><a as="int">x</a></t>', "t")


def test_json_is_read_once_and_moved(tmp_path):
    p = tmp_path / "installed.json"
    p.write_text(json.dumps({"stamp": "s", "at": 2.0, "written": ["A"]}), encoding="utf-8")
    assert statexml.read(p) == {"stamp": "s", "at": 2.0, "written": ["A"]}
    assert (tmp_path / "installed.xml").is_file() and p.is_file()               # the JSON form stays one release
    assert not list(tmp_path.glob("*.moving"))
    p.write_text(json.dumps({"stamp": "stale"}), encoding="utf-8")              # an older release changed it: wins once
    assert statexml.read(p)["stamp"] == "stale" and statexml.read(p)["stamp"] == "stale"
    statexml.write(p, {"stamp": "new"})                                         # the JSON form follows every save
    assert statexml.read(p) == {"stamp": "new"} and json.loads(p.read_text())["stamp"] == "new"
    assert statexml.exists(p)
    statexml.unlink(p)
    assert not statexml.exists(p) and not p.exists() and not (tmp_path / "installed.xml").exists()
    assert statexml.read(p) is None


def test_a_move_never_writes_over_a_newer_xml(tmp_path):
    p = tmp_path / "current.json"
    statexml.write(p, {"stamp": "newer"})
    statexml.migrate(p, {"stamp": "older"})
    assert statexml.read(p) == {"stamp": "newer"}


def test_an_xml_file_that_cannot_be_read_is_kept_not_overwritten(tmp_path):
    p = tmp_path / "deleted.json"
    p.write_text(json.dumps({"deleted_before": ["x"]}), encoding="utf-8")
    (tmp_path / "deleted.xml").write_bytes(b"<deleted version=")
    assert statexml.read(p) == {"deleted_before": ["x"]}                        # the code works with what it has
    assert (tmp_path / "deleted.xml").read_bytes() == b"<deleted version="       # a read changes nothing
    assert any("deleted.xml" in x for x in statexml.problems())
    statexml.write(p, {"deleted_before": ["y"]})
    assert (tmp_path / "deleted.xml.unreadable").read_bytes() == b"<deleted version="
    assert statexml.read(p) == {"deleted_before": ["y"]}


def test_json_that_is_not_an_object_is_nothing(tmp_path):
    p = tmp_path / "current.json"
    p.write_text("[1, 2]", encoding="utf-8")
    assert statexml.read(p) is None and not (tmp_path / "current.xml").exists()


def test_both_codecs_write_the_same_bytes():
    sys.path.insert(0, str(REPO / "pc-sync"))
    try:
        from fksync import statexml as sync_side
    finally:
        sys.path.remove(str(REPO / "pc-sync"))
    rnd = random.Random(11)
    for _ in range(800):
        obj = {k: _random_value(rnd) for k in rnd.sample(["stamp", "orca_at", "written", "inapp", "x"], rnd.randint(0, 5))}
        data = statexml.dumps("t", obj)
        assert sync_side.dumps("t", obj) == data and sync_side.loads(data, "t") == obj


# ---------- what a build leaves behind ----------

def _built(env) -> str:
    TB.synced(env)
    r = builds.run_build(env, now=TG.NOW)
    assert r["ok"] is True, r
    return r["stamp"]


def test_a_build_and_its_state_are_xml(env):
    stamp = _built(env)
    folder = TB.profiles_dir()
    for name in ("current", "deleted", "lines", "build-state"):
        assert (folder / f"{name}.xml").is_file(), name
        assert (folder / f"{name}.json").is_file(), name                        # the old form is kept for a rollback
    assert (folder / "live" / "meta.xml").is_file() and (folder / "live" / "meta.json").is_file()
    b = folder / "builds" / stamp
    assert (b / "BUILD.xml").is_file() and (b / "BUILD.json").is_file() and (b / "SLICER.xml").is_file()
    assert statexml.read(b / "BUILD.json") == json.loads((b / "BUILD.json").read_text(encoding="utf-8"))
    assert builds.current()["stamp"] == stamp and builds.manifest(stamp)["stamp"] == stamp
    builds.record_installed(stamp, "a")
    assert (folder / "installed.xml").is_file() and builds._installed()["stamp"] == stamp
    assert (folder / "installed.json").is_file()


def test_the_build_zip_in_either_form(env):
    stamp = _built(env)
    b = TB.profiles_dir() / "builds" / stamp
    want = json.loads((b / "BUILD.json").read_text(encoding="utf-8"))
    j = zipfile.ZipFile(io.BytesIO(builds.build_zip(stamp)))
    x = zipfile.ZipFile(io.BytesIO(builds.build_zip(stamp, "xml")))
    assert "BUILD.json" in j.namelist() and "BUILD.xml" not in j.namelist()
    assert "BUILD.xml" in x.namelist() and "BUILD.json" not in x.namelist()
    assert [n for n in j.namelist() if n != "BUILD.json"] == [n for n in x.namelist() if n != "BUILD.xml"]
    assert json.loads(j.read("BUILD.json")) == want
    assert statexml.loads(x.read("BUILD.xml"), "build") == want
    # a build kept by an older Hub (JSON only) and one with only the XML form: each form is made from the other
    (b / "BUILD.xml").unlink()
    assert statexml.loads(zipfile.ZipFile(io.BytesIO(builds.build_zip(stamp, "xml"))).read("BUILD.xml"), "build") == want
    statexml.write_plain(b / "BUILD.json", want)
    (b / "BUILD.json").unlink()
    assert json.loads(zipfile.ZipFile(io.BytesIO(builds.build_zip(stamp))).read("BUILD.json")) == want


def test_a_build_of_an_older_hub_is_found_and_moved(env):
    stamp = _built(env)
    folder = TB.profiles_dir()
    # put the folder back as an older Hub left it: JSON files only
    for name in ("current", "deleted", "lines", "build-state"):
        data = statexml.read(folder / f"{name}.json", keep_version=(name == "lines"))
        (folder / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
        (folder / f"{name}.xml").unlink()
    (folder / "builds" / stamp / "BUILD.xml").unlink()
    (folder / "builds" / stamp / "SLICER.xml").unlink()
    assert builds.current()["stamp"] == stamp
    assert builds.manifest(stamp)["stamp"] == stamp
    assert (folder / "current.xml").is_file()
    assert len(lines_mod.load()) >= 1 and (folder / "lines.xml").is_file()
    assert builds._state().get("last_attempt", {}).get("ok") is True
    # and the next build works from them
    TB.tick()
    builds.request_rebuild("again")
    assert builds.run_build(env, now=TG.NOW + 1)["ok"] is True
