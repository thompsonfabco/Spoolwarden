"""Focused regression tests for wave1-review round 2 core fixes (D11)."""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from filament_kit import alerts_api, backup, config, event_sink, events, hub_view, inventory, modules, pairing, station_client, undo
from filament_kit.api import create_app
from filament_kit.bridge import auto_slot
from filament_kit.spoolman import Spoolman
from filament_kit.web import reader as web_reader


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    pairing.make_cert("fkhost", [], tmp_path / "etc")
    mods = {n: True for n in modules.MODES["server"]}
    config.save({"modules": mods, "server_name": "Workshop", "spoolman_url": "http://127.0.0.1:7912",
                 "placement": {"box_count": 2}})
    event_sink.set_sink(event_sink.StoreSink(events.store()))
    yield tmp_path
    event_sink.set_sink(None)


@pytest.fixture
def sm(env, spoolman_url):
    s = Spoolman(spoolman_url)
    s.ensure_fields()
    return s


def _make_spool(sm: Spoolman, *, tag: str = "", comment: str = "", first_used: str | None = None,
                location: str = "Dry box 1") -> int:
    fil = sm.post("filament", {"name": "PLA Test", "material": "PLA", "color_hex": "112233", "weight": 1000})
    body = {"filament_id": fil["id"], "remaining_weight": 1000, "location": location, "comment": comment}
    if first_used is not None:
        body["first_used"] = first_used
    if tag:
        body["extra"] = {"nfc_id": json.dumps(tag)}
    return sm.post("spool", body)["id"]


# ---------- 1. inventory.py spool.opened undo fields (04.8) ----------

def test_link_tag_emits_opened_undo_fields_and_undo_restores_them(sm):
    s_old = _make_spool(sm, tag="14:aa:bb:cc:dd:ee:11", comment="", first_used="2026-01-01T00:00:00Z")
    s_new = _make_spool(sm, comment=f"{inventory.SEALED_COMMENT} | batch 7", location="Sealed")

    res = inventory.link_tag(sm, s_new, "14:aa:bb:cc:dd:ee:11", dry=False, force=True)
    assert res.event is not None and res.event["kind"] == "spool.opened"
    data = res.event["data"]
    assert data["before_first_used"] == ""
    assert data["before_comment"] == f"{inventory.SEALED_COMMENT} | batch 7"
    assert data["took_tag_from"] == str(s_old)

    undo.undo_event(res.event_id, sm)
    restored_new = sm.get(f"spool/{s_new}")
    restored_old = sm.get(f"spool/{s_old}")
    assert not restored_new.get("first_used")
    assert restored_new.get("comment") == f"{inventory.SEALED_COMMENT} | batch 7"
    assert json.loads((restored_old.get("extra") or {}).get("nfc_id") or '""') == "14:aa:bb:cc:dd:ee:11"


def test_open_spool_emits_opened_undo_fields_and_undo_restores_them(sm):
    s_old = _make_spool(sm, tag="14:aa:bb:cc:dd:ee:12", comment="", first_used="2026-01-01T00:00:00Z")
    s_new = _make_spool(sm, comment=inventory.SEALED_COMMENT, location="Sealed")

    opened = inventory.open_spool(sm, s_new, "14:aa:bb:cc:dd:ee:12", config.load(), force=True, dry=False)
    ev = events.read(newest=1)["events"][0]
    assert ev["kind"] == "spool.opened"
    assert ev["data"]["before_first_used"] == ""
    assert ev["data"]["before_comment"] == inventory.SEALED_COMMENT
    assert ev["data"]["took_tag_from"] == str(s_old)

    undo.undo_event(opened["event_id"], sm)
    restored_new = sm.get(f"spool/{s_new}")
    restored_old = sm.get(f"spool/{s_old}")
    assert not restored_new.get("first_used")
    assert restored_new.get("comment") == inventory.SEALED_COMMENT
    assert json.loads((restored_old.get("extra") or {}).get("nfc_id") or '""') == "14:aa:bb:cc:dd:ee:12"


# ---------- 2. GET /api/hub mounted for viewers + D6 no root folder listing ----------

def test_api_hub_mounted_for_viewer_and_never_lists_backup_folder(sm, monkeypatch):
    def forbid_listing(*args, **kwargs):
        raise AssertionError("hub_view must never list the root-only backups folder")

    monkeypatch.setattr(backup, "listing", forbid_listing)
    with event_sink.acting({"type": "hub"}):
        event_sink.emit("backup.finished", {"type": "hub"},
                        {"file": "fk.zip", "bytes": "10", "encrypted": "true", "nightly": "true"})

    viewer_tok = pairing.redeem_code(pairing.issue("Viewer", scope="viewer").code)
    station_tok = pairing.redeem_code(pairing.issue("Station", scope="station").code)
    app = create_app(sm)
    client = TestClient(app)

    r_unauth = client.get("/api/hub")
    assert r_unauth.status_code == 401

    r_station = client.get("/api/hub", headers={"Authorization": f"Bearer {station_tok}"})
    assert r_station.status_code == 403

    r_viewer = client.get("/api/hub", headers={"Authorization": f"Bearer {viewer_tok}", "Host": "hub.local:7915"})
    assert r_viewer.status_code == 200
    body = r_viewer.json()
    assert body["name"] == "Workshop"
    assert "last_at" in body["backup"]
    assert isinstance(body["backup"]["nightly"], bool)
    assert body["hub_page"] == "https://hub.local/setup/"


# ---------- 3. 02.13 Auto cancels earlier queued reader taps on the same printer ----------

def test_reader_auto_cancels_queued_tap_for_same_printer_only(env, monkeypatch):
    pytest.importorskip("mcp")

    class FakeSM:
        def get(self, path):
            assert path.startswith("spool")
            return [{"id": 11, "archived": False, "extra": {"nfc_id": json.dumps("4:11:22:33:44:55:66")},
                     "filament": {"id": 201, "name": "PLA", "vendor": {"name": "Overture"}}}]

    class FakeKit:
        def __init__(self):
            self.posts = []

        def get(self, path):
            return {"printer": {"name": "H2D", "state": "connected", "auto_possible": True}, "status": {"ams": []}}

        def post(self, path, body):
            self.posts.append((path, body))
            return {"auto": True, "pending": True}

    class FakeTools:
        def __init__(self, kit):
            self._kit = kit

        def kit_bridge(self):
            return self._kit

    kit = FakeKit()
    q = web_reader.LoadQueue(lambda: kit, start_thread=False)
    q.add("SER1", "A1", 99)
    q.add("SER2", "A1", 88)

    rdr = web_reader.Reader(FakeTools(kit), FakeSM(), lan_cidrs=["127.0.0.1/32"], queue=q)

    # Dry run must not cancel queued taps
    res_dry = rdr._scan("04:11:22:33:44:55:66", "SER1", "auto", dry=True)
    assert res_dry.status_code == 200
    assert ("SER1", "A1") in q.jobs()

    # Real auto scan cancels SER1's queued tap but leaves SER2's untouched
    res_live = rdr._scan("04:11:22:33:44:55:66", "SER1", "auto", dry=False)
    assert res_live.status_code == 200
    assert json.loads(res_live.body)["message"] == web_reader._say("reader_auto_queued")
    assert ("SER1", "A1") not in q.jobs()
    assert ("SER2", "A1") in q.jobs()
    assert ("/api/printers/SER1/slots/auto/load", {"spool_id": 11, "dry_run": False}) in kit.posts


def test_load_queue_drop_printer_waits_for_inflight_send():
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    class BlockingKit:
        def get(self, path):
            return {"printer": {"state": "connected"},
                    "status": {"ams": [{"slots": [{"label": "A1", "present": True, "reading": False}]}]}}

        def post(self, path, body):
            started.set()
            release.wait(timeout=2)
            finished.set()
            return {"preset": {"name": "PLA"}}

    t = 100.0
    q = web_reader.LoadQueue(BlockingKit, clock=lambda: t, start_thread=False)
    q.add("SER1", "A1", 12)
    q.tick()                                        # seen=100.0
    t = 105.0                                       # past SETTLE

    worker = threading.Thread(target=q.tick)
    worker.start()
    assert started.wait(timeout=2)

    dropped_done = threading.Event()

    def do_drop():
        q.drop_printer("SER1")
        dropped_done.set()

    dropper = threading.Thread(target=do_drop)
    dropper.start()
    # drop_printer must block until the in-flight send finishes
    assert not dropped_done.wait(timeout=0.05)
    assert not finished.is_set()

    release.set()
    worker.join(timeout=2)
    dropper.join(timeout=2)
    assert dropped_done.is_set()
    assert finished.is_set()
    assert ("SER1", "A1") not in q.jobs()


def test_auto_slot_and_station_client_use_catalogue_texts():
    cat_path = Path(__file__).resolve().parents[2] / "strings" / "en.json"
    cat = json.loads(cat_path.read_text(encoding="utf-8"))

    assert auto_slot.AUTO_NOT_POSSIBLE == cat["auto_not_possible"]
    assert auto_slot._say("auto_slot_gone") == cat["auto_slot_gone"]
    assert auto_slot._say("auto_nothing_to_choose") == cat["auto_nothing_to_choose"]
    assert auto_slot._say("auto_not_waiting") == cat["auto_not_waiting"]
    assert station_client.TEXT_STATION_NO_PIN == cat["station_not_pinned"].replace(
        "{command}", cat["command_name"])


# ---------- 4. alerts_api.csv_not_readable uses {product} ----------

def test_alerts_api_csv_not_readable_uses_product_placeholder():
    assert alerts_api.MESSAGES["csv_not_readable"] == "This file is not a CSV file that {product} can read."
    assert alerts_api.say("csv_not_readable") == f"This file is not a CSV file that {alerts_api.PRODUCT} can read."
