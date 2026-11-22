"""RC1c, keep the old form (orchestrator ruling 2026-11-22): after a write through the new code, the JSON file that v0.6.2's
Slicer Sync reads is there, current, and in the shape its writer made (store.py:164, sync.py:165/486/812, installer.py:242).
Each test reads the file with v0.6.2's own reading code (copied here, with its line)."""
import base64
import io
import json
import stat
import sys
import types
import zipfile
from pathlib import Path

import pytest

from fksync import installer, statexml, store, sync, zips

POSIX = sys.platform != "win32"


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def _v062_load_config(dirs):                    # v0.6.2 store.py:149
    try:
        text = dirs.config.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    cfg = json.loads(text)
    assert isinstance(cfg, dict)
    return cfg


def _v062_load_state(dirs):                     # v0.6.2 sync.py:156
    try:
        st = json.loads(dirs.state.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return st if isinstance(st, dict) else {}


def _v062_read_aside(dirs):                     # v0.6.2 store.py:188
    raw = dirs.pair_aside.read_bytes()
    body = json.loads(raw)
    return body if isinstance(body, dict) else None


def _dirs(tmp_path) -> store.AppDirs:
    d = store.AppDirs(tmp_path / "sync")
    d.ensure()
    return d


# ---------- config.json ----------

def test_config_json_is_current_in_the_old_shape(tmp_path):
    d = _dirs(tmp_path)
    cfg = {"server": "https://h:8443", "zeta": 1, "name": "é"}
    store.save_config(d, cfg)
    assert d.config.read_bytes() == (json.dumps(cfg, indent=2, sort_keys=True) + "\n").encode("utf-8")
    assert _v062_load_config(d) == cfg
    store.save_config(d, {**cfg, "zeta": 2})
    assert _v062_load_config(d)["zeta"] == 2 and store.load_config(d)["zeta"] == 2
    if POSIX:
        assert _mode(d.config) == 0o600


def test_a_config_changed_by_an_older_sync_wins_once(tmp_path):
    d = _dirs(tmp_path)
    store.save_config(d, {"server": "https://new:1"})
    d.config.write_text(json.dumps({"server": "https://old-edit:2"}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert store.load_config(d) == {"server": "https://old-edit:2"}
    store.save_config(d, {"server": "https://newer:3"})
    assert store.load_config(d) == {"server": "https://newer:3"}


# ---------- state.json (and each secondary folder's) ----------

def test_state_json_is_current_in_the_old_shape(tmp_path):
    d = _dirs(tmp_path)
    st = {"installed": {"stamp": "s1", "at": 5, "trees": ["base"]}, "last": {"outcome": "installed"}, "z": "é"}
    sync._save_state(d, st)
    assert d.state.read_bytes() == (json.dumps(st, indent=2, sort_keys=True) + "\n").encode("utf-8")
    assert _v062_load_state(d) == st == sync.load_state(d)
    sync._save_state(d, {**st, "installed": {"stamp": "s2"}})
    assert _v062_load_state(d)["installed"]["stamp"] == "s2"
    if POSIX:
        assert _mode(d.state) == 0o600


def test_a_state_changed_by_an_older_sync_wins_once(tmp_path):
    d = _dirs(tmp_path)
    sync._save_state(d, {"installed": {"stamp": "new"}})
    d.state.write_text(json.dumps({"installed": {"stamp": "old-edit"}}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert sync.load_state(d) == {"installed": {"stamp": "old-edit"}}


def test_a_secondary_folders_state_is_kept_too(tmp_path):
    sd = store.AppDirs(tmp_path / "sync" / "folders" / "studio-1")
    sd.ensure()
    sync._save_state(sd, {"variant": "studio", "installed": {"stamp": "s"}})
    assert _v062_load_state(sd) == {"variant": "studio", "installed": {"stamp": "s"}}


# ---------- installing.json (the crash marker) ----------

def test_the_install_marker_is_compact_json_like_v062(tmp_path):
    d = _dirs(tmp_path)
    me = types.SimpleNamespace(d=d, now=1700000000)
    sync._Run._write_marker(me, "20260101-000000", d.backups / "b", True, {"base": ["é"]})
    m = {"stamp": "20260101-000000", "backup": "b", "at": 1700000000, "orca": True, "accept": {"base": ["é"]}}
    p = sync.marker_path(d)
    assert p.read_bytes() == json.dumps(m).encode("utf-8")      # v0.6.2 sync.py:486: no indent, no newline
    assert json.loads(p.read_text(encoding="utf-8")) == m       # v0.6.2 sync.py:670
    if POSIX:
        assert _mode(p) == 0o600


# ---------- pair-aside.json ----------

def test_the_pair_aside_is_compact_json_like_v062_and_follows_the_waiting_mark(tmp_path):
    d = _dirs(tmp_path)
    d.token.write_bytes(b"TOKEN")
    store.save_config(d, {"server": "https://h"})
    store.set_pair_aside(d)
    raw = d.pair_aside.read_bytes()
    assert b"\n" not in raw and raw == json.dumps(json.loads(raw)).encode("utf-8")      # v0.6.2 store.py:185
    body = _v062_read_aside(d)
    assert base64.b64decode(body["token"]) == b"TOKEN"
    assert base64.b64decode(body["config"]) == d.config.read_bytes()                   # the two keys v0.6.2 restores
    store.mark_pair_waiting(d)
    assert _v062_read_aside(d)["waiting"] is True
    assert d.pair_aside.read_bytes() == json.dumps(_v062_read_aside(d)).encode("utf-8")
    if POSIX:
        assert _mode(d.pair_aside) == 0o600


# ---------- KEPT.json ----------

def test_kept_json_is_compact_like_v062(tmp_path):
    statexml.write_plain(tmp_path / "KEPT.json", {"base": ["é", "a"]}, also_json=True)      # installer.py:239, sync.py:1111
    raw = (tmp_path / "KEPT.json").read_bytes()
    assert raw == json.dumps({"base": ["é", "a"]}).encode("utf-8")                         # v0.6.2 installer.py:242
    assert json.loads(raw)["base"] == ["é", "a"]                                           # v0.6.2 installer.py:329


# ---------- the installed build's BUILD.json ----------

BUILD = {"stamp": "20260101-000000", "presets": [{"name": "A é"}], "inapp": {"A é": {"k": "v"}}, "restored": ["x"]}


def _zip(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def test_a_build_that_came_as_xml_also_has_the_json_an_older_sync_reads(tmp_path):
    dest = tmp_path / "out"
    zips.unpack_build(_zip({"BUILD.xml": statexml.dumps("build", BUILD), "base/a.json": b"{}"}), dest)
    assert (dest / "BUILD.xml").is_file()
    got = json.loads((dest / "BUILD.json").read_text(encoding="utf-8"))      # v0.6.2 __main__.py:457, installer.py:145
    assert got == BUILD and got.get("stamp") == "20260101-000000"
    # and the copy kept as dirs.installed still holds it
    d = _dirs(tmp_path)
    installer._replace_installed(dest, d)
    assert json.loads((d.installed / "BUILD.json").read_text(encoding="utf-8")) == BUILD
