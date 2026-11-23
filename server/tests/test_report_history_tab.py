"""Tests for redact extras, redacted Hub report with 3-file retention, and history_tab routes."""
from __future__ import annotations

import os

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from filament_kit import config, events, redact, report
from filament_kit.setup_console import history_tab
from filament_kit.setup_page import SetupPage
from test_setup_page import BASE, CODE, H, HOME, HOSTNAME, env, helper, post  # noqa: F401


def _history_client(helper, who=HOME) -> TestClient:
    page = SetupPage(helper=helper, hosts=lambda: {HOSTNAME})
    routes = [Route("/setup/api" + p, fn, methods=[m]) for p, fn, m in history_tab.routes(page)]
    return TestClient(Starlette(routes=routes), base_url=BASE, client=who)


# ---------- redact.py ----------

@pytest.mark.parametrize("line, secret", [
    ("backup_password=my-secret-pass", "my-secret-pass"),
    ("passphrase: horse-battery-staple", "horse-battery-staple"),
    ("pairing_code=482910", "482910"),
    ("setup_code: 991122", "991122"),
    ("X-Setup-Code: 778899", "778899"),
])
def test_redact_hides_backup_passwords_and_codes(line, secret):
    out = redact.text(line, [])
    assert secret not in out
    assert redact.HIDDEN in out


def test_redact_keeps_ordinary_sentences():
    s = "Hub report written: 3 printers, 482 g left, exit code 0."
    assert redact.text(s, []) == s


# ---------- report.py ----------

def test_report_build_redacts_secrets_and_anonymizes_names(monkeypatch, env):
    monkeypatch.setattr(report.socket, "gethostname", lambda: "my-secret-hub-host")
    monkeypatch.setattr(report.platform, "node", lambda: "my-secret-hub-host")

    from filament_kit.bridge import registry          # a real registry entry (D11: no fake with fields Printer lacks)
    registry.upsert(registry.Printer("00M09A123456789", "Studio X1C", "X1C", "10.20.30.11", "12345678", True))

    from filament_kit import pairing
    monkeypatch.setattr(pairing, "devices", lambda: [{"name": "Kitchen Phone", "type": "android", "version": "1.4.0"}])

    cfg = {
        "server_name": "Workshop Hub",
        "spoolman_url": "http://10.20.30.12:7912",
        "modules": {"server": True, "printers": True},
        "backup": {"keep_days": 14, "backup_password": "supersecretvalue"},
    }
    text = report.build(
        cfg,
        journal_fn=lambda unit, n: [
            "2026-11-12T14:00:00+0000 connected to 10.20.30.11 serial 00M09A123456789",
            "2026-11-12T14:01:00+0000 Studio X1C paired with Kitchen Phone pairing_code=654321",
        ],
    )
    lines = text.splitlines()
    assert lines[0] == "Spoolwarden Hub report"
    assert "What this holds:" in lines[2]
    assert "What this never holds:" in lines[3]
    for secret in ("10.20.30.11", "10.20.30.12", "00M09A123456789", "supersecretvalue", "654321",
                   "Workshop Hub", "my-secret-hub-host", "Studio X1C", "Kitchen Phone"):
        assert secret not in text
    assert "printer-1" in text
    assert "device-1" in text
    assert "hub" in text
    assert "## Versions" in text
    assert "## Printers" in text
    assert "## Settings" in text
    assert "## Service logs" in text


def test_report_collect_keeps_newest_three_and_copies_out(monkeypatch, tmp_path, env):
    monkeypatch.setenv("FK_BACKUPS", str(tmp_path / "backups"))
    out_file = tmp_path / "exports" / "my-report.txt"

    written = []
    for i in range(5):
        p = report.collect(cfg={"modules": {}}, journal_fn=lambda u, n: [], out=out_file if i == 4 else None)
        # Ensure distinct mtimes so pruning order is deterministic
        os.utime(p, (1_700_000_000 + i * 10, 1_700_000_000 + i * 10))
        written.append(p)

    kept = report.listing()
    assert len(kept) == 3
    assert kept[0] == written[-1]
    assert report.latest() == written[-1]
    assert out_file.is_file()
    assert out_file.read_text("utf-8") == written[-1].read_text("utf-8")


# ---------- history_tab.py ----------

def _seed_moves(count: int, subject_id: str = "212"):
    from datetime import datetime, timedelta, timezone
    st = events.store()
    t0 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
    for i in range(count):
        st.write("spool.moved", {"type": "spool", "id": subject_id}, {"type": "hub"},
                 {"spool": subject_id, "before_location": f"A{i}", "after_location": f"A{i + 1}"},
                 now=t0 + timedelta(minutes=i))
    return st


def test_activity_route_open_and_validates_params(env, helper):
    _seed_moves(15)
    c = _history_client(helper)

    r = c.get("/setup/api/activity?subject=spool:212&kinds=spool.moved&since=0&limit=5")
    assert r.status_code == 200
    assert [e["id"] for e in r.json()["events"]] == [1, 2, 3, 4, 5]           # `since` pages from the oldest
    newest = c.get("/setup/api/activity?subject=spool:212&limit=10")           # no `since`: the newest ten
    assert [e["id"] for e in newest.json()["events"]] == list(range(6, 16))
    assert newest.json()["events"][-1]["undo"]["possible"] is True

    bad = c.get("/setup/api/activity?since=-1")
    assert bad.status_code == 400
    assert "error" in bad.json()


def test_undo_route_code_gated_and_conflict_shape(env, helper, monkeypatch, spoolman_url):
    from filament_kit.spoolman import Spoolman
    sm = Spoolman(spoolman_url)
    sm.ensure_fields()
    st = _seed_moves(2)
    monkeypatch.setattr(history_tab, "_spoolman", lambda page: sm)
    # a real spool 1 for the undone event to act on
    from filament_kit import inventory
    sid = inventory.add_spools(sm, [{"vendor": "SUNLU", "name": "PLA+ Grey", "material": "PLA", "net": 1000, "tare": 213,
                                     "density": 1.27, "count": 1, "sealed": False}],
                               {"pla_location": "Rack 1", "box_prefix": "Box ", "box_count": 1, "overflow": "Overflow",
                                "sealed_location": "Sealed stock"}, dry=False).spool_ids[0]
    st.write("spool.weighed", {"type": "spool", "id": str(sid)}, {"type": "hub"},
             {"spool": str(sid), "before_grams": "900", "after_grams": "800"})
    sm.patch(f"spool/{sid}", {"remaining_weight": 800})
    weighed = st.last_id()

    c = _history_client(helper)
    assert post(c, f"events/{weighed}/undo", {}, code=None).status_code == 403

    ok = post(c, f"events/{weighed}/undo", {})
    assert ok.status_code == 200
    assert ok.json()["undone"] is True and ok.json()["event"]["undo_of"] == weighed
    assert ok.json()["event"]["actor"]["type"] == "page"                     # the Hub page's undo is `page` (D8)
    assert sm.get(f"spool/{sid}")["remaining_weight"] == 900

    again = post(c, f"events/{weighed}/undo", {})                              # already undone
    assert again.status_code == 409
    assert again.json() == {"error": "This was already undone.", "code": "already_undone"}

    changed = post(c, "events/1/undo", {})                                     # spool 212 moved again since
    assert changed.status_code == 409 and changed.json()["code"] == "changed_since"

    missing = post(c, "events/999/undo", {})
    assert missing.status_code == 404


def test_health_route_open_and_validates_days(env, helper):
    events.store().write("check.failed", {"type": "hub"}, {"type": "hub"}, {"text": "Spoolman is down"}, key="spoolman")
    c = _history_client(helper)
    r = c.get("/setup/api/health?days=30")
    assert r.status_code == 200
    assert r.json()["days"] == 30 and [row["key"] for row in r.json()["checks"]] == ["spoolman"]

    bad = c.get("/setup/api/health?days=14")
    assert bad.status_code == 400
    assert "error" in bad.json()


def test_export_and_import_routes_code_gated(env, helper, monkeypatch, spoolman_url):
    from filament_kit.spoolman import Spoolman
    sm = Spoolman(spoolman_url)
    sm.ensure_fields()
    monkeypatch.setattr(history_tab, "_spoolman", lambda page: sm)
    c = _history_client(helper)
    assert c.get("/setup/api/export/usage.csv").status_code == 403

    r = c.get("/setup/api/export/usage.csv?days=7", headers={"X-Setup-Code": CODE})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert r.text.startswith("time,printer,job,slot,spool,")

    assert c.get("/setup/api/export/nope.csv", headers={"X-Setup-Code": CODE}).status_code == 404

    spools = b"vendor,name,material,net\nSUNLU,PLA+ Grey,PLA,1000\n"
    assert c.post("/setup/api/import/spools", content=spools, headers=H).status_code == 403
    imp = c.post("/setup/api/import/spools?dry_run=0", content=spools, headers={**H, "X-Setup-Code": CODE})
    assert imp.status_code == 200, imp.text
    assert imp.json()["dry"] is False and imp.json()["add"] == 1 and len(sm.get("spool")) == 1

    bad = c.post("/setup/api/import/spools", content=b"a,b\n1,2\n", headers={**H, "X-Setup-Code": CODE})
    assert bad.status_code == 400

    imp_loc = c.post("/setup/api/import/locations", content=b"name\nShelf A\n", headers={**H, "X-Setup-Code": CODE})
    assert imp_loc.status_code == 200
    assert imp_loc.json()["dry"] is True


def test_report_start_and_download_routes(monkeypatch, tmp_path, env, helper):
    monkeypatch.setenv("FK_BACKUPS", str(tmp_path / "backups"))
    c = _history_client(helper)

    # Start report job (code-gated)
    assert post(c, "report", {}, code=None).status_code == 403
    started = post(c, "report", {})
    assert started.status_code == 200
    assert started.json() == {"ok": True, "name": "report"}
    assert helper.calls[-1] == "job"

    # Download before any report exists -> 404
    assert c.get("/setup/api/report/download", headers={"X-Setup-Code": CODE}).status_code == 404

    # Create a report and download via both GET and POST (code-gated)
    p = report.collect(cfg={"modules": {}}, journal_fn=lambda u, n: [])
    assert c.get("/setup/api/report/download").status_code == 403
    assert post(c, "report/download", {}, code=None).status_code == 403

    dl_get = c.get("/setup/api/report/download", headers={"X-Setup-Code": CODE})
    assert dl_get.status_code == 200
    assert dl_get.headers["content-type"].startswith("text/plain")
    assert "Spoolwarden Hub report" in dl_get.text
    assert p.name in dl_get.headers["content-disposition"]

    dl_post = post(c, "report/download", {})
    assert dl_post.status_code == 200
    assert dl_post.headers["content-type"].startswith("text/plain")
    assert dl_post.text == dl_get.text
