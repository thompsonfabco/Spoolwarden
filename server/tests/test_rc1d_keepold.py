"""The rc1d-wave state files keep their JSON form one release through the statexml helper's keep_old_form: the shape
v0.6.2 wrote, kept current on every write, with a change made by an older release winning once (read.json too: v0.6.2 reads it when a save names the read)."""
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
    assert library._read_state("previous.json")["dir"] == "6-20261001"      # the XML was written from it: no second win
    (d / "previous.json").unlink()                                          # an older release deleted it: none, not the XML
    assert library._read_state("previous.json") is None


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
    assert library._read_local(jp)["colors"] == ["Blue"]                    # the XML was written from it
    jp.unlink()                                                             # an older release deleted it: gone
    assert library._read_local(jp) is None
    library.add_local(json.dumps({**e, "colors": ["Green"]}).encode("utf-8"))
    assert json.loads(jp.read_text(encoding="utf-8"))["colors"] == ["Green"]


def test_an_owner_entry_json_keeps_non_ascii_letters_as_v062_wrote_it():
    e, _ = library.add_local(json.dumps({**v062_entry(), "product": "M\u00fcller PLA"}).encode("utf-8"))
    jp = library._local_dir() / "sunlu" / (library.slug(e["product"]) + ".json")
    want = json.dumps(library.canonical(e), indent=2, ensure_ascii=False) + "\n"       # v0.6.2: library.dumps(entry)
    assert "M\u00fcller" in want and jp.read_bytes() == want.encode("utf-8")


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
    assert intake_history._load(RID)["device"] == "changed by v0.6.2"      # the XML was written from it
    jp.unlink()                                                             # an older release deleted it: no such record
    with pytest.raises(intake_history.HistoryError):
        intake_history._load(RID)


@POSIX
def test_a_record_json_is_private():
    intake_history._save({"id": RID, "at": 1.0, "state": "detected", "saved": [], "revisions": []})
    assert stat.S_IMODE(os.stat(intake_history._dir(RID) / "record.json").st_mode) == 0o600


def test_a_pending_read_keeps_read_json_as_v062_wrote_it_and_both_forms_go_when_it_is_taken():
    intake_history.stash_read("r1", [b"\xff\xd8\xff\xe0" + b"x" * 100],
                              {"items": [{"vendor": "SUNLU", "name": "Zo\u00e9"}], "engine": {"reader": "local"}})
    pend = intake_history._pending("r1")
    jp = pend / "read.json"
    body = json.loads(jp.read_text(encoding="utf-8"))                         # what v0.6.2's record_save reads
    assert body["items"][0]["vendor"] == "SUNLU" and body["engine"] == {"reader": "local"}
    assert jp.read_bytes() == json.dumps(body).encode("utf-8")                # json.dumps(body): no indent, no newline
    assert sha(jp).is_file() and statexml.xml_path(jp).is_file()
    if hasattr(os, "geteuid"):
        assert stat.S_IMODE(os.stat(jp).st_mode) == 0o600
    older = {**body, "hint": "changed by v0.6.2"}
    want = json.dumps(older).encode("utf-8")
    jp.write_bytes(want)                                                      # v0.6.2 changed it: only the JSON form has it
    assert jp.read_bytes() == want and "changed by v0.6.2" not in statexml.xml_path(jp).read_text(encoding="utf-8")
    saved = [{"values": {"vendor": "SUNLU"}, "spool_ids": [1], "filament_id": 2}]
    rid = intake_history.record_save(saved, source="phone", read_id="r1")     # record_save is the first reader of the change
    assert intake_history._load(rid)["read"]["hint"] == "changed by v0.6.2"   # an older change wins once, through record_save
    assert not pend.exists()                                                  # read.xml, read.json and its hash go together


# ---------- the share queue ----------

def test_the_share_queue_keeps_share_json_as_v062_wrote_it_and_an_older_change_wins_once():
    st = {"entries": {RID: "fb-9"}, "failed": None}
    intake_history.make_state_dir(intake_history._dir(RID))
    share_queue._write(RID, st)
    jp = share_queue._path(RID)
    assert jp.read_bytes() == json.dumps(st, indent=1).encode("utf-8") and sha(jp).is_file()
    jp.write_text(json.dumps({"entries": {RID: "fb-10"}, "failed": None}, indent=1), encoding="utf-8")
    assert share_queue._read(RID)["entries"] == {RID: "fb-10"}              # v0.6.2 queued a correction
    assert share_queue._read(RID)["entries"] == {RID: "fb-10"}              # the XML was written from it
    jp.unlink()                                                             # v0.6.2 drained the queue
    assert share_queue._read(RID)["entries"] == {}
    share_queue._write(RID, st)
    share_queue._write(RID, {"entries": {}, "failed": None})
    assert not jp.exists() and not sha(jp).exists() and not statexml.xml_path(jp).exists()


@POSIX
def test_a_share_json_is_private():
    intake_history.make_state_dir(intake_history._dir(RID))
    share_queue._write(RID, {"entries": {RID: "fb-9"}, "failed": None})
    assert stat.S_IMODE(os.stat(share_queue._path(RID)).st_mode) == 0o600


# ---------- a JSON that v0.6.2 changed or deleted wins over the XML, and the next save keeps it ----------

def test_a_catalog_pointer_and_hold_changed_and_deleted_by_v062_win_and_the_next_save_keeps_them():
    d = library._dir()
    for name in ("7-20261101", "8-20261102"):
        (d / "versions" / name).mkdir(parents=True)
        (d / "versions" / name / "index.json").write_text("{}", encoding="utf-8")
    library._write_state("current.json", {"dir": "7-20261101", "version": 7, "at": 1.0})
    library._write_state("rolled_back.json", {"from": 9, "at": 1.5})
    # v0.6.2 after `update --rollback`, then an update: it points current.json at 8 and deletes rolled_back.json
    (d / "current.json").write_text(json.dumps({"dir": "8-20261102", "version": 8, "at": 2.0}, indent=2) + "\n",
                                    encoding="utf-8")
    (d / "rolled_back.json").unlink()
    assert library._read_state("current.json")["version"] == 8
    assert library._pointer("current.json")["dir"] == "8-20261102"
    assert library.rolled_back_from() is None
    assert not statexml.xml_path(d / "rolled_back.json").exists()                  # the hold is gone, not revived
    assert not (d / "rolled_back.xml.unreadable").exists()                         # the deletion is a deletion: no copy is kept (statexml-ext 7f897d3a)
    assert statexml.read(d / "current.json", keep_version=True, keep_old_form=False)["version"] == 8  # the XML form was written from it
    cur = library._read_state("current.json")
    library._write_state("current.json", {**cur, "at": 3.0})                       # the next save of current.json itself
    assert json.loads((d / "current.json").read_text(encoding="utf-8"))["version"] == 8    # the next save keeps it
    assert statexml.read(d / "current.json", keep_version=True, keep_old_form=False)["version"] == 8
    assert library._read_state("current.json")["version"] == 8


def test_a_catalog_pointer_deleted_by_v062_is_none_not_the_old_xml():
    d = library._dir()
    library._write_state("previous.json", {"dir": "7-20261101", "version": 7, "at": 1.0})
    (d / "previous.json").unlink()
    assert library._read_state("previous.json") is None and library._pointer("previous.json") is None
    assert not statexml.xml_path(d / "previous.json").exists()


def test_an_owner_entry_changed_by_v062_survives_the_next_save_and_a_deleted_one_is_gone():
    e, _ = library.add_local(json.dumps(v062_entry()).encode("utf-8"))
    jp = library._local_dir() / "sunlu" / "pla-plus-2-0.json"
    jp.write_text(library.dumps({**e, "colors": ["Blue"]}), encoding="utf-8")       # v0.6.2 edited it
    assert [x["colors"] for x in library._load_local()] == [["Blue"]]
    again = {**library._load_local()[0], "material": "PETG"}                        # the next save, from what was read
    assert again["colors"] == ["Blue"] and v062_entry()["colors"] == ["Red"]        # Blue came only from v0.6.2's change
    library.add_local(json.dumps(again).encode("utf-8"))
    assert json.loads(jp.read_text(encoding="utf-8"))["colors"] == ["Blue"]
    jp.unlink()                                                                       # v0.6.2 deleted it
    assert library._read_local(jp) is None and library._load_local() == []
    assert not statexml.xml_path(jp).exists() and not statexml.xml_path(jp).with_name("pla-plus-2-0.xml.unreadable").exists()   # no copy kept
    assert library.remove_local("sunlu--pla-plus-2-0") is False                    # nothing left to remove


def test_a_record_changed_by_v062_survives_the_next_save_and_a_deleted_one_is_gone():
    intake_history._save({"id": RID, "at": 1.0, "state": "detected", "device": "phone", "saved": [], "revisions": []})
    jp = intake_history._dir(RID) / "record.json"
    older = json.loads(jp.read_text(encoding="utf-8"))
    older["device"] = "v062"                                                          # an apply or undo under v0.6.2
    jp.write_text(json.dumps(older, indent=1), encoding="utf-8")
    rec = intake_history._load(RID)
    assert rec["device"] == "v062"
    rec["state"] = "applied"
    intake_history._save(rec)                                                         # the next save
    saved = json.loads(jp.read_text(encoding="utf-8"))
    assert saved["device"] == "v062" and saved["state"] == "applied"
    assert intake_history._load(RID)["device"] == "v062"
    jp.unlink()                                                                       # v0.6.2 deleted the record
    with pytest.raises(intake_history.HistoryError):
        intake_history._load(RID)
    assert RID not in intake_history._ids_newest_first()
    assert not statexml.xml_path(jp).exists()


def test_a_share_json_deleted_by_v062_empties_the_queue_instead_of_reviving_it():
    intake_history.make_state_dir(intake_history._dir(RID))
    share_queue._write(RID, {"entries": {RID: "fb-9"}, "failed": None})
    jp = share_queue._path(RID)
    jp.unlink()                                                                       # v0.6.2 drained the queue
    assert share_queue._read(RID) == {"entries": {}, "failed": None}
    assert not statexml.xml_path(jp).exists()
