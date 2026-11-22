"""RC1a (R85 section 1.4-1.5): config.py reads config.xml first, falls back to config.json, and saves both forms."""
import json
import os
import stat
import sys

import pytest

from filament_kit import acl, config
from filament_kit.profiles import statexml

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and ACLs")


def _sha(f):
    import hashlib
    return hashlib.sha256(f.read_bytes()).hexdigest()


@pytest.fixture
def etc(tmp_path, monkeypatch):
    d = tmp_path / "etc"
    d.mkdir()
    monkeypatch.setenv("FK_ETC", str(d))
    monkeypatch.setattr(statexml, "_unreadable", {})
    return d


def test_save_writes_both_forms_and_they_read_back_equal(etc):
    cfg = {"port": 1234, "tare_hints": {"PLA Basic": 3}, "locations": ["A", "B"], "web": {"lan": []}}
    config.save(cfg)
    assert json.loads((etc / "config.json").read_text(encoding="utf-8")) == cfg
    assert statexml.loads((etc / "config.xml").read_bytes(), "config") == cfg | {"json_sha256": _sha(etc / "config.json")}
    assert config.load_raw() == cfg


def test_the_xml_form_is_read_first(etc):
    (etc / "config.json").write_text(json.dumps({"port": 1}), encoding="utf-8")
    (etc / "config.xml").write_bytes(statexml.dumps("config", {"port": 2}))
    assert config.load()["port"] == 2


def test_an_old_json_only_install_is_read_and_not_moved(etc):
    (etc / "config.json").write_text(json.dumps({"port": 7}), encoding="utf-8")
    assert config.load()["port"] == 7
    assert not (etc / "config.xml").exists()
    config.save(config.load_raw() | {"port": 8})
    assert config.load()["port"] == 8 and (etc / "config.xml").exists()


def test_load_raw_has_no_defaults_and_none_without_files(etc):
    assert config.load_raw() == {}
    config.save({"future_key": 1})
    assert config.load_raw() == {"future_key": 1}


def test_a_damaged_xml_uses_the_json_and_shows_in_problems(etc):
    (etc / "config.json").write_text(json.dumps({"port": 5}), encoding="utf-8")
    (etc / "config.xml").write_bytes(b"<config version=")
    assert config.load()["port"] == 5
    assert any("config.xml" in p for p in statexml.problems())
    assert (etc / "config.xml").read_bytes() == b"<config version="      # a read changes nothing


def test_a_damaged_xml_is_kept_aside_by_the_next_save(etc):
    (etc / "config.json").write_text(json.dumps({"port": 5}), encoding="utf-8")
    (etc / "config.xml").write_bytes(b"<config version=")
    config.load()
    config.save({"port": 6})
    assert (etc / "config.xml.unreadable").read_bytes() == b"<config version="
    assert config.load()["port"] == 6 and not statexml.problems()


def test_an_error_when_no_form_reads(etc):
    (etc / "config.json").write_text("{not json", encoding="utf-8")
    (etc / "config.xml").write_bytes(b"<config")
    with pytest.raises(ValueError):
        config.load()
    (etc / "config.json").unlink()
    with pytest.raises(ValueError):
        config.load()


def test_note_problem_feeds_problems(monkeypatch):
    monkeypatch.setattr(statexml, "_unreadable", {})
    statexml.note_problem("/etc/x/config.xml", "not readable XML")
    assert statexml.problems() == ["/etc/x/config.xml (not readable XML)"]
    statexml.clear_problem("/etc/x/config.xml")
    assert statexml.problems() == []


def test_every_save_grants_the_web_unit_read_on_config_xml_before_the_replace(etc, monkeypatch):
    """The ACL is set on the temp file and travels through the atomic replace: it is there on every save."""
    seen = []
    real_replace = os.replace
    monkeypatch.setattr(config, "grant_web_read", lambda p: seen.append(("grant", p.name.split(".tmp")[0].rsplit(".", 1)[0])))
    monkeypatch.setattr(config.os, "replace", lambda a, b: (seen.append(("replace", os.path.basename(b))), real_replace(a, b))[1])
    config.save({"port": 1})
    config.save({"port": 2})
    assert seen.index(("grant", "config.xml")) < seen.index(("replace", "config.xml"))
    assert seen.count(("grant", "config.xml")) == 2 and seen.count(("grant", "config.json")) == 2


@POSIX
def test_config_xml_is_640_after_every_save(etc):
    for port in (1, 2):
        config.save({"port": port})
        for name in ("config.xml", "config.json"):
            assert stat.S_IMODE((etc / name).stat().st_mode) == 0o640, name


@POSIX
def test_a_real_acl_on_config_xml_survives_a_save(etc, monkeypatch):
    if os.geteuid() != 0 or not acl.supported():
        pytest.skip("needs root and xattrs")
    monkeypatch.setattr(config, "uid_of", lambda user: 54321)
    try:
        config.save({"port": 1})
        assert acl.users_of(etc / "config.xml") == {54321: acl.R}
        config.save({"port": 2})
    except OSError:
        pytest.skip("no ACLs on this file system")
    assert acl.users_of(etc / "config.xml") == {54321: acl.R}
    assert acl.users_of(etc / "config.json") == {54321: acl.R}


# ---------- updates.py: the state file and the "installed" test ----------

@pytest.fixture
def opt(tmp_path, monkeypatch):
    d = tmp_path / "opt"
    (d / "releases").mkdir(parents=True)
    monkeypatch.setenv("FK_OPT", str(d))
    return d / "releases"


def test_the_update_state_is_written_as_xml_and_read_back(opt):
    from filament_kit import updates
    updates._write_state(previous="1.2.3", latest={"version": "9.9.9", "notes": "x", "at": 1.0})
    assert (opt / "state.xml").is_file() and (opt / "state.json").is_file()
    assert json.loads((opt / "state.json").read_text(encoding="utf-8")) == updates._read_state()
    assert updates._read_state() == {"previous": "1.2.3", "latest": {"version": "9.9.9", "notes": "x", "at": 1.0}}


def test_an_old_json_state_is_read_and_left_for_a_rollback(opt):
    from filament_kit import updates
    old = json.dumps({"previous": "1.0.0", "failed": None})
    (opt / "state.json").write_text(old, encoding="utf-8")
    assert updates._read_state()["previous"] == "1.0.0" and not (opt / "state.xml").exists()
    updates._write_state(held="1.1.0")
    assert updates._read_state() == {"previous": "1.0.0", "failed": None, "held": "1.1.0"}
    assert json.loads((opt / "state.json").read_text(encoding="utf-8")) == updates._read_state()


def test_installed_means_either_form_of_the_settings(etc):
    from filament_kit import updates
    assert not updates._installed()
    (etc / "config.xml").write_bytes(statexml.dumps("config", {}))
    assert updates._installed()
    (etc / "config.xml").unlink()
    (etc / "config.json").write_text("{}", encoding="utf-8")
    assert updates._installed()


def test_the_rollback_cleans_modules_in_both_forms(etc):
    from filament_kit import modules, updates
    name = next(iter(modules.NEWER))
    config.save({"modules": {name: False, "server": True}})
    assert updates._modules_fit("0.0.1") is True
    for form in (json.loads((etc / "config.json").read_text(encoding="utf-8")),
                 statexml.loads((etc / "config.xml").read_bytes(), "config")):
        assert name not in form["modules"]


def test_a_hold_set_through_the_state_is_seen_in_state_json(opt):
    """R582 H2: the release `update --rollback` goes to reads only state.json; its timer must see the hold."""
    from filament_kit import updates
    updates._write_state(previous=None, held="9.9.9")
    assert json.loads((opt / "state.json").read_text(encoding="utf-8")).get("held") == "9.9.9"
    updates._write_state(last_ok_at=5.0)
    assert json.loads((opt / "state.json").read_text(encoding="utf-8")).get("held") == "9.9.9"
    updates._write_state(held=None)
    assert json.loads((opt / "state.json").read_text(encoding="utf-8")).get("held") is None


def test_a_damaged_state_xml_is_kept_aside_not_written_over(opt, monkeypatch):
    """R582 M2: the read notes it, so the next write keeps the damaged file as state.xml.unreadable."""
    from filament_kit import updates
    monkeypatch.setattr(statexml, "_unreadable", {})
    (opt / "state.xml").write_bytes(b"<state")
    (opt / "state.json").write_text(json.dumps({"previous": "1.0.0"}), encoding="utf-8")
    assert updates._read_state()["previous"] == "1.0.0"
    assert any("state.xml" in p for p in statexml.problems())
    updates._write_state(held="1")
    assert (opt / "state.xml.unreadable").read_bytes() == b"<state"
    assert updates._read_state()["held"] == "1" and not statexml.problems()


def test_a_json_changed_after_a_rollback_wins_over_the_older_xml(etc):
    """R582 H1: config.xml records the hash of the config.json it was saved with; another JSON is a newer one."""
    config.save({"port": 1, "modules": {"server": True}})
    (etc / "config.json").write_text(json.dumps({"port": 2, "modules": {"server": True, "station": True}}),
                                     encoding="utf-8")                      # what an older release writes
    assert config.load_raw()["port"] == 2 and config.load()["modules"]["station"] is True
    config.save(config.load_raw())                                           # the next save carries it into both forms
    assert statexml.loads((etc / "config.xml").read_bytes(), "config")["port"] == 2
    assert config.load_raw()["port"] == 2


def test_an_xml_without_a_hash_and_a_matching_hash_are_read_as_before(etc):
    (etc / "config.json").write_text(json.dumps({"port": 1}), encoding="utf-8")
    (etc / "config.xml").write_bytes(statexml.dumps("config", {"port": 2}))      # saved before the hash existed
    assert config.load_raw()["port"] == 2
    config.save({"port": 3})
    assert config.load_raw() == {"port": 3}


def test_a_restored_config_xml_is_read_by_the_service():
    """R582 M1: the restore policy and `modules apply` treat config.xml as they do config.json."""
    from filament_kit import backup
    it = backup.Item(root="etc/filament-kit", path="config.xml", kind="file", mode=0o666, owner="x", group="x")
    assert backup._policy(it) == ("root", "filament-kit", 0o640)
    assert 'etc / "config.xml"' in _text("server/filament_kit/apply.py")


# ---------- the scripts ----------

def _text(rel):
    from pathlib import Path
    return (Path(__file__).resolve().parents[2] / rel).read_text(encoding="utf-8")


def test_install_sh_reads_and_owns_both_forms():
    t = _text("install/install.sh")
    assert 'config.xml" ] || [ -f "$ETC/config.json"' in t                  # the first-install test
    assert "for cf in config.xml config.json; do" in t and 'chmod 640 "$ETC/$cf"' in t
    assert "config.load_raw()" in t and "ElementTree" not in t        # the Hub's Python reads the XML (R85 1.6)


def test_the_cli_script_reads_the_xml_state_then_the_json_one():
    t = _text("install/spoolwarden-cli.sh")
    assert t.index("releases/state.xml") < t.index("releases/state.json")
