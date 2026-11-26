"""Tests for Feature 6: Hub Network/LAN editor, /setup/api/state lockdown, and Mobile Alert Rules API."""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from filament_kit import alerts_api, api, config, modules, pairing, setup_code, setup_helper
from filament_kit.bridge import registry
from filament_kit.spoolman import Spoolman
from filament_kit.web import intake_app, mcp_tools
from test_setup_page import BASE, CODE, H, HOME, SERIAL, FakeManager, Helper, client, env, helper, make, manager, post  # noqa: F401
from test_setup_trusted import _trust


# ---------- 1. Hub Network / LAN Editor (setup_helper.py) ----------

def test_general_registers_web_lan_and_spoolman_allow_with_apply():
    assert setup_helper.GENERAL["web.lan"][1] == "apply"
    assert setup_helper.GENERAL["spoolman.allow"][1] == "apply"


def test_cidrs_validation_and_limits():
    assert setup_helper._cidrs(["192.168.1.0/24", "fd12:3456::/64", "192.168.1.5/24"], allow_empty=False) == [
        "192.168.1.0/24",
        "fd12:3456::/64",
    ]
    assert setup_helper._cidrs(None, allow_empty=True) is None
    assert setup_helper._cidrs([], allow_empty=True) is None

    for bad in ("0.0.0.0/0", "::/0"):
        with pytest.raises(ValueError, match="prefix length 0"):
            setup_helper._cidrs([bad], allow_empty=False)

    with pytest.raises(ValueError, match="at least one"):
        setup_helper._cidrs([], allow_empty=False)
    with pytest.raises(ValueError, match="at least one"):
        setup_helper._cidrs(None, allow_empty=False)

    too_many = [f"10.0.{i}.0/24" for i in range(33)]
    with pytest.raises(ValueError, match="at most 32"):
        setup_helper._cidrs(too_many, allow_empty=False)


def test_general_web_lan_prevents_self_lockout_for_non_loopback(env):
    r = setup_helper.handle({
        "op": "general",
        "code": CODE,
        "client": "192.0.2.20",
        "key": "web.lan",
        "value": ["10.0.0.0/8"],
    })
    assert r["ok"] is False and r["status"] == 400 and "lock out" in r["error"]
    assert config.load()["web"]["lan"] == ["192.0.2.0/24"]

    # general_group also refuses
    rg = setup_helper.handle({
        "op": "general_group",
        "code": CODE,
        "client": "192.0.2.20",
        "values": {"web.lan": ["10.0.0.0/8"], "server_name": "ShouldNotSave"},
    })
    assert rg["ok"] is False and rg["status"] == 400 and "web.lan" in rg["errors"]
    assert config.load()["web"]["lan"] == ["192.0.2.0/24"]

    # Loopback caller or matching range is allowed
    ok_loop = setup_helper.handle({
        "op": "general",
        "code": CODE,
        "client": "127.0.0.1",
        "key": "web.lan",
        "value": ["10.0.0.0/8"],
    })
    assert ok_loop["ok"] is True and ok_loop["applies"] is True
    assert config.load()["web"]["lan"] == ["10.0.0.0/8"]

    # spoolman.allow accepts empty -> None
    ok_sm = setup_helper.handle({
        "op": "general",
        "code": CODE,
        "client": "127.0.0.1",
        "key": "spoolman.allow",
        "value": [],
    })
    assert ok_sm["ok"] is True and ok_sm["applies"] is True
    assert config.load()["spoolman"]["allow"] is None


# ---------- 2. /setup/api/state Lockdown ----------

def test_setup_state_redacts_when_locked_and_unlocks_by_header_cookie_post_or_unset(env, helper, manager, monkeypatch):
    registry.upsert(registry.Printer(SERIAL, "H2D", "H2D", "192.0.2.40", "12345678", True))
    registry.add_klipper("Voron", "printer.example.com", 7125)
    app = make(env, helper, manager, add=frozenset({"klipper"}))
    c = client(app)

    # Locked: redacted
    r_locked = c.get("/setup/api/state")
    assert r_locked.status_code == 200
    d_locked = r_locked.json()
    assert d_locked["printers_redacted"] is True
    assert [p["serial"] for p in d_locked["printers"]["list"]] == ["locked-1", "locked-2"]
    assert all(p["redacted"] is True and p["ip"] == "" for p in d_locked["printers"]["list"])
    assert SERIAL not in r_locked.text and "192.0.2.40" not in r_locked.text and "printer.example.com" not in r_locked.text

    # Unlocked via X-Setup-Code header
    r_hdr = c.get("/setup/api/state", headers={"X-Setup-Code": CODE})
    assert r_hdr.status_code == 200 and r_hdr.json()["printers_redacted"] is False
    assert all(p["redacted"] is False for p in r_hdr.json()["printers"]["list"])
    assert any(p["serial"] == SERIAL and p["ip"] == "192.0.2.40" for p in r_hdr.json()["printers"]["list"])

    # Unlocked via POST /setup/api/state
    r_post = post(c, "state", {})
    assert r_post.status_code == 200 and r_post.json()["printers_redacted"] is False

    # Unlocked via trusted cookie
    c_trust = client(app)
    _trust(c_trust)
    assert c_trust.get("/setup/api/state").json()["printers_redacted"] is False
    assert c_trust.get("/setup/api/printers/states").json()["printers_redacted"] is False

    # Unlocked when no setup code is set
    monkeypatch.setattr(setup_code, "is_set", lambda: False)
    assert client(app).get("/setup/api/state").json()["printers_redacted"] is False


# ---------- 3. Mobile Alert Rules API (api.py & intake_app.py) ----------

def test_api_alerts_patch_saves_and_maps_refusals(env):
    pairing.make_cert("kit", ["192.0.2.5"], config.paths().etc)
    pr = pairing.issue(device_hint="phone")
    tok = pr.token
    app = api.create_app(Spoolman("http://127.0.0.1:9"), enabled_modules=modules.enabled(config.load()))
    c = TestClient(app)
    hdr = {"Authorization": f"Bearer {tok}"}

    r_ok = c.patch("/api/alerts", json={"running_low_g": 120}, headers=hdr)
    assert r_ok.status_code == 200
    assert r_ok.json()["running_low_g"] == 120

    r_bad = c.patch("/api/alerts", json={"running_low_g": -5}, headers=hdr)
    assert r_bad.status_code == 400
    body = r_bad.json()
    assert body["error"] == "invalid" and body["field"] == "running_low_g"


def test_home_web_routes_allows_alerts_patch_and_viewer_stays_get_only(env):
    assert ("PATCH", "/api/alerts") in api.HOME_WEB_ROUTES
    assert all(m == "GET" for m, _ in api.VIEWER_ROUTES)

    pairing.make_cert("kit", ["192.0.2.5"], config.paths().etc)
    pr_viewer = pairing.issue(device_hint="home-assistant", role=pairing.VIEWER_ROLE)
    app = api.create_app(Spoolman("http://127.0.0.1:9"), enabled_modules=modules.enabled(config.load()))
    c = TestClient(app)
    r = c.patch("/api/alerts", json={"low_spool_g": 100}, headers={"Authorization": f"Bearer {pr_viewer.token}"})
    assert r.status_code == 403


def test_intake_app_alerts_patch_home_only_and_forwards():
    class FakeKit:
        def __init__(self):
            self.patched = []

        def patch(self, path, body):
            self.patched.append((path, body))
            return {"ok": True, **body}

    kit = FakeKit()
    tools = mcp_tools.Tools(Spoolman("http://127.0.0.1:9"), kit_api=kit, kit_writes=True)
    home_app = Starlette(routes=intake_app.IntakeApp(
        tools, public_url=None, lan_cidrs=["192.0.2.0/24"], push=False,
        host_ok=lambda r: True, kit_api=kit,
    ).routes())
    with TestClient(home_app, base_url=BASE, client=HOME) as c_home:
        r_home = c_home.patch("/intake/api/alerts", json={"low_spool_g": 150}, headers=H)
        assert r_home.status_code == 200
        assert kit.patched == [("/api/alerts", {"low_spool_g": 150})]

    pub_app = Starlette(routes=intake_app.IntakeApp(
        tools, public_url="https://hub.example.com/filament", lan_cidrs=["192.0.2.0/24"], push=False,
        host_ok=lambda r: True, kit_api=kit,
    ).routes())
    with TestClient(pub_app, base_url="https://hub.example.com", client=HOME) as c_pub:
        r_pub = c_pub.patch("/intake/api/alerts", json={"low_spool_g": 150}, headers={"Origin": "https://hub.example.com"})
        assert r_pub.status_code == 403 and r_pub.json()["error"] == "home_only"
