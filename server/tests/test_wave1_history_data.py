"""Regression tests for the rework of the history and data modules (wave 1 review, refuter-04): the newest events,
the backup password across a restore, undo, health, analytics, the CSV and report texts and the actors. Each one fails
on the code before the rework. Spoolman is the real client against the in-memory server (fakes/spoolman_fake.py)."""
from __future__ import annotations

import io
import json
import os
import struct
import tarfile
import threading
import urllib.error
from datetime import datetime, timedelta, timezone

import pytest

from filament_kit import (alerts_api, analytics, backup, backup_crypt, csv_io, event_sink, events, health,
                          inventory, report, storage, undo, updates)
from filament_kit.bridge import usage as busage
from filament_kit.spoolman import Spoolman
from test_backup_crypt import kit  # noqa: F401  (the fixture: a kit's folders in a temporary directory)

RULES = {"pla_location": "Rack 1", "box_prefix": "Storage box ", "box_count": 2, "overflow": "Overflow",
         "sealed_location": "Sealed stock"}
NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
SPOOL = {"type": "spool", "id": "1"}
HUB = {"type": "hub"}


@pytest.fixture
def state(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    event_sink.set_sink(None)
    yield tmp_path
    event_sink.set_sink(None)


@pytest.fixture
def store(state):
    st = events.store()                      # under the temporary state folder
    event_sink.set_sink(event_sink.StoreSink(st))
    return st


@pytest.fixture
def sm(state, spoolman_url):
    s = Spoolman(spoolman_url)
    s.ensure_fields()
    return s


def _spool(sm, **extra) -> int:
    item = {"vendor": "SUNLU", "name": "PLA+ Grey", "material": "PLA", "color_hex": "8A8C8E", "net": 1000,
            "tare": 213, "density": 1.27, "count": 1, "sealed": False}
    sid = inventory.add_spools(sm, [item], RULES, dry=False).spool_ids[0]
    if extra:
        sm.patch(f"spool/{sid}", extra)
    return sid


# ---------------------------------------------------------------- D1: the newest events

def test_newest_returns_the_latest_events_in_ascending_order_and_since_paging_is_unchanged(store):
    for i in range(15):
        store.write("spool.moved", {"type": "spool", "id": "212"}, HUB,
                    {"spool": "212", "before_location": f"A{i}", "after_location": f"A{i + 1}"},
                    now=NOW + timedelta(minutes=i))
    store.write("spool.moved", {"type": "spool", "id": "7"}, HUB, {"spool": "7"}, now=NOW + timedelta(hours=1))
    got = store.read(newest=10, activity=True, subject="spool:212")
    assert [e["id"] for e in got["events"]] == list(range(6, 16)) and got["next"] == 16
    assert [e["id"] for e in events.read(newest=3)["events"]] == [14, 15, 16]
    assert [e["id"] for e in store.read(limit=10)["events"]] == list(range(1, 11))       # `since` paging as it was
    assert [e["id"] for e in store.read(since=3, limit=2)["events"]] == [4, 5]
    assert [e["id"] for e in store.read(since=12, newest=2)["events"]] == [15, 16]
    answer = alerts_api.events_answer(newest=10, activity=True, subject="spool:212")
    assert [e["id"] for e in answer["events"]] == list(range(6, 16))
    assert [e["id"] for e in alerts_api.newest(2)] == [15, 16]


def test_the_report_lists_the_newest_events_not_the_oldest(store, monkeypatch):
    monkeypatch.setattr(report, "EVENT_LIMIT", 3)
    for i in range(6):
        store.write("spool.moved", {"type": "spool", "id": str(i)}, HUB, {"spool": str(i)}, now=NOW + timedelta(minutes=i))
    lines = report._events_section()
    assert len(lines) == 3 and "[spool:5]" in lines[-1] and "[spool:3]" in lines[0]


def test_the_report_replaces_location_names(state):
    storage.save(storage.Storage(locations=[storage.Location(name="Garage shelf", overflow=True, default_overflow=True, sealed=True)]))
    names = dict(report._name_map({}))
    assert names["Garage shelf"] == "location-1"
    assert "Garage shelf" not in report._scrub("Moved to Garage shelf.", [], report._name_map({}))


# ---------------------------------------------------------------- D2: a restore keeps the Hub's backup password

def test_a_restore_keeps_the_hubs_backup_password(kit):
    plain = backup.create()                                    # made before any password was set
    backup.write_password("owner-password-1")
    assert backup.restore(plain, yes=True, out=lambda _m: None, run=lambda _c: 0) == 0
    assert backup.read_password() == "owner-password-1"        # not gone: nightly backups stay encrypted


def test_restoring_an_encrypted_backup_leaves_the_current_password(kit):
    backup.write_password("old-password-aaa")
    enc = backup.create()
    backup.write_password("new-password-bbb")
    assert backup.restore(enc, yes=True, password="old-password-aaa", out=lambda _m: None, run=lambda _c: 0) == 0
    assert backup.read_password() == "new-password-bbb"


def test_a_restore_decrypts_beside_the_backups_not_in_the_system_temp_folder(kit, monkeypatch):
    backup.write_password("scratch-password-1")
    enc = backup.create()
    seen = []
    real = backup_crypt.decrypt_file
    monkeypatch.setattr(backup_crypt, "decrypt_file", lambda src, dst, pwd: (seen.append(dst), real(src, dst, pwd))[1])
    assert backup.restore(enc, yes=True, out=lambda _m: None, run=lambda _c: 0) == 0
    assert seen and all(backup.backups_dir() in p.parents for p in seen)


def test_an_encrypted_nightly_downloads_under_the_password_that_opens_it(kit):
    folder = backup.backups_dir()
    folder.mkdir(parents=True, exist_ok=True)
    tgz = folder / "plain.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(kit / "etc", arcname="etc/filament-kit")
    enc = folder / "2026-11-08.tar.gz.enc"
    backup_crypt.encrypt_file(tgz, enc, "that-days-password")
    tgz.unlink()
    backup.write_password("todays-password-1")                  # changed since the nightly was made
    with pytest.raises(backup.BackupError):
        backup.open_for_download(enc.name)
    fd, name = backup.open_for_download(enc.name, "that-days-password")
    with os.fdopen(fd, "rb") as fh:
        data = fh.read()
    assert name.endswith(".zip.enc")
    out = io.BytesIO()
    backup_crypt.decrypt_stream(io.BytesIO(data), out, "that-days-password")
    assert out.getvalue()[:2] == b"PK"


def test_a_crafted_header_cannot_ask_for_gigabytes_of_scrypt(monkeypatch):
    enc = io.BytesIO()
    backup_crypt.encrypt_stream(io.BytesIO(b"data"), enc, "some-password-123", chunk_size=4096, n_log2=10)
    raw = bytearray(enc.getvalue())
    magic, salt, n_log2, r, p, chunk, prefix = backup_crypt.HEADER_STRUCT.unpack(bytes(raw[:backup_crypt.HEADER_STRUCT.size]))
    raw[:backup_crypt.HEADER_STRUCT.size] = backup_crypt.HEADER_STRUCT.pack(magic, salt, 20, 32, p, chunk, prefix)
    monkeypatch.setattr(backup_crypt, "_derive_key", lambda *a: pytest.fail("derived a key for a 4 GiB cost"))
    with pytest.raises(backup_crypt.WrongPasswordOrDamaged):
        backup_crypt.decrypt_stream(io.BytesIO(bytes(raw)), io.BytesIO(), "some-password-123")


# ---------------------------------------------------------------- D8: who did it

def test_a_backup_and_an_update_carry_the_actor_of_the_request(kit):
    with event_sink.acting({"type": "page", "name": "Desk"}):
        backup.create()
        updates._emit_updated("0.5.0", "0.6.0", False)
    with event_sink.acting("cli"):
        backup.create()
    backup.create()
    rows = events.read(kinds="backup.finished,hub.updated")["events"]
    assert [e["actor"]["type"] for e in rows] == ["page", "page", "cli", "hub"]


# ---------------------------------------------------------------- D9: the Spoolman export parameter

def test_the_spoolman_export_asks_for_fmt_csv(monkeypatch):
    seen = []

    class Resp:
        def read(self):
            return b"id\n1\n"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(csv_io.urllib.request, "urlopen", lambda req, timeout=15: (seen.append(req.full_url), Resp())[1])
    csv_io.export_csv("spoolman-spools", sm=Spoolman("http://127.0.0.1:7912"))
    assert seen == ["http://127.0.0.1:7912/api/v1/export/spools?fmt=csv"]

    def refuse(req, timeout=15):
        raise urllib.error.HTTPError(req.full_url, 422, "Unprocessable", {}, io.BytesIO(b"bad"))

    monkeypatch.setattr(csv_io.urllib.request, "urlopen", refuse)
    with pytest.raises(Exception) as e:
        csv_io.export_csv("spoolman-spools", sm=Spoolman("http://127.0.0.1:7912"))
    assert getattr(e.value, "status", None) == 422


def test_the_activity_csv_holds_activity_kinds_with_the_sentence(store):
    store.write("spool.moved", SPOOL, HUB, {"spool": "1", "before_location": "A", "after_location": "B"})
    store.write("check.failed", HUB, HUB, {"text": "Spoolman is down"}, key="spoolman")
    store.write("print.started", {"type": "printer", "id": "P1", "name": "X1"}, HUB, {"job": "part"})
    text = csv_io.export_csv("activity", days=1)
    rows = [r.split(",", 4) for r in text.strip().splitlines()[1:]]
    assert [r[2] for r in rows] == ["spool.moved"]
    assert "From A to B." in rows[0][4] and "before_location" not in rows[0][4]


def test_a_spools_import_without_rules_uses_the_storage_list(state, monkeypatch):
    seen = {}

    def add_spools(sm, items, rules, dry, *, parents_checked=False):
        seen["rules"] = rules
        return inventory.AddResult(dry=dry)

    monkeypatch.setattr(inventory, "add_spools", add_spools)
    csv_io.import_spools(Spoolman("http://127.0.0.1:9"), "vendor,name,material,net\nSUNLU,PLA,PLA,1000\n")
    assert isinstance(seen["rules"], storage.Storage)


def test_the_csv_import_texts_come_from_the_catalogue(state):
    res = csv_io.import_locations("name,type\n,wrong\n")
    assert res["rows"][0]["line"] == alerts_api.say("csv_left_out")
    assert [p["text"] for p in res["rows"][0]["problems"]] == [alerts_api.say("csv_need_location_name"),
                                                               alerts_api.say("csv_bad_type")]
    with pytest.raises(csv_io.CsvError, match="not a CSV file"):
        csv_io.import_locations(b"\xff\xfe")


# ---------------------------------------------------------------- undo

def test_an_empty_slot_cleared_cannot_be_undone(store, sm):
    ev = store.write("slot.cleared", {"type": "printer", "id": "01P00A1", "name": "X1"}, "page", {"slot": "A1"})
    assert undo.evaluate(ev, all_evs=store.all_events()) == {"possible": False, "why_not": "not_undoable"}
    with pytest.raises(undo.UndoError) as e:
        undo.undo_event(ev["id"], sm, store=store)
    assert e.value.code == "not_undoable"


def test_the_same_slot_label_on_another_printer_does_not_block_an_undo(store):
    a = store.write("spool.loaded", SPOOL, "page", {"spool": "1", "before_location": "Shelf", "after_location": "X1 - A1",
                                                    "before_slot": "", "after_slot": "A1"})
    store.write("spool.loaded", {"type": "spool", "id": "2"}, "page",
                {"spool": "2", "before_location": "Shelf", "after_location": "P1 - A1", "before_slot": "", "after_slot": "A1"})
    assert undo.evaluate(a, all_evs=store.all_events())["possible"] is True
    store.write("spool.unloaded", {"type": "spool", "id": "3"}, "page",
                {"spool": "3", "before_location": "X1 - A1", "after_location": "Shelf", "before_slot": "A1",
                 "after_slot": ""})
    assert undo.evaluate(a, all_evs=store.all_events())["why_not"] == "changed_since"     # the same slot, the same printer


@pytest.mark.parametrize("kind, data", [
    ("spool.weighed", {"spool": "1", "after_grams": "420"}),
    ("spool.emptied", {"spool": "1", "after_grams": "0"}),
    ("spool.moved", {"spool": "1"}),
])
def test_an_event_without_its_before_values_is_not_offered_and_refuses_cleanly(store, sm, kind, data):
    ev = store.write(kind, SPOOL, "page", data)
    assert undo.evaluate(ev, all_evs=store.all_events()) == {"possible": False, "why_not": "not_undoable"}
    with pytest.raises(undo.UndoError):                       # not a KeyError that a route turns into a 500
        undo.undo_event(ev["id"], sm, store=store)


def test_undoing_a_tag_link_puts_back_what_the_link_changed(store, sm):
    from filament_kit import tags
    uid = tags.normalize_uid("04AABBCCDDEE80")
    sealed = "SEALED - tag when opened"
    sid = _spool(sm, comment=sealed, extra={"nfc_id": json.dumps("")})
    other = _spool(sm, extra={"nfc_id": json.dumps(uid)})
    first = inventory.link_tag(sm, sid, uid, force=True, dry=False)
    assert first.cleared == [other]
    cur = sm.get(f"spool/{sid}")
    ev = store.write("spool.opened", {"type": "spool", "id": str(sid)}, "page", {
        "spool": str(sid), "before_location": cur["location"], "after_location": cur["location"],
        "before_tag": "", "after_tag": uid, "before_first_used": "", "before_comment": sealed,
        "took_tag_from": str(other)})
    undo.undo_event(ev["id"], sm, store=store)
    back = sm.get(f"spool/{sid}")
    assert back["first_used"] is None and back["comment"] == sealed
    assert tags.stored(back) is None
    assert tags.stored(sm.get(f"spool/{other}")) == uid


def test_undoing_a_slot_to_slot_load_keeps_the_shelf_to_go_back_to(store, sm):
    sid = _spool(sm, location="X1 - A2", extra={"prev_location": json.dumps("Shelf A")})
    ev = store.write("spool.loaded", {"type": "spool", "id": str(sid)}, "page", {
        "spool": str(sid), "before_location": "X1 - A1", "after_location": "X1 - A2",
        "before_slot": "A1", "after_slot": "A2"})
    undo.undo_event(ev["id"], sm, store=store)
    back = sm.get(f"spool/{sid}")
    assert back["location"] == "X1 - A1" and json.loads(back["extra"]["prev_location"]) == "Shelf A"


def test_undo_runs_under_the_inventory_lock_and_checks_running_low(store, sm):
    sid = _spool(sm)
    remaining = sm.get(f"spool/{sid}")["remaining_weight"]
    ev = store.write("spool.weighed", {"type": "spool", "id": str(sid)}, "page",
                     {"spool": str(sid), "before_grams": str(remaining + 50), "after_grams": str(remaining)})
    held, checked = [], []
    real_patch = sm.patch

    def patch(path, body):
        got = []
        t = threading.Thread(target=lambda: got.append(inventory._LOCK.acquire(timeout=0.2)))
        t.start()
        t.join()
        held.append(not got[0])                                # another thread could not take the lock
        return real_patch(path, body)

    sm.patch = patch
    event_sink.set_weight_hook(lambda s: checked.append(s))
    try:
        undo.undo_event(ev["id"], sm, store=store)
    finally:
        event_sink.set_weight_hook(None)
        del sm.patch
    assert held == [True] and checked == [sm]


# ---------------------------------------------------------------- health

def test_the_health_history_follows_printers_devices_and_storage_too(store):
    X1 = {"type": "printer", "id": "P1", "name": "Workshop X1"}
    store.write("printer.offline", X1, HUB, now=NOW - timedelta(days=3))
    store.write("printer.online", X1, HUB, now=NOW - timedelta(days=2))
    store.write("device.down", {"type": "device", "id": "d1", "name": "Pixel"}, HUB, now=NOW - timedelta(days=1))
    store.write("storage.low", HUB, HUB, {"free": "1", "total": "10", "percent": "90"}, now=NOW - timedelta(hours=5))
    store.write("check.failed", HUB, HUB, {"text": "Spoolman is down"}, key="spoolman", now=NOW - timedelta(hours=4))
    view = health.health_view(7, store=store, now=NOW)
    rows = {c["key"]: c for c in view["checks"]}
    assert set(rows) == {"printer-offline:P1", "device-down:d1", "storage-low", "spoolman"}
    assert rows["printer-offline:P1"]["group"] == "printers" and rows["printer-offline:P1"]["text"] == "Workshop X1"
    assert rows["printer-offline:P1"]["now"] == "ok" and len(rows["printer-offline:P1"]["spans"]) == 1
    assert rows["device-down:d1"]["now"] == "look" and rows["device-down:d1"]["group"] == "devices"
    assert rows["storage-low"]["text"] == alerts_api.say("health_row_storage") and rows["storage-low"]["group"] == "storage"
    assert rows["spoolman"]["group"] == "checks"
    assert [c["to"] for c in view["changes"] if c["key"] == "printer-offline:P1"] == ["ok", "look"]


# ---------------------------------------------------------------- analytics

def test_old_entries_without_an_outcome_are_not_counted_as_unfinished(state, monkeypatch):
    from filament_kit.bridge import registry
    serial = "01P00A000000010"
    registry.upsert(registry.Printer(serial=serial, ip="10.0.0.10", access_code="12345678", name="Workshop X1"))
    now = NOW.timestamp()
    monkeypatch.setattr(busage, "_now", lambda: now)
    busage.append(serial, {"job": "old", "grams": 10.0, "spool_id": 1, "slot": "A1", "observe": False})      # no outcome
    busage.append(serial, {"job": "bad", "grams": 20.0, "spool_id": 1, "slot": "A1", "observe": False, "outcome": "failed"})
    month = analytics.view(months=1, sm=None, now=now)["months"][-1]
    assert month["prints"] == 2 and month["unfinished_prints"] == 1 and month["unfinished_grams"] == 20.0
    assert month["unfinished_prints"] == busage.by_month(busage.entries(serial, 30))[0]["unfinished_prints"]


def test_spools_finished_does_not_count_an_undone_empty_or_the_undo(store, sm):
    sid = _spool(sm)
    keep = store.write("spool.emptied", {"type": "spool", "id": str(sid)}, "page",
                       {"spool": str(sid), "before_grams": "500", "after_grams": "0"}, now=NOW)
    store.write("spool.emptied", {"type": "spool", "id": "99"}, "page",
                {"spool": "99", "before_grams": "500", "after_grams": "0"}, now=NOW)
    sm.patch(f"spool/{sid}", {"archived": True, "remaining_weight": 0})
    undo.undo_event(keep["id"], sm, store=store, now=NOW + timedelta(hours=1))     # writes its own spool.emptied
    assert analytics._spools_finished_in_month("2026-11") == 1                     # only spool 99


def test_the_insight_texts_come_from_the_catalogue(state):
    assert analytics.REMOVED_PRINTER_NAME == alerts_api.say("analytics_removed_printer")
    assert alerts_api.say("analytics_note_no_weight", count=1) == "1 print has no weight."
    assert alerts_api.say("analytics_note_no_weight", count=2) == "2 prints have no weight."
