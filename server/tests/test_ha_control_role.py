"""Tests for Feature 8: Opt-in Home Assistant control role (`ha-control`)."""
from __future__ import annotations

import json

from starlette.testclient import TestClient

from filament_kit import api, cli, config, modules, pairing
from filament_kit.bridge import registry
from filament_kit.spoolman import Spoolman
from test_setup_page import CODE, SERIAL, FakeManager, app, client, env, helper, make, manager, post  # noqa: F401


def test_pairing_ha_control_role_authorize_and_device_type(env):
    assert pairing.HA_CONTROL_ROLE == "ha-control"
    pairing.make_cert("kit", ["192.0.2.5"], config.paths().etc)
    pr_ctrl = pairing.issue(device_hint="home-assistant", role=pairing.HA_CONTROL_ROLE)
    pr_view = pairing.issue(device_hint="home-assistant", role=pairing.VIEWER_ROLE)

    got_ctrl = pairing.authorize(pr_ctrl.token)
    got_view = pairing.authorize(pr_view.token)
    assert got_ctrl is not None and got_ctrl[1] == pairing.HA_CONTROL_ROLE
    assert got_view is not None and got_view[1] == pairing.VIEWER_ROLE

    devs = {d["id"]: d for d in pairing.devices()}
    assert devs[got_ctrl[0]]["type"] == "home-assistant"
    assert devs[got_view[0]]["type"] == "home-assistant"


def test_ha_control_routes_and_authed_enforcement(env, manager):
    expected_control = {
        ("POST", "/api/printers/{id}/command"),
        ("POST", "/api/printers/{id}/slots/{label}/load"),
        ("POST", "/api/printers/{id}/slots/auto/load"),
        ("POST", "/api/printers/{id}/slots/{label}/unload"),
        ("POST", "/api/printers/{id}/slots/{label}/clear"),
    }
    assert set(api.VIEWER_ROUTES) <= set(api.HA_CONTROL_ROUTES)
    assert expected_control <= set(api.HA_CONTROL_ROUTES)

    registry.upsert(registry.Printer(SERIAL, "H2D", "H2D", "192.0.2.40", "12345678", True))
    pairing.make_cert("kit", ["192.0.2.5"], config.paths().etc)
    pr_ctrl = pairing.issue(device_hint="home-assistant", role=pairing.HA_CONTROL_ROLE)
    pr_view = pairing.issue(device_hint="home-assistant", role=pairing.VIEWER_ROLE)

    on = modules.enabled(config.load()) | {"printers"}
    app = api.create_app(Spoolman("http://127.0.0.1:9"), printers=manager, camera_hub=object(),
                         mapper=object(), enabled_modules=on)
    c = TestClient(app)
    h_ctrl = {"Authorization": f"Bearer {pr_ctrl.token}"}
    h_view = {"Authorization": f"Bearer {pr_view.token}"}

    # Viewer stays strictly read-only (403 on control routes and all writes)
    for path in (
        f"/api/printers/{SERIAL}/command",
        f"/api/printers/{SERIAL}/slots/A1/load",
        f"/api/printers/{SERIAL}/slots/auto/load",
        f"/api/printers/{SERIAL}/slots/A1/unload",
        f"/api/printers/{SERIAL}/slots/A1/clear",
        "/api/alerts",
    ):
        method = "PATCH" if path == "/api/alerts" else "POST"
        assert c.request(method, path, json={}, headers=h_view).status_code == 403

    # HA_CONTROL_ROLE passes _authed on the 5 control routes (not 403) and GET viewer routes
    assert c.get("/api/info", headers=h_ctrl).status_code == 200
    for path in (
        f"/api/printers/{SERIAL}/command",
        f"/api/printers/{SERIAL}/slots/A1/load",
        f"/api/printers/{SERIAL}/slots/auto/load",
        f"/api/printers/{SERIAL}/slots/A1/unload",
        f"/api/printers/{SERIAL}/slots/A1/clear",
    ):
        assert c.post(path, json={}, headers=h_ctrl).status_code != 403

    # HA_CONTROL_ROLE gets 403 on all other writes
    assert c.patch("/api/alerts", json={"low_spool_g": 100}, headers=h_ctrl).status_code == 403
    assert c.patch("/api/spools/1", json={}, headers=h_ctrl).status_code == 403
    assert c.post("/api/printers", json={}, headers=h_ctrl).status_code == 403
    assert c.delete(f"/api/printers/{SERIAL}", headers=h_ctrl).status_code == 403
    assert c.post("/api/events/1/undo", json={}, headers=h_ctrl).status_code == 403


def test_setup_pair_home_assistant_control_flag(app):
    pairing.make_cert("kit", ["192.0.2.5"], config.paths().etc)
    c = client(app)
    r_ctrl = post(c, "pair", {"kind": "home-assistant", "control": True})
    assert r_ctrl.status_code == 200
    tok_ctrl = pairing.redeem_code(r_ctrl.json()["code"])
    assert pairing.role_of(pairing.authorize(tok_ctrl)[0]) == pairing.HA_CONTROL_ROLE

    r_view = post(c, "pair", {"kind": "home-assistant"})
    assert r_view.status_code == 200
    tok_view = pairing.redeem_code(r_view.json()["code"])
    assert pairing.role_of(pairing.authorize(tok_view)[0]) == pairing.VIEWER_ROLE


def test_cli_pair_home_assistant_control_flag(env, capsys, monkeypatch):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "kit.local")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    (config.paths().etc / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    monkeypatch.setattr(cli, "_is_root", lambda: True)

    rc = cli.main(["--json", "pair", "--home-assistant", "--control", "--no-wait"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["ok"] is True
    tok = pairing.redeem_code(out["code"])
    assert pairing.role_of(pairing.authorize(tok)[0]) == pairing.HA_CONTROL_ROLE

    # --control without --home-assistant is refused
    rc_bad = cli.cmd_pair(control=True, wait=False)
    assert rc_bad == 2
