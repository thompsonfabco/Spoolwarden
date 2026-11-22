"""RC1c (R85 section 1): Slicer Sync's own files as XML, and the build manifest in either form."""
import base64
import io
import json
import random
import sys
import zipfile
from pathlib import Path

import pytest

from fksync import client, installer, statexml, store, sync, zips

REPO = Path(__file__).resolve().parents[2]


def _random_value(rnd: random.Random, depth: int = 0):
    r = rnd.random()
    if depth > 3 or r < 0.3:
        return rnd.choice(["", "a", "12", "1.50", "true", "x y", "a\nb", None, True, False, 0, -5, 3.5, 123456789012])
    if r < 0.6:
        return [_random_value(rnd, depth + 1) for _ in range(rnd.randint(0, 3))]
    keys = rnd.choice([["a", "b_c"], ["Bambu PLA @X1C", "k"], ["as", "x"], []])
    return {k: _random_value(rnd, depth + 1) for k in rnd.sample(keys, rnd.randint(0, len(keys)))}


def test_every_value_comes_back_as_it_went_in():
    rnd = random.Random(5)
    for _ in range(800):
        obj = {k: _random_value(rnd) for k in rnd.sample(["stamp", "at", "rejected", "installed", "x"], rnd.randint(0, 5))}
        assert statexml.loads(statexml.dumps("t", obj), "t") == obj, obj


@pytest.mark.parametrize("bad", [
    b"<?xml version='1.0'?><!DOCTYPE t [<!ENTITY a 'b'>]><t>&a;</t>", b"<other/>", b"<t", b"nope"])
def test_a_file_that_is_not_one_is_refused(bad):
    with pytest.raises(statexml.StateXmlError):
        statexml.loads(bad, "t")


def test_same_bytes_as_the_hub_writes():
    sys.path.insert(0, str(REPO / "server"))
    try:
        from filament_kit.profiles import statexml as hub_side
    except ImportError:
        pytest.skip("the Hub's package is not on the path (the pc-sync tests run alone)")
    finally:
        sys.path.remove(str(REPO / "server"))
    rnd = random.Random(9)
    for _ in range(300):
        obj = {k: _random_value(rnd) for k in rnd.sample(["stamp", "at", "rejected", "x"], rnd.randint(0, 4))}
        assert hub_side.dumps("t", obj) == statexml.dumps("t", obj)


def test_note_problem_feeds_problems_like_the_hubs():
    statexml.note_problem("/x/config.xml", "not readable XML")
    assert "/x/config.xml (not readable XML)" in statexml.problems()
    statexml.clear_problem("/x/config.xml")
    assert not any("/x/config.xml" in p for p in statexml.problems())


def test_json_is_read_once_moved_and_the_xml_wins(tmp_path):
    p = tmp_path / "state.json"
    p.write_text(json.dumps({"installed": {"stamp": "s", "at": 2.0}, "last": {"outcome": "installed"}}), encoding="utf-8")
    st = sync.load_state(store.AppDirs(tmp_path))
    assert st["installed"] == {"stamp": "s", "at": 2.0}
    assert (tmp_path / "state.xml").is_file()
    p.write_text(json.dumps({"installed": {"stamp": "stale"}}), encoding="utf-8")
    assert sync.load_state(store.AppDirs(tmp_path))["installed"]["stamp"] == "s"
    d = store.AppDirs(tmp_path)
    sync._save_state(d, {"installed": {"stamp": "new"}})
    assert sync.load_state(d) == {"installed": {"stamp": "new"}}


def test_an_xml_file_that_cannot_be_read_is_kept(tmp_path):
    (tmp_path / "state.xml").write_bytes(b"<state version=")
    d = store.AppDirs(tmp_path)
    assert sync.load_state(d) == {}
    sync._save_state(d, {"x": 1})
    assert (tmp_path / "state.xml.unreadable").read_bytes() == b"<state version="
    assert sync.load_state(d) == {"x": 1}


def test_config_is_written_in_both_forms_and_xml_is_read_first(tmp_path):
    d = store.AppDirs(tmp_path / "fks")
    cfg = {"server": "kit.example.com:8443", "variant": "BambuStudio", "user_id": "default", "port": 8443}
    store.save_config(d, cfg)
    assert json.loads(d.config.read_text(encoding="utf-8")) == cfg          # an older Sync still finds its pairing
    assert statexml.xml_path(d.config).is_file() and store.load_config(d) == cfg
    d.config.write_text(json.dumps({"server": "stale"}), encoding="utf-8")
    assert store.load_config(d) == cfg
    statexml.xml_path(d.config).write_bytes(b"<config")
    with pytest.raises(ValueError):
        store.load_config(d)


def test_an_older_config_is_read_and_moved(tmp_path):
    d = store.AppDirs(tmp_path / "fks")
    d.ensure()
    d.config.write_text(json.dumps({"server": "kit:1"}), encoding="utf-8")
    assert store.load_config(d) == {"server": "kit:1"}
    assert statexml.xml_path(d.config).is_file()


def test_the_pairing_kept_aside_comes_back_in_both_forms(tmp_path):
    d = store.AppDirs(tmp_path / "fks")
    store.save_token(d, "tok-1")
    store.save_config(d, {"server": "one:1"})
    store.set_pair_aside(d)
    assert statexml.exists(d.pair_aside) and not statexml.xml_path(d.pair_aside).read_bytes().count(b"tok-1")
    store.save_token(d, "tok-2")
    store.save_config(d, {"server": "two:2"})
    assert store.restore_pair_aside(d) is True
    assert store.load_token(d) == "tok-1" and store.load_config(d) == {"server": "one:1"}
    assert json.loads(d.config.read_text(encoding="utf-8")) == {"server": "one:1"}
    assert not statexml.exists(d.pair_aside)


def test_an_older_syncs_aside_is_restored(tmp_path):
    d = store.AppDirs(tmp_path / "fks")
    d.ensure()
    store.save_token(d, "tok-new")
    store.save_config(d, {"server": "new:1"})
    body = {"token": base64.b64encode(b"tok-old").decode(), "config": base64.b64encode(b'{"server": "old:1"}').decode(),
            "waiting": True}
    d.pair_aside.write_text(json.dumps(body), encoding="utf-8")        # as an older Sync wrote it
    assert store.pair_waiting(d) is True
    assert store.restore_pair_aside(d) is True
    assert d.token.read_bytes() == b"tok-old" and store.load_config(d) == {"server": "old:1"}   # the bytes as kept


def test_unpair_removes_both_forms(tmp_path):
    d = store.AppDirs(tmp_path / "fks")
    store.save_config(d, {"server": "a:1"})
    statexml.unlink(d.config)
    assert not d.config.exists() and not statexml.xml_path(d.config).exists()


# ---------- the build manifest ----------

def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = 0o600 << 16
            zf.writestr(info, data, zipfile.ZIP_DEFLATED)
    return buf.getvalue()


MANIFEST = {"stamp": "20261102-010203", "live_hash": "ab", "presets": [{"name": "A @X", "target": "base"}],
            "inapp": {"A @X": {"k": ["1"]}}, "stale": []}


def test_a_build_zip_with_either_manifest_unpacks(tmp_path):
    got = zips.unpack_build(_zip({"BUILD.xml": statexml.dumps("build", MANIFEST), "base/A.json": b"{}"}), tmp_path / "x")
    assert got == MANIFEST and (tmp_path / "x" / "BUILD.xml").is_file()
    got = zips.unpack_build(_zip({"BUILD.json": json.dumps(MANIFEST).encode(), "base/A.json": b"{}"}), tmp_path / "j")
    assert got == MANIFEST and (tmp_path / "j" / "BUILD.json").is_file()
    # the installer reads whichever is there
    assert installer._build_meta(tmp_path / "x")[0] == {"A @X": {"k": ["1"]}}
    assert installer._build_meta(tmp_path / "j")[0] == {"A @X": {"k": ["1"]}}


@pytest.mark.parametrize("entries", [
    {"base/A.json": b"{}"},
    {"BUILD.xml": b"<build", "base/A.json": b"{}"},
    {"BUILD.xml": b"<other/>"},
    {"BUILD.xml": b"<build/>", "BUILD.json": b"{}"},
    {"BUILD.xml": b"<?xml version='1.0'?><!DOCTYPE build [<!ENTITY a 'b'>]><build>&a;</build>"}])
def test_a_build_zip_without_one_good_manifest_is_refused(tmp_path, entries):
    with pytest.raises(ValueError):
        zips.unpack_build(_zip(entries), tmp_path / "bad")
    assert not (tmp_path / "bad").exists()


def test_the_client_asks_for_the_xml_form(monkeypatch):
    seen = []
    c = client.Client.__new__(client.Client)
    monkeypatch.setattr(client.Client, "_request", lambda self, method, path, *a, **k: seen.append(path) or b"zip")
    assert c.build("20261102-010203") == b"zip"
    assert seen == ["/api/sync/build?stamp=20261102-010203&format=xml"]


# ---------- the install marker and KEPT ----------

def test_the_marker_is_xml_and_moves_with_the_folder_record(tmp_path):
    a, b = store.AppDirs(tmp_path / "a"), store.AppDirs(tmp_path / "b")
    a.ensure()
    b.ensure()
    statexml.write(sync.marker_path(a), {"stamp": "s", "backup": "bk", "at": 1.5, "orca": False, "accept": {"base": {"N": "h"}}})
    assert statexml.xml_path(sync.marker_path(a)).is_file() and not sync.marker_path(a).exists()
    assert sync._read_marker(a)["accept"] == {"base": {"N": "h"}}
    sync._move_marker(a, b)
    assert not statexml.exists(sync.marker_path(a)) and sync._read_marker(b)["stamp"] == "s"
    sync.marker_path(b).write_text(json.dumps({"stamp": "old"}), encoding="utf-8")      # an older Sync's marker is read
    statexml.unlink(statexml.xml_path(sync.marker_path(b)))
    assert sync._read_marker(b)["stamp"] == "old"


def test_kept_is_written_in_both_forms(tmp_path):
    statexml.write_plain(tmp_path / installer.KEPT, {"base": ["B", "A"]}, also_json=True)
    assert json.loads((tmp_path / installer.KEPT).read_text(encoding="utf-8")) == {"base": ["B", "A"]}
    assert installer._read_kept(tmp_path) == {"A", "B"}
    (tmp_path / installer.KEPT).unlink()
    assert installer._read_kept(tmp_path) == {"A", "B"}                       # the XML form alone
    statexml.unlink(tmp_path / installer.KEPT)
    assert installer._read_kept(tmp_path) is None
