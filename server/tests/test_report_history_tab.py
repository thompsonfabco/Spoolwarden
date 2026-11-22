"""Tests for HD6: redact extras, redacted Hub report with 3-file retention, and history_tab routes."""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from filament_kit import alerts_api, config, redact, report
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

    fake_printers = {
        "p1": SimpleNamespace(id="p1", name="Studio X1C", kind="bambu", model="X1C", serial="00M09A123456789", ip="192.168.1.55"),
    }
    from filament_kit.bridge import registry
    monkeypatch.setattr(registry, "load", lambda: fake_printers)

    from filament_kit import pairing
    monkeypatch.setattr(pairing, "devices", lambda: [{"name": "Kitchen Phone", "type": "android", "version": "1.4.0"}])

    cfg = {
        "server_name": "Workshop Hub",
        "spoolman_url": "http://192.168.1.10:7912",
        "modules": {"server": True, "printers": True},
        "backup": {"keep_days": 14, "backup_password": "supersecretvalue"},
    }
    text = report.build(
        cfg,
        journal_fn=lambda unit, n: [
            "2026-11-12T14:00:00+0000 connected to 192.168.1.55 serial 00M09A123456789",
            "2026-11-12T14:01:00+0000 Studio X1C paired with Kitchen Phone pairing_code=654321",
        ],
    )
    lines = text.splitlines()
    assert lines[0] == "Spoolwarden Hub report"
    assert "What this holds:" in lines[2]
    assert "What this never holds:" in lines[3]
    for secret in ("192.168.1.55", "192.168.1.10", "00M09A123456789", "supersecretvalue", "654321",
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

def test_activity_route_open_and_validates_params(monkeypatch, env, helper):
    seen = {}

    def fake_events_answer(**kw):
        seen.update(kw)
        return {"events": [{"id": 1, "kind": "spool.moved"}], "next": 2, "oldest": 1}

    monkeypatch.setattr(alerts_api, "events_answer", fake_events_answer)
    c = _history_client(helper)

    r = c.get("/setup/api/activity?subject=spool:212&actor=hub_page&kinds=spool.moved&since=0&limit=25")
    assert r.status_code == 200
    assert r.json()["events"][0]["id"] == 1
    assert seen["subject"] == "spool:212"
    assert seen["actor"] == "hub_page"
    assert seen["limit"] == 25

    bad = c.get("/setup/api/activity?since=-1")
    assert bad.status_code == 400
    assert "error" in bad.json()


def test_undo_route_code_gated_and_conflict_shape(monkeypatch, env, helper):
    class FakeConflict(Exception):
        status = 409
        code = "changed_since"

    def fake_undo(event_id, actor=None):
        if event_id == 99:
            raise LookupError("no such event")
        if event_id == 42:
            raise FakeConflict("That spool was changed since.")
        return {"undone": True, "event": {"id": 100, "undo_of": event_id}}

    import sys
    import filament_kit
    fake_mod = SimpleNamespace(undo_event=fake_undo)
    monkeypatch.setitem(sys.modules, "filament_kit.undo", fake_mod)
    monkeypatch.setattr(filament_kit, "undo", fake_mod, raising=False)

    c = _history_client(helper)
    assert post(c, "events/10/undo", {}, code=None).status_code == 403

    ok = post(c, "events/10/undo", {})
    assert ok.status_code == 200
    assert ok.json()["undone"] is True

    conflict = post(c, "events/42/undo", {})
    assert conflict.status_code == 409
    assert conflict.json() == {"error": "That spool was changed since.", "code": "changed_since"}

    missing = post(c, "events/99/undo", {})
    assert missing.status_code == 404


def test_health_route_open_and_validates_days(monkeypatch, env, helper):
    import sys
    import filament_kit
    fake_mod = SimpleNamespace(view=lambda days=7: {"from": "2026-11-01", "to": "2026-11-08", "checks": [], "changes": [], "days": days})
    monkeypatch.setitem(sys.modules, "filament_kit.health", fake_mod)
    monkeypatch.setattr(filament_kit, "health", fake_mod, raising=False)

    c = _history_client(helper)
    r = c.get("/setup/api/health?days=30")
    assert r.status_code == 200
    assert r.json()["days"] == 30

    bad = c.get("/setup/api/health?days=14")
    assert bad.status_code == 400
    assert "error" in bad.json()


def test_export_and_import_routes_code_gated(monkeypatch, env, helper):
    import sys
    import filament_kit
    fake_csv = SimpleNamespace(
        export_csv=lambda set_name, **kw: f"col1,col2\n{set_name},{kw.get('days', '')}\n",
        import_spools=lambda text, dry_run=True, actor=None: {"rows": [], "add": 1, "dry": dry_run},
        import_locations=lambda text, dry_run=True, actor=None: {"rows": [], "add": 1, "change": 0, "dry": dry_run},
    )
    monkeypatch.setitem(sys.modules, "filament_kit.csv_io", fake_csv)
    monkeypatch.setattr(filament_kit, "csv_io", fake_csv, raising=False)

    c = _history_client(helper)
    assert c.get("/setup/api/export/usage.csv").status_code == 403

    r = c.get("/setup/api/export/usage.csv?days=7", headers={"X-Setup-Code": CODE})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "usage,7" in r.text

    assert c.get("/setup/api/export/nope.csv", headers={"X-Setup-Code": CODE}).status_code == 404

    assert c.post("/setup/api/import/spools", content=b"a,b\n1,2\n", headers=H).status_code == 403
    imp = c.post("/setup/api/import/spools?dry_run=0", content=b"a,b\n1,2\n",
                 headers={**H, "X-Setup-Code": CODE})
    assert imp.status_code == 200
    assert imp.json()["dry"] is False

    imp_loc = c.post("/setup/api/import/locations", content=b"name\nShelf A\n",
                     headers={**H, "X-Setup-Code": CODE})
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
