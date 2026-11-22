"""RC1c, keep the old form: after a write through the new code, the JSON file that v0.6.2's
Hub reads is there, current, and in the shape its writer made (builds.py:180 / lines.py:602: json.dumps(indent=2,
ensure_ascii=False) + newline, mode 0644, state owner). Each test reads the file with v0.6.2's own reading code (copied
here, with its line), and one test per file."""
import errno
import json
import stat
import sys
from pathlib import Path

import pytest

from filament_kit.profiles import builds, lines as lines_mod, statexml, updates as updates_mod

import test_builds as TB
import test_generate as TG
from test_builds import env  # noqa: F401  (the fixture)

POSIX = sys.platform != "win32"
FILES = ("current", "deleted", "lines", "build-state", "installed", "updates", "owner-edits", "pc-heartbeat", "live/meta")


def _v062_read_json(path: Path):                # v0.6.2 builds.py:168
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _v062_read_obj(path: Path) -> dict:         # v0.6.2 builds.py:176
    data = _v062_read_json(path)
    return data if isinstance(data, dict) else {}


def _old_bytes(obj) -> bytes:                   # v0.6.2 builds.py:182, lines.py:608
    return (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


@pytest.fixture
def made(env):                                  # noqa: F811
    """A Hub folder after a build, an install, an acceptance, an owner-edits record and a heartbeat: every file written
    through the new code (non-ASCII values included: v0.6.2 wrote them unescaped)."""
    TB.synced(env)
    r = builds.run_build(env, now=TG.NOW)
    assert r["ok"] is True, r
    stamp = r["stamp"]
    builds.record_installed(stamp, "a")
    folder = TB.profiles_dir()
    updates_mod.save_accepted(folder, {"base": {"Preset é": "ab" * 32}, "orca": {}}, builds._write_json)
    builds._write_json(folder / builds.OWNER_EDITS, {"stamp": stamp, "base": {"Preset é": {"k": "ü"}}})
    builds.record_heartbeat("a", "studio_running", 12.5, "Bambu Studio")
    return folder, stamp


@pytest.mark.parametrize("name", FILES)
def test_the_json_form_is_kept_in_the_shape_v062_wrote(made, name):
    folder, _ = made
    jp = folder / f"{name}.json"
    assert jp.is_file(), f"{name}.json: the old form is missing"
    xml = statexml.read(jp, keep_version=(name == "lines"))
    assert jp.read_bytes() == _old_bytes(json.loads(jp.read_bytes()))      # indent, ensure_ascii, newline (key order is the writer's)
    assert _v062_read_json(jp) == xml                               # v0.6.2's reader finds the current data
    if POSIX:
        assert _mode(jp) == 0o644 and _mode(statexml.xml_path(jp)) == 0o644


def test_current_json_names_the_last_build_to_v062(made):
    folder, stamp = made
    assert _v062_read_obj(folder / "current.json").get("stamp") == stamp               # builds.py:723, :972


def test_lines_json_follows_a_save_and_v062_loads_it(made):
    folder, _ = made
    ls = lines_mod.load()
    assert len(ls) >= 1
    with lines_mod.transaction() as cur:
        first = sorted(cur)[0]
        del cur[first]
    rows = _v062_read_json(folder / "lines.json")["lines"]                             # v0.6.2 lines.py:561
    assert first not in [r["line_id"] for r in rows]
    assert [r["line_id"] for r in rows] == sorted(lines_mod.load())


def test_installed_and_updates_json_follow_the_next_write(made):
    folder, stamp = made
    assert _v062_read_obj(folder / "installed.json").get("stamp") == stamp             # builds.py:316
    updates_mod.save_accepted(folder, {"base": {"Other": "cd" * 32}, "orca": {}}, builds._write_json)
    got = _v062_read_obj(folder / "updates.json")["accepted"]["base"]                  # updates.py:202
    assert got == {"Other": "cd" * 32}
    assert updates_mod.accepted(folder)["base"] == got


def test_an_older_hubs_change_to_a_state_file_wins_once(made):
    folder, stamp = made
    # an older Hub (after `update --rollback`) records another last build, and the owner's acceptance of a preset
    (folder / "current.json").write_text(json.dumps({"stamp": "20200101-000000"}, indent=2) + "\n", encoding="utf-8")
    (folder / "updates.json").write_text(
        json.dumps({"accepted": {"base": {"By Old": "ef" * 32}, "orca": {}}}, indent=2) + "\n", encoding="utf-8")
    assert builds._state_obj(folder / "current.json") == {"stamp": "20200101-000000"}
    assert updates_mod.accepted(folder)["base"] == {"By Old": "ef" * 32}
    # taken over: the XML says so now, and the next new write goes back through both forms
    assert statexml.read(folder / "current.json") == {"stamp": "20200101-000000"}
    builds._write_json(folder / "current.json", {"stamp": stamp})
    assert _v062_read_obj(folder / "current.json") == {"stamp": stamp} == builds._state_obj(folder / "current.json")


def test_lines_changed_by_an_older_hub_win_once(made):
    folder, _ = made
    assert len(lines_mod.load()) >= 1
    (folder / "lines.json").write_text(json.dumps({"version": lines_mod.FILE_VERSION, "lines": []}, indent=2) + "\n",
                                       encoding="utf-8")
    assert len(lines_mod.load()) == 0
    assert statexml.read(folder / "lines.json", keep_version=True)["lines"] == []


def test_lines_load_uses_the_newer_json_when_lines_xml_cannot_be_rewritten(made, monkeypatch):
    folder, _ = made
    assert len(lines_mod.load()) >= 1
    (folder / "lines.json").write_text(json.dumps({"version": lines_mod.FILE_VERSION, "lines": []}, indent=2) + "\n",
                                       encoding="utf-8")

    def refuse(path, data, mode, state_owned):              # a user with no write permission on state/profiles
        raise PermissionError(errno.EACCES, "denied")
    monkeypatch.setattr(statexml, "_put", refuse)
    assert len(lines_mod.load()) == 0                       # not the stale lines.xml


def test_lines_load_reads_a_newer_json_under_the_lock(made, monkeypatch):
    folder, _ = made
    (folder / "lines.json").write_text(json.dumps({"version": lines_mod.FILE_VERSION, "lines": []}, indent=2) + "\n",
                                       encoding="utf-8")
    seen = []
    real = statexml.read

    def spy(*a, **kw):
        seen.append(getattr(lines_mod._held, "depth", 0))
        return real(*a, **kw)
    monkeypatch.setattr(statexml, "read", spy)
    lines_mod.load()
    assert seen and all(depth > 0 for depth in seen)        # the read can rewrite lines.xml: locked() is held


def test_the_live_meta_json_is_current_for_v062(made):
    folder, _ = made
    assert _v062_read_obj(folder / "live" / "meta.json") == builds._meta()             # builds.py:216
    assert _v062_read_obj(folder / "live" / "meta.json").get("owner") == "a"


def test_the_heartbeat_and_build_state_are_current_for_v062(made):
    folder, _ = made
    hb = _v062_read_obj(folder / "pc-heartbeat.json")                                  # builds.py:1252
    assert hb["outcome"] == "studio_running" and hb["app"] == "Bambu Studio" and hb["waiting_since"] == 12.5
    assert _v062_read_obj(folder / "build-state.json") == builds._state()              # builds.py:304
    assert _v062_read_obj(folder / "owner-edits.json")["base"] == {"Preset é": {"k": "ü"}}      # builds.py:368


def test_the_build_manifests_json_is_v062s_shape(made):
    folder, stamp = made
    raw = (folder / "builds" / stamp / "BUILD.json").read_bytes()
    want = json.loads(raw)
    assert raw == json.dumps(want, indent=2, ensure_ascii=False).encode("utf-8")       # v0.6.2 builds.py:906: no newline
    assert statexml.read(folder / "builds" / stamp / "BUILD.json") == want
    slicer = (folder / "builds" / stamp / "SLICER.json").read_bytes()
    assert slicer == _old_bytes(json.loads(slicer))                                    # v0.6.2 updates.py:158


def test_lines_load_never_waits_for_a_build_and_returns_the_newest_lines(made):
    import threading
    folder, _ = made
    assert len(lines_mod.load()) >= 1
    lines_mod.save({})                                      # the newest saved lines: none
    held, release, got = threading.Event(), threading.Event(), []

    def build():                                            # a build holds lines.lock for its whole run
        with lines_mod.locked():
            held.set()
            release.wait(30)

    def read():
        got.append(lines_mod.load())
    b = threading.Thread(target=build, daemon=True)
    b.start()
    try:
        assert held.wait(10)
        r = threading.Thread(target=read, daemon=True)
        r.start()
        r.join(5)
        assert not r.is_alive(), "lines.load() waited for the build's lock"
        assert len(got[0]) == 0
    finally:
        release.set()
        b.join(10)


def test_locked_without_waiting_raises_when_held_and_waits_by_default(made):
    import threading
    held, release = threading.Event(), threading.Event()

    def hold():
        with lines_mod.locked():
            held.set()
            release.wait(30)
    t = threading.Thread(target=hold, daemon=True)
    t.start()
    try:
        assert held.wait(10)
        with pytest.raises(BlockingIOError):
            with lines_mod.locked(wait=False):
                pass
    finally:
        release.set()
        t.join(10)
    with lines_mod.locked(wait=False):                      # free again
        pass
