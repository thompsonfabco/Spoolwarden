"""Regression tests for AP1 (printers/test, Klipper api_key, Bambu address add, WEB_ROUTES),
AP2 twin (GET /intake/api/share-corrections), and EV9b (`spoolwarden certificate --renew`)."""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography import x509
from starlette.testclient import TestClient

from filament_kit import cli, config, pairing, setup_page
from filament_kit.api import create_app
from filament_kit.bridge import family, registry
from filament_kit.bridge.klipper import moonraker
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


class _PrintersStub:
    def state(self, pid):
        return "connected"

    def summary(self, pid):
        return {"gcode_state": "IDLE", "percent": 0, "remaining_min": 0, "job": None}

    def status(self, pid):
        return {"ams": []}

    def sync(self):
        pass

    def wait_settled(self, pid, timeout):
        return "connected"

    def verified(self, pid):
        return None

    def camera_kind(self, pid):
        return None

    def model(self, pid):
        return (None, None)


def _client(spoolman_url, *, klipper_on: bool = True, bambu_on: bool = True):
    bambu = _PrintersStub() if bambu_on else None
    klipper = _PrintersStub() if klipper_on else None
    mods = {"server", "local_intake"}
    if bambu_on:
        mods.add("printers")
    if klipper_on:
        mods.add("klipper")
    sm = Spoolman(spoolman_url)
    app = create_app(sm=sm, printers=family.Printers(bambu, klipper), enabled_modules=frozenset(mods))
    tok = pairing.redeem_code(pairing.issue("phone").code)
    c = TestClient(app, raise_server_exceptions=False)
    c.headers["Authorization"] = f"Bearer {tok}"
    return c


def test_printer_test_reachable_true_false_and_saves_nothing(spoolman_url, monkeypatch):
    monkeypatch.setattr(setup_page, "CHECK_GAP", 0.0)
    seen = []

    class FakeMoonraker:
        def __init__(self, host, port, api_key="", **_kw):
            seen.append((host, port, api_key))

        def info(self):
            return {"state": "ready"}

    monkeypatch.setattr(moonraker, "Moonraker", FakeMoonraker)
    c = _client(spoolman_url)
    r = c.post("/api/printers/test", json={"family": "klipper", "host": "192.168.1.50", "port": 7125, "api_key": "k1"})
    assert r.status_code == 200 and r.json() == {"reachable": True}
    assert seen == [("192.168.1.50", 7125, "k1")]
    assert registry.load() == {}

    class OfflineMoonraker(FakeMoonraker):
        def info(self):
            raise moonraker.MoonrakerError("timeout")

    monkeypatch.setattr(moonraker, "Moonraker", OfflineMoonraker)
    r2 = c.post("/api/printers/test", json={"family": "klipper", "host": "192.168.1.51"})
    assert r2.status_code == 200 and r2.json() == {"reachable": False}
    assert registry.load() == {}


def test_printer_test_refuses_bad_inputs_unauthorized_bambu_and_klipper_off(spoolman_url, monkeypatch):
    monkeypatch.setattr(setup_page, "CHECK_GAP", 0.0)
    c = _client(spoolman_url)
    rb = c.post("/api/printers/test", json={"family": "bambu", "host": "192.168.1.50"})
    assert rb.status_code == 400 and rb.json()["message"] == "A Bambu Lab printer is tested when it is added."

    assert c.post("/api/printers/test", json={"family": "klipper", "host": ""}).status_code == 400
    assert c.post("/api/printers/test", json={"family": "klipper", "host": "192.168.1.50", "port": 99999}).status_code == 400
    assert c.post("/api/printers/test", json={"family": "klipper", "host": "192.168.1.50", "api_key": "bad key"}).status_code == 400

    class AuthFailMoonraker:
        def __init__(self, *a, **kw):
            pass

        def info(self):
            raise moonraker.Unauthorized("nope")

    monkeypatch.setattr(moonraker, "Moonraker", AuthFailMoonraker)
    ru = c.post("/api/printers/test", json={"family": "klipper", "host": "192.168.1.50", "api_key": "wrong"})
    assert ru.status_code == 400 and ru.json()["field"] == "api_key"

    off = _client(spoolman_url, klipper_on=False)
    ro = off.post("/api/printers/test", json={"family": "klipper", "host": "192.168.1.50"})
    assert ro.status_code == 409 and ro.json()["error"] == "module_off"


def test_printer_test_token_scope_web_printer_changes_and_viewer(spoolman_url, monkeypatch):
    monkeypatch.setattr(setup_page, "CHECK_GAP", 0.0)

    class FakeMoonraker:
        def __init__(self, *a, **kw):
            pass

        def info(self):
            return {"state": "ready"}

    monkeypatch.setattr(moonraker, "Moonraker", FakeMoonraker)
    sm = Spoolman(spoolman_url)
    app = create_app(sm=sm, printers=family.Printers(_PrintersStub(), _PrintersStub()), enabled_modules=ON)
    c = TestClient(app, raise_server_exceptions=False)

    web_tok = pairing.redeem_code(pairing.issue("web (MCP)", role=pairing.WEB_ROLE).code)
    wh = {"Authorization": f"Bearer {web_tok}"}
    r_off = c.post("/api/printers/test", headers=wh, json={"family": "klipper", "host": "192.168.1.50"})
    assert r_off.status_code == 403 and r_off.json()["error"] == "public_printer_changes_off"

    config.save({**config.load(), "web": {"printer_changes": True}})
    r_on = c.post("/api/printers/test", headers=wh, json={"family": "klipper", "host": "192.168.1.50"})
    assert r_on.status_code == 200 and r_on.json() == {"reachable": True}
    assert c.get("/api/intake/share-corrections", headers=wh).status_code == 200

    viewer_tok = pairing.redeem_code(pairing.issue("viewer", role=pairing.VIEWER_ROLE).code)
    vh = {"Authorization": f"Bearer {viewer_tok}"}
    assert c.post("/api/printers/test", headers=vh, json={"family": "klipper", "host": "192.168.1.50"}).status_code == 403
    assert c.post("/api/printers", headers=vh, json={"family": "klipper", "host": "192.168.1.50"}).status_code == 403
    assert c.get("/api/intake/share-corrections", headers=vh).status_code == 403


def test_klipper_add_saves_and_validates_api_key(spoolman_url):
    c = _client(spoolman_url)
    bad = c.post("/api/printers", json={"family": "klipper", "host": "192.168.1.60", "api_key": "has space"})
    assert bad.status_code == 400 and bad.json()["field"] == "api_key"
    r = c.post("/api/printers", json={"family": "klipper", "host": "192.168.1.60", "port": 7125, "api_key": "secret-key"})
    assert r.status_code == 200
    pid = r.json()["printer"]["id"]
    assert registry.load()[pid].api_key == "secret-key"


def test_bambu_add_by_address_reads_serial_refuses_three_cases_and_409_exists(spoolman_url, monkeypatch):
    c = _client(spoolman_url)

    monkeypatch.setattr(setup_page, "setup_serial", lambda ip, timeout=5: (_ for _ in ()).throw(OSError("timed out")))
    r1 = c.post("/api/printers", json={"family": "bambu", "address": "192.168.1.70", "access_code": "12345678"})
    assert r1.status_code == 400 and "did not answer" in r1.json()["message"] and r1.json()["field"] == "serial"

    monkeypatch.setattr(setup_page, "setup_serial", lambda ip, timeout=5: None)
    r2 = c.post("/api/printers", json={"family": "bambu", "address": "192.168.1.70", "access_code": "12345678"})
    assert r2.status_code == 400 and "did not name its serial" in r2.json()["message"] and r2.json()["field"] == "serial"

    monkeypatch.setattr(setup_page, "setup_serial", lambda ip, timeout=5: "01P00A111111111")
    r3 = c.post("/api/printers", json={"family": "bambu", "address": "192.168.1.70", "access_code": "12345678",
                                       "serial": "01P00A222222222"})
    assert r3.status_code == 400 and "names itself 01P00A111111111, not 01P00A222222222" in r3.json()["message"]

    r_ok = c.post("/api/printers", json={"family": "bambu", "address": "192.168.1.70", "access_code": "12345678",
                                         "name": "Lab X1C"})
    assert r_ok.status_code == 200
    assert r_ok.json()["printer"]["id"] == "01P00A111111111"

    r_dup = c.post("/api/printers", json={"family": "bambu", "address": "192.168.1.70", "access_code": "12345678"})
    assert r_dup.status_code == 409 and r_dup.json()["error"] == "exists"


def test_web_twin_share_corrections_forwards_kit_and_lan_only(spoolman_url):
    sm = Spoolman(spoolman_url)
    kit_app = create_app(sm=sm, printers=family.Printers(_PrintersStub(), _PrintersStub()), enabled_modules=ON)
    kit_tc = TestClient(kit_app, raise_server_exceptions=False)
    web_tok = pairing.redeem_code(pairing.issue("web", role=pairing.WEB_ROLE).code)
    expected = kit_tc.get("/api/intake/share-corrections", headers={"Authorization": f"Bearer {web_tok}"}).json()

    class KitProxy:
        def get(self, path):
            r = kit_tc.get(path, headers={"Authorization": f"Bearer {web_tok}"})
            return r.json()

    from starlette.applications import Starlette
    web = IntakeApp(type("_T", (), {"cfg": lambda self: config.load(), "sm": sm})(),
                    public_url="https://hub.example/app", lan_cidrs=["192.168.1.0/24"], kit_api=KitProxy())
    web_app = Starlette(routes=web.routes())
    lan_c = TestClient(web_app, client=("192.168.1.25", 50000))
    assert lan_c.get("/intake/api/share-corrections").json() == expected

    wan_c = TestClient(web_app, client=("8.8.8.8", 50000))
    assert wan_c.get("/intake/api/share-corrections").status_code == 403


def test_certificate_renew_prompts_renews_restarts_and_prints_fingerprint(tmp_path, monkeypatch, capsys):
    etc = Path(os.environ["FK_ETC"])
    old_fp = pairing.fingerprint((etc / "cert.pem").read_bytes())

    # Without --renew, existing cert is kept
    assert cli.main(["certificate"]) == 0
    out_keep = capsys.readouterr().out
    assert "Certificate present." in out_keep
    assert pairing.fingerprint((etc / "cert.pem").read_bytes()) == old_fp

    # With --renew, prompts exact R86 question; declining leaves cert unchanged
    prompts = []
    restarts = []
    monkeypatch.setattr(cli, "_restart_service", lambda: restarts.append(1))
    monkeypatch.setattr(cli, "_no_systemd", lambda: True)
    monkeypatch.setattr(cli.output, "terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda p="": prompts.append(capsys.readouterr().out + " " + p) or "n")
    assert cli.main(["certificate", "--renew"]) == 5
    out_decline = " ".join("".join(prompts).split())
    assert ("Make a new certificate for this Hub? Every paired phone, PC and Weigh Station will see a changed "
            "certificate and must trust it again or be paired again. Tag Readers set up for this Hub must be set up "
            "again.") in out_decline
    assert restarts == []

    # Accepting --renew makes a new cert with .local, restarts Hub, and prints fingerprint + Tag Reader line
    monkeypatch.setattr("builtins.input", lambda p="": "y")
    assert cli.main(["certificate", "--renew"]) == 0
    out_renew = capsys.readouterr().out
    assert "Made a new certificate." in out_renew
    assert "Tag Readers set up for this Hub must be set up again." in out_renew
    assert "Fingerprint:" in out_renew
    assert restarts == [1]
    cert = x509.load_pem_x509_certificate((etc / "cert.pem").read_bytes())
    dns = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    assert any(d.endswith(".local") for d in dns)
