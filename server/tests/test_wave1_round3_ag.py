"""Proving tests for Brief 19 round 3 fixes in api.py, cli.py, setup_page.py, and web/intake_app.py."""
from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from filament_kit import cli, config, modules, pairing, reader_corrections, setup_page
from filament_kit.api import create_app
from filament_kit.bridge import family
from filament_kit.spoolman import Spoolman
from filament_kit.web.intake_app import IntakeApp


ON = frozenset({"server", "printers", "klipper", "local_intake"})


@pytest.fixture(autouse=True)
def fk_env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    pairing.make_cert("fkhost", [], tmp_path / "etc")
    config.save({"modules": {"enabled": sorted(ON)}})


def test_bambu_add_by_address_requires_certificate_in_kit_api(spoolman_url, monkeypatch):
    sm = Spoolman(spoolman_url)
    app = create_app(sm=sm, printers=family.Printers(object(), None), enabled_modules=ON)
    tok = pairing.redeem_code(pairing.issue("phone").code)
    c = TestClient(app, raise_server_exceptions=False)
    c.headers["Authorization"] = f"Bearer {tok}"

    monkeypatch.setattr(setup_page, "setup_serial", lambda ip, timeout=5: (_ for _ in ()).throw(OSError("timed out")))
    r = c.post("/api/printers", json={"family": "bambu", "address": "192.168.1.70", "access_code": "12345678",
                                      "serial": "01P00A222222222"})
    assert r.status_code == 400 and "did not answer" in r.json()["message"] and r.json()["field"] == "address"


def test_events_get_refuses_newest_with_since_at_wait_or_over_1000(spoolman_url):
    sm = Spoolman(spoolman_url)
    app = create_app(sm=sm, printers=family.Printers(None, None), enabled_modules=ON)
    tok = pairing.redeem_code(pairing.issue("phone").code)
    c = TestClient(app, raise_server_exceptions=False)
    h = {"Authorization": f"Bearer {tok}"}

    assert c.get("/api/events?newest=1001", headers=h).status_code == 400
    assert c.get("/api/events?newest=10&since_at=2026-01-01T00:00:00Z", headers=h).status_code == 400
    assert c.get("/api/events?newest=10&wait=5", headers=h).status_code == 400
    assert c.get("/api/events?newest=10&since=0", headers=h).status_code == 400


def test_intake_app_corrections_refusal_uses_code_and_activity_passes_newest(spoolman_url):
    sm = Spoolman(spoolman_url)
    seen_paths = []

    class FakeKit:
        def get(self, path):
            seen_paths.append(path)
            return {"events": []}

    web = IntakeApp(SimpleNamespace(cfg=lambda: config.load(), sm=sm),
                    public_url=None, lan_cidrs=["192.168.1.0/24"], kit_api=FakeKit(), host_ok=lambda _r: True)
    app = Starlette(routes=web.routes())
    c = TestClient(app, base_url="https://testserver", client=("192.168.1.25", 50000), raise_server_exceptions=False)

    r = c.get("/intake/api/activity?subject=spool:5&newest=10")
    assert r.status_code == 200
    assert seen_paths[-1].startswith("/api/events?")
    assert "newest=10" in seen_paths[-1] and "limit=" not in seen_paths[-1]
    assert c.get("/intake/api/activity?newest=10&since=1").status_code == 400

    r_corr = c.post("/intake/api/label-reader/corrections/nonexistent/remove",
                    headers={"Origin": "https://testserver"})
    assert r_corr.status_code >= 400
    body = r_corr.json()
    assert "code" in body and "reason" not in body


def test_setup_page_preview_server_module_includes_code(monkeypatch):
    import asyncio
    monkeypatch.setattr(modules, "enabled", lambda cfg: {"server"})
    page = setup_page.SetupPage()
    res = asyncio.run(page.preview(SimpleNamespace(query_params={"name": "server"}), {}))
    assert res["code"] == "server_module"


def test_cli_actor_scheduled_is_hub_and_cert_made_warns_tag_readers(tmp_path, monkeypatch, capsys):
    assert cli._cli_actor(SimpleNamespace(scheduled=True)) == "hub"
    monkeypatch.setenv("FILAMENT_KIT_ACTOR", "hub")
    assert cli._cli_actor(SimpleNamespace(scheduled=False)) == "hub"

    etc = Path(os.environ["FK_ETC"])
    (etc / "cert.pem").unlink()
    (etc / "key.pem").unlink(missing_ok=True)
    assert cli.main(["certificate"]) == 0
    out = capsys.readouterr().out
    assert "Made a new certificate." in out
    assert "Tag Readers set up for this Hub must be set up again." in out


def test_cli_status_without_root_queries_helper_for_backup_done(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_is_root", lambda: False)
    monkeypatch.setattr(cli, "_no_systemd", lambda: False)
    monkeypatch.setattr(cli.modules, "enabled", lambda cfg: {"server"})
    monkeypatch.setattr(setup_page.HelperClient, "__call__", lambda self, req: {"last": "2026-11-22"} if req["op"] == "last_backup" else {})
    monkeypatch.setattr(cli.status_rows, "rows", lambda **kw: [])
    monkeypatch.setattr(cli.status_rows, "print_rows", lambda rows, **kw: True)
    monkeypatch.setattr(cli.getting_started, "view", lambda **kw: seen.append(kw) or {"show": False})
    assert cli.cmd_status(lambda: None) == 0
    assert seen == [{"backup_done": True}]


def test_cli_backup_password_json_refusal_and_yes_flag(monkeypatch, capsys):
    assert cli.main(["--json", "backup", "password"]) == 2
    assert "backup password" in capsys.readouterr().err

    calls = []
    monkeypatch.setattr(cli, "_is_root", lambda: True)
    monkeypatch.setattr(cli.history_cli, "run_backup_password", lambda **kw: calls.append(kw) or 0)
    assert cli.main(["backup", "password", "--remove", "--yes"]) == 0
    assert calls == [{"remove": True, "show": False, "password": None}]
