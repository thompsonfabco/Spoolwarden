"""RC1a: the settings file as XML, kept in both forms for one release. What the web unit's side
(web_access) does with it: reads config.xml first and config.json when there is no readable XML, and keeps the web
unit's read on both files. The fixture pair in data/settings_xml is the tag and field map of every settings key, for
config.py (the wave's file) to apply."""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import pytest

from filament_kit import acl, config, web_access
from filament_kit.profiles import statexml

DATA = Path(__file__).parent / "data" / "settings_xml"
LINUX = pytest.mark.skipif(sys.platform == "win32", reason="POSIX ACLs")


@pytest.fixture
def etc(tmp_path, monkeypatch):
    d = tmp_path / "etc"
    d.mkdir()
    monkeypatch.setenv("FK_ETC", str(d))
    monkeypatch.setenv("FK_STATE", str(tmp_path / "state"))
    return d


def _walk(obj, prefix=""):
    """Every key path of a settings object, a dict of values included (not the entries of a list)."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield prefix + k
            yield from _walk(v, prefix + k + ".")


# ---------- the fixture pair: the mapping of every settings key ----------

def test_the_fixture_holds_every_default_key():
    full = json.loads((DATA / "config.full.json").read_text(encoding="utf-8"))
    assert set(_walk(config.DEFAULTS)) <= set(_walk(full))


def test_the_fixture_json_becomes_the_fixture_xml_byte_for_byte():
    full = json.loads((DATA / "config.full.json").read_text(encoding="utf-8"))
    assert statexml.dumps("config", full) == (DATA / "config.full.xml").read_bytes()


def test_the_fixture_xml_reads_back_as_the_fixture_json():
    full = json.loads((DATA / "config.full.json").read_text(encoding="utf-8"))
    assert statexml.loads((DATA / "config.full.xml").read_bytes(), "config") == full


# ---------- web_access reads the settings in both forms ----------

def test_the_settings_read_from_the_xml_form(etc):
    (etc / "config.xml").write_bytes((DATA / "config.full.xml").read_bytes())
    assert web_access._settings()["slot_service"] == {"token_file": "/etc/filament-kit/slot_token"}
    assert web_access._settings() == json.loads((DATA / "config.full.json").read_text(encoding="utf-8"))
    assert not (etc / "config.json").exists()                      # reading never writes the other form


def test_the_settings_read_from_the_json_form_when_there_is_no_xml(etc):
    (etc / "config.json").write_bytes((DATA / "config.full.json").read_bytes())
    assert web_access._settings()["modules"]["ai_intake"] is True
    assert not (etc / "config.xml").exists()                       # and never moves it: root's /etc


def test_the_xml_form_wins_when_both_are_there(etc):
    (etc / "config.json").write_text(json.dumps({"port": 1}), encoding="utf-8")
    (etc / "config.xml").write_bytes(statexml.dumps("config", {"port": 2}))
    assert web_access._settings() == {"port": 2}


def test_a_json_changed_by_an_older_release_wins_over_the_xml_and_the_hash_key_is_not_returned(etc):
    """The web unit's reader follows the same rule as config.load_raw (json_sha256), and stays read only."""
    config.save({"slot_service": {"token_file": "/old"}})
    (etc / "config.json").write_text(json.dumps({"slot_service": {"token_file": "/new"}}), encoding="utf-8")
    xml = (etc / "config.xml").read_bytes()
    assert web_access._settings() == {"slot_service": {"token_file": "/new"}}
    assert (etc / "config.xml").read_bytes() == xml                # reading never writes
    config.save({"port": 3})
    assert web_access._settings() == {"port": 3}                   # unchanged JSON: the XML, without its hash key


def test_a_damaged_xml_falls_back_to_the_json_form_and_says_so_once(etc, caplog):
    (etc / "config.json").write_text(json.dumps({"port": 1}), encoding="utf-8")
    (etc / "config.xml").write_text("<config version=", encoding="utf-8")
    with caplog.at_level(logging.WARNING, logger=web_access.log.name):
        assert web_access._settings() == {"port": 1}
    assert [r.getMessage() for r in caplog.records if "config.xml" in r.getMessage()]
    assert (etc / "config.xml").read_text(encoding="utf-8") == "<config version="   # never overwritten


def test_a_damaged_xml_without_the_json_form_is_an_error_the_callers_catch(etc):
    (etc / "config.xml").write_text("<config version=", encoding="utf-8")
    with pytest.raises(OSError):
        web_access._settings()
    (etc / "config.json").write_text("[1]", encoding="utf-8")
    with pytest.raises(ValueError):
        web_access._settings()


def test_the_etc_files_the_web_unit_reads_name_both_forms():
    assert {"config.xml", "config.json"} <= set(web_access.ETC_FILES)


@LINUX
def test_the_apply_takes_back_the_slot_token_read_with_only_the_xml_form(etc, tmp_path):
    if not acl.supported():
        pytest.skip("no xattrs")
    token = etc / "h2d_token"
    token.write_text("t\n", encoding="utf-8")
    try:
        acl.grant(token, {54321: acl.R, 54322: acl.R})
    except OSError:
        pytest.skip("no ACLs on this file system")
    (etc / "config.xml").write_bytes(statexml.dumps("config", {"slot_service": {"token_file": str(token)}}))
    web_access._revoke_old_slot_token(54321)
    assert acl.users_of(token) == {54322: acl.R}


@LINUX
def test_the_web_unit_gets_read_on_both_forms_of_the_settings(etc, tmp_path):
    if not acl.supported():
        pytest.skip("no xattrs")
    (tmp_path / "state").mkdir()
    for name in web_access.ETC_FILES:
        (etc / name).write_text("x", encoding="utf-8")
    try:
        acl.grant(tmp_path, {54321: acl.R})
    except OSError:
        pytest.skip("no ACLs on this file system")
    web_access.grant(54321, os.getuid())
    for name in ("config.xml", "config.json"):
        assert acl.users_of(etc / name) == {54321: acl.R}, name


# ---------- uninstall.sh: both forms go with the settings folder ----------

def test_uninstall_purge_removes_the_whole_settings_folder_and_a_plain_uninstall_keeps_it():
    text = (Path(__file__).resolve().parents[2] / "install" / "uninstall.sh").read_text(encoding="utf-8")
    purge = text[text.index('if [ "$PURGE" -eq 1 ]; then\n    rm -rf'):]
    # config.xml and config.json are both inside /etc/filament-kit: no file name, so a third form needs no edit here
    assert purge.splitlines()[1].split()[:3] == ["rm", "-rf", "/etc/filament-kit"]
    assert "config.json" not in text and "config.xml" not in text
    assert "Kept: /etc/filament-kit " in purge
