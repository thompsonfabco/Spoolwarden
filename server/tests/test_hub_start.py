"""Tests for AF6 Getting Started tab routes and settings Network port / Label Reader endpoints."""
from __future__ import annotations

import os
import re
import subprocess
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from filament_kit import (
    config,
    firstrun,
    getting_started,
    hubport,
    local_intake_install,
    local_reader,
    modules,
    setup_helper,
)
from filament_kit.setup_console import start_tab
from filament_kit.setup_page import SetupPage
from test_setup_page import AWAY, BASE, CODE, H, HOME, HOSTNAME, STATIC, Helper, client, env, helper, make, post  # noqa: F401


def _start_client(helper, who=HOME) -> TestClient:
    page = SetupPage(helper=helper, hosts=lambda: {HOSTNAME})
    routes = [Route("/setup/api" + p, fn, methods=[m]) for p, fn, m in start_tab.routes(page)]
    return TestClient(Starlette(routes=routes), base_url=BASE, client=who)


def test_start_routes_return_getting_started_view_and_toggle_hidden(env, helper):
    assert firstrun.set_hidden(False)
    c = _start_client(helper)
    r = c.get("/setup/api/start")
    assert r.status_code == 200
    body = r.json()
    assert body["show"] is True and body["hidden"] is False and body["restore_offer"] is True
    assert body["done"] == 0 and body["total"] == len(body["steps"]) > 0

    assert post(c, "start", {"hide": True}, code=None).status_code == 403
    r_hide = post(c, "start", {"hide": True})
    assert r_hide.status_code == 200
    assert r_hide.json()["show"] is False and r_hide.json()["hidden"] is True and r_hide.json()["finished"] is False

    r_show = post(c, "start", {"hide": False})
    assert r_show.status_code == 200
    assert r_show.json()["show"] is True and r_show.json()["hidden"] is False


def test_start_post_keeps_marks_and_rejects_bad_inputs(env, helper):
    assert firstrun.set_hidden(False)
    c = _start_client(helper)
    before = c.get("/setup/api/start").json()
    assert before["steps"][0]["id"] == "name" and before["steps"][0]["state"] == "next"

    r = post(c, "start", {"keep": "name"})
    assert r.status_code == 200
    after = r.json()
    assert after["done"] == before["done"] + 1
    assert after["steps"][0]["state"] == "done"
    assert after["steps"][1]["state"] == "next"

    for bad in ({}, {"hide": "yes"}, {"keep": "unknown"}, {"hide": True, "keep": "name"}):
        res = post(c, "start", bad)
        assert res.status_code == 400 and "error" in res.json()

    assert c.post("/setup/api/start", json={"hide": True}, headers={}).status_code == 403
    assert _start_client(helper, who=AWAY).get("/setup/api/start").status_code == 403


def test_start_calls_finish_once_when_all_steps_done(env, helper, monkeypatch):
    assert firstrun.set_hidden(False)
    c = _start_client(helper)

    def fake_view():
        fr = firstrun.load()
        return {
            "show": fr.shows,
            "hidden": fr.hidden,
            "finished": fr.finished,
            "restore_offer": False,
            "done": 2,
            "total": 2,
            "steps": [
                {"id": "name", "state": "done", "line": "Named", "target": "#settings/general"},
                {"id": "storage", "state": "done", "line": "1 location", "target": "#settings/storage"},
            ],
        }

    monkeypatch.setattr(getting_started, "view", fake_view)
    first = c.get("/setup/api/start").json()
    assert first["show"] is True and first["done"] == first["total"] == 2
    assert firstrun.load().finished is True

    second = c.get("/setup/api/start").json()
    assert second["show"] is False and second["finished"] is True


def test_general_view_exposes_port_and_reader_install_pending(env, helper, monkeypatch):
    cfg = config.load()
    cfg["port"] = 8443
    cfg["modules"] = {**cfg.get("modules", {}), "local_intake": True}
    config.save(cfg)
    monkeypatch.setattr(local_intake_install, "installed", lambda: None)
    app = make(env, helper, add={"local_intake"})
    r = client(app).get("/setup/api/settings/general")
    assert r.status_code == 200
    data = r.json()
    assert data["port"] == 8443
    assert data["reader_install_pending"] is True


def test_port_change_route_validates_and_starts_helper_job(env, helper, monkeypatch):
    monkeypatch.setattr(hubport, "port_state", lambda _p: False)
    cfg = config.load()
    cfg["modules"] = {**cfg.get("modules", {}), "remote": True}
    config.save(cfg)
    app = make(env, helper, add={"remote"})
    c = client(app)

    assert post(c, "settings/port", {"port": 8443}, code=None).status_code == 403
    assert post(c, "settings/port", {"port": 8443}, code="00000000").status_code == 403

    for bad in (0, 80, "abc", 70000, True, 443):
        r = post(c, "settings/port", {"port": bad})
        assert r.status_code == 400 and r.json().get("field") == "port"

    cfg = config.load()
    cfg["modules"] = {**cfg.get("modules", {}), "remote": False}
    config.save(cfg)
    app_no_remote = make(env, helper, drop={"remote"})
    ok = post(client(app_no_remote), "settings/port", {"port": 443})
    assert ok.status_code == 200
    assert ok.json() == {"ok": True, "port": 443, "job": "port_change"}
    assert "port_change" in helper.calls
    pending = config.paths().etc / "pending_port"
    assert pending.read_text(encoding="ascii").strip() == "443"
    if hasattr(os, "geteuid"):
        assert (pending.stat().st_mode & 0o777) == 0o600


def test_helper_port_change_writes_pending_port_and_run_job_consumes_it(env, monkeypatch):
    pending = config.paths().etc / "pending_port"
    reply = setup_helper.handle({"op": "port_change", "code": CODE, "port": 8443})
    assert reply == {"ok": True, "status": 200, "name": "port_change"}
    assert pending.read_text(encoding="ascii").strip() == "8443"

    ran: list[list[str]] = []
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda cmd, **kw: ran.append(list(cmd)) or SimpleNamespace(returncode=0),
    )
    rc = setup_helper.run_job("port_change")
    assert rc == 0
    assert ran == [[setup_helper.WRAPPER, "port", "8443", "--yes"]]
    assert not pending.exists()

    # Missing pending_port fails clearly without launching wrapper
    assert setup_helper.run_job("port_change") == 1
    assert "error:" in setup_helper.job_log().read_text(encoding="utf-8")

    # Non-numeric pending_port fails and gets cleaned up
    pending.write_text("bad-port\n", encoding="ascii")
    assert setup_helper.run_job("port_change") == 1
    assert not pending.exists()

    # Remote module blocks port 443 in helper too
    cfg = config.load()
    cfg["modules"]["remote"] = True
    config.save(cfg)
    r443 = setup_helper.handle({"op": "port_change", "code": CODE, "port": 443})
    assert r443["ok"] is False and r443["status"] in (400, 409)

    # If _start_job fails, pending_port is removed
    monkeypatch.setattr(setup_helper, "_start_job", lambda *a, **k: (_ for _ in ()).throw(setup_helper.Refused(500, "boom")))
    r8888 = setup_helper.handle({"op": "port_change", "code": CODE, "port": 8888})
    assert r8888["ok"] is False and r8888["status"] == 500
    assert not pending.exists()


def test_reader_health_and_test_forward_trust(env, helper, monkeypatch):
    calls: list[tuple[str, bool]] = []

    def fake_test(cfg, timeout=3, trust=False):
        url = (cfg.get("local_intake") or {}).get("url") or "http://127.0.0.1:7915"
        calls.append((url, trust))
        return {
            "ok": trust or not url.startswith("https://"),
            "url": url,
            "version": "1.0",
            "api_version": 1,
            "tiers": {"cpu": {"usable": True}},
            "degraded": False,
            "revise": True,
            "error": None if trust else "cert changed",
            "fp": "abcd1234ef567890",
            "pin": "replaced" if trust else "changed",
            "https": url.startswith("https://"),
            "warning": None if trust else "changed",
        }

    monkeypatch.setattr(local_reader, "test", fake_test)
    cfg = config.load()
    cfg["modules"] = {**cfg.get("modules", {}), "local_intake": True}
    config.save(cfg)
    app = make(env, helper, add={"local_intake"})
    c = client(app)

    r_get = c.get("/setup/api/settings/local-intake")
    assert r_get.status_code == 200
    assert calls[-1][1] is False and r_get.json()["pin"] == "changed"

    r_post = post(c, "settings/local-intake/test", {"url": "https://reader.local:7915", "tier": "auto"})
    assert r_post.status_code == 200
    assert calls[-1] == ("https://reader.local:7915", True)
    assert r_post.json()["pin"] == "replaced" and r_post.json()["fp"] == "abcd1234ef567890"


def test_setup_start_static_assets_present_and_iife_scoped():
    js = (STATIC / "setup_start.js").read_text(encoding="utf-8")
    css = (STATIC / "setup_start.css").read_text(encoding="utf-8")
    assert "window.gettingStartedCard" in js
    assert re.search(r"^\s*\(\(\)\s*=>\s*\{", js, re.M)
    assert not re.search(r"^(?:function|const|let|var)\s+", js, re.M)
    assert ".start__step" in css
