"""The rc1d-wave state files keep their JSON form one release through the statexml helper's keep_old_form: the shape
v0.6.2 wrote, kept current on every write, with a change made by an older release winning once; read.json is not kept."""
import json
import os
import stat
import sys

import pytest

from filament_kit import config, intake_history, library, pairing, share_queue
from filament_kit.profiles import statexml

POSIX = pytest.mark.skipif(sys.platform == "win32", reason="file modes are POSIX only")
RID = "0123456789ab"


@pytest.fixture(autouse=True)
def fk_env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))


def sha(jp):
    return jp.with_name(jp.name + ".sha256")


def v062_entry():
    return {"schema_version": 1, "vendor": "SUNLU", "product": "PLA+ 2.0", "material": "PLA",
            "colors": ["Red"], "bambu_parent": "Bambu PLA Tough+",
            "fields": {"nozzle_temperature_range_low": ["205"], "nozzle_temperature_range_high": ["245"]},
            "published_ranges": {"bed_all_plates": "50-60"}, "conflicts": [], "source_quality": "official_page",
            "sources": ["https://www.sunlu.com/x"], "updated": "2026-11-01"}


# ---------- the catalog state (library/current.json and the others) ----------

def test_a_catalog_state_file_keeps_its_json_in_v062s_shape_and_current():
    d = library._dir()
    state = {"dir": "7-20261101", "version": 7, "at": 1.5}
    library._write_state("current.json", state)
    jp = d / "current.json"
    assert jp.read_bytes() == (json.dumps(state, indent=2) + "\n").encode("utf-8")      # library._write_json of v0.6.2
    assert sha(jp).is_file() and statexml.xml_path(jp).is_file()
    library._write_state("current.json", {**state, "dir": "8-20261102", "version": 8})
    assert json.loads(jp.read_text(encoding="utf-8"))["dir"] == "8-20261102"
    library._write_state("rolled_back.json", {"from": 8, "at": 2.5})
    assert json.loads((d / "rolled_back.json").read_text(encoding="utf-8")) == {"from": 8, "at": 2.5}


@POSIX
def test_a_catalog_state_json_is_world_readable_like_v062s():
    library._write_state("current.json", {"dir": "7-20261101", "version": 7, "at": 1.5})
    assert stat.S_IMODE(os.stat(library._dir() / "current.json").st_mode) == 0o644


def test_a_catalog_pointer_changed_by_an_older_release_wins_once():
    d = library._dir()
    library._write_state("previous.json", {"dir": "7-20261101", "version": 7, "at": 1.5})
    (d / "previous.json").write_text(json.dumps({"dir": "6-20261001", "version": 6, "at": 1.0}, indent=2) + "\n",
                                     encoding="utf-8")                      # v0.6.2 after `update --rollback`
    got = library._read_state("previous.json")
    assert got["dir"] == "6-20261001"
    (d / "previous.json").unlink()
    assert library._read_state("previous.json")["dir"] == "6-20261001"      # the XML was written from it


def test_a_dropped_catalog_state_leaves_neither_form_nor_hash():
    library._write_state("rolled_back.json", {"from": 8, "at": 2.5})
    library._drop_state("rolled_back.json")
    d = library._dir()
    assert not (d / "rolled_back.json").exists() and not sha(d / "rolled_back.json").exists()
    assert library.rolled_back_from() is None


# ---------- the owner's entries ----------

def test_an_owner_entry_keeps_its_json_in_v062s_shape_and_an_older_change_wins_once():
    e, _ = library.add_local(json.dumps(v062_entry()).encode("utf-8"))
    jp = library._local_dir() / "sunlu" / "pla-plus-2-0.json"
    assert jp.read_bytes() == library.dumps(e).encode("utf-8") and sha(jp).is_file()       # v0.6.2's `dumps(entry)`
    assert statexml.xml_path(jp).is_file()
    older = {**e, "colors": ["Blue"]}
    jp.write_text(library.dumps(older), encoding="utf-8")                   # v0.6.2 edited the entry after a rollback
    assert library._read_local(jp)["colors"] == ["Blue"]
    jp.unlink()
    assert library._read_local(jp)["colors"] == ["Blue"]                    # now from the XML
    library.add_local(json.dumps({**e, "colors": ["Green"]}).encode("utf-8"))
    assert json.loads(jp.read_text(encoding="utf-8"))["colors"] == ["Green"]


# ---------- the Recently added records ----------

def test_a_record_keeps_record_json_as_v062_wrote_it_and_an_older_change_wins_once():
    rec = {"id": RID, "at": 1.0, "state": "detected", "device": "phone", "saved": [], "revisions": []}
    intake_history._save(rec)
    jp = intake_history._dir(RID) / "record.json"
    assert jp.read_bytes() == json.dumps({**rec, "version": 0}, indent=1).encode("utf-8")    # indent=1, no newline
    assert sha(jp).is_file() and RID in intake_history._ids_newest_first()
    older = json.loads(jp.read_text(encoding="utf-8"))
    older["device"] = "changed by v0.6.2"
    jp.write_text(json.dumps(older, indent=1), encoding="utf-8")
    assert intake_history._load(RID)["device"] == "changed by v0.6.2"
    jp.unlink()
    assert intake_history._load(RID)["device"] == "changed by v0.6.2"


@POSIX
def test_a_record_json_is_private():
    intake_history._save({"id": RID, "at": 1.0, "state": "detected", "saved": [], "revisions": []})
    assert stat.S_IMODE(os.stat(intake_history._dir(RID) / "record.json").st_mode) == 0o600


def test_a_pending_read_is_not_kept_as_json():
    intake_history.stash_read("r1", [b"\xff\xd8\xff\xe0" + b"x" * 100], {"items": [], "engine": {"reader": "local"}})
    names = sorted(p.name for p in intake_history._pending("r1").iterdir())
    assert "read.xml" in names and "read.json" not in names and "read.json.sha256" not in names


# ---------- the share queue ----------

def test_the_share_queue_keeps_share_json_as_v062_wrote_it_and_an_older_change_wins_once():
    st = {"entries": {RID: "fb-9"}, "failed": None}
    intake_history.make_state_dir(intake_history._dir(RID))
    share_queue._write(RID, st)
    jp = share_queue._path(RID)
    assert jp.read_bytes() == json.dumps(st, indent=1).encode("utf-8") and sha(jp).is_file()
    jp.write_text(json.dumps({"entries": {RID: "fb-10"}, "failed": None}, indent=1), encoding="utf-8")
    assert share_queue._read(RID)["entries"] == {RID: "fb-10"}              # v0.6.2 queued a correction
    jp.unlink()
    assert share_queue._read(RID)["entries"] == {RID: "fb-10"}
    share_queue._write(RID, {"entries": {}, "failed": None})
    assert not jp.exists() and not sha(jp).exists() and not statexml.xml_path(jp).exists()


@POSIX
def test_a_share_json_is_private():
    intake_history.make_state_dir(intake_history._dir(RID))
    share_queue._write(RID, {"entries": {RID: "fb-9"}, "failed": None})
    assert stat.S_IMODE(os.stat(share_queue._path(RID)).st_mode) == 0o600
