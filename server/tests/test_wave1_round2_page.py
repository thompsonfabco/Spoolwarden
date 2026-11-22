"""Regression tests for wave 1 round 2 Hub page, helper, and station fixes."""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from filament_kit import backup, setup_code, setup_helper, trusted
from filament_kit.setup_console import start_tab
from filament_kit.setup_page import SetupPage
from filament_kit.station import guard as station_guard, page as station_page, settings as station_settings
from test_setup_page import BASE, CODE, H, HOME, HOSTNAME, Helper, client, env, helper, make  # noqa: F401


def test_backup_open_forwards_optional_password(env, monkeypatch):
    seen = {}

    def fake_open(name, password=None):
        seen["name"] = name
        seen["password"] = password
        fd = os.open(__file__, os.O_RDONLY)
        return fd, "test.zip.enc"

    monkeypatch.setattr(backup, "open_for_download", fake_open)
    out = setup_helper._backup_open({"name": "daily.tar.gz.enc", "password": "old-secret-password"})
    os.close(out["fd"])
    assert seen == {"name": "daily.tar.gz.enc", "password": "old-secret-password"}
    assert out["download"] == "test.zip.enc"


def test_run_job_marks_backup_restore_update_with_page_actor(env, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["actor"] = kw["env"].get("FILAMENT_KIT_ACTOR")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(setup_helper.subprocess, "run", fake_run)
    for job in ("backup", "restore", "update", "rollback"):
        assert setup_helper.run_job(job) == 0
        assert seen["actor"] == "page"


def test_start_tab_passes_helper_backup_done_without_listing_folder(env, helper, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("backup.listing must not be called from start_tab")

    monkeypatch.setattr(backup, "listing", boom)
    fake_helper = lambda req: ({"ok": True, "last": "2026-11-21 12:00"}
                               if req.get("op") == "backup_last" else {"ok": True})
    page = SetupPage(helper=fake_helper, hosts=lambda: {HOSTNAME})
    c = TestClient(Starlette(routes=[Route("/setup/api" + p, fn, methods=[m]) for p, fn, m in start_tab.routes(page)]),
                   base_url=BASE, client=HOME)
    r = c.get("/setup/api/start")
    assert r.status_code == 200
    steps = {s["id"]: s for s in r.json()["steps"]}
    assert steps["backup"]["state"] == "done"


def test_trusted_cookie_is_scoped_to_setup_path_for_cohosted_station(env, monkeypatch):
    from test_setup_page import Helper
    trusted.set_enabled(True)
    _, token = trusted.add("Workshop computer", HOME[0])
    c = client(make(env, Helper()))
    c.cookies.set(trusted.COOKIE, token)
    r = c.post("/setup/api/settings",
               json={"key": "station.url", "value": "http://station.example.com:5003"},
               headers=H)
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert f"{trusted.COOKIE}=" in set_cookie
    assert "Path=/setup" in set_cookie
    assert "Path=/;" not in set_cookie and not set_cookie.endswith("Path=/")


def test_station_texts_settings_and_guard_use_catalogue_keys():
    cat = json.loads((Path(__file__).resolve().parents[2] / "strings" / "en.json").read_text(encoding="utf-8"))
    js = (Path(station_page.__file__).resolve().parent / "static" / "station.js").read_text(encoding="utf-8")
    used = set(re.findall(r'T\["(station_page_[a-z_]+)"\]', js))
    assert used <= set(station_page.TEXTS)
    assert "station_page_page_from" in used and "station_page_page_from_subnets_note" in used
    for k in station_page.TEXTS:
        assert k in cat, k
    for k in station_guard.TEXTS:
        assert f"station_refusal_{k}" in cat, k
        assert station_guard.TEXTS[k] == cat[f"station_refusal_{k}"]
    specs = {s["id"]: s for s in station_settings.specs()}
    assert specs["beep"]["label"] == cat["station_setting_beep"]
    assert specs["voice"]["choice_labels"]["auto"] == cat["station_voice_auto"]
    assert specs["lamp.idle"]["choice_labels"]["off"] == cat["station_lamp_off"]
