"""The Tag Reader's API on the Hub's own home listener (api._intake_web_app mounts web.reader.Reader): the five /reader
routes answer on the app the real factory builds, with Remote access (the web unit, web.public_url) not configured, under
the same reader-token and home-network checks as the web unit's routes (test_reader.py holds their behaviour)."""
from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from starlette.testclient import TestClient  # noqa: E402

from filament_kit import config, modules, pairing, reader_config  # noqa: E402
from filament_kit.api import create_app  # noqa: E402
from filament_kit.bridge import registry  # noqa: E402
from filament_kit.web import reader, slots  # noqa: E402
from test_api_printers import FakeManager  # noqa: E402
from test_reader import AWAY, HOME, UID, FakeSM, spool  # noqa: E402

SERIAL = "01P00A111111111"
PRESET = {"filament_id": "PFUS1", "name": "SUNLU PLA+ @X1C", "filament_type": "PLA"}


@pytest.fixture
def hub(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    pairing.make_cert("fkhub", ["192.168.1.5"], tmp_path / "etc")
    # the home server preset: printers on, and no web unit, no public address (nothing of Remote access)
    config.save({"modules": {n: True for n in modules.MODES["server"] if n != "web"}, "web": {"lan": ["192.168.1.0/24"]}})
    cfg = config.load()
    assert "web" not in modules.enabled(cfg) and not (cfg.get("web") or {}).get("public_url")
    registry.add_bambu(SERIAL, "Garage", "X1C", "192.168.1.50", "12345678")
    mgr = FakeManager()
    mgr.sync()
    mgr.statuses[SERIAL] = {"gcode_state": "IDLE", "ams": [{"unit": 0, "label": "A", "kind": "ams", "slots": [
        {"label": "A1", "type": "PLA", "present": True, "reading": False, "spool": None},
        {"label": "A2", "type": "", "present": False, "reading": False, "spool": None}]}]}
    app = create_app(FakeSM([spool(7, UID)]), printers=mgr)

    def kit_over_the_app(self, method, path, body=None):
        """KitApi with its transport (the loopback HTTPS socket) swapped for a call into this same app: the pairing,
        the bearer token and the kit routes are the real ones."""
        r = TestClient(app).request(method, path, json=body, headers={"Authorization": f"Bearer {self._token()}"})
        if r.status_code >= 400:
            err = r.json()
            raise slots.SlotError(err.get("message") or "", code=err.get("error"), http=r.status_code)
        return r.json()
    monkeypatch.setattr(slots.KitApi, "_call", kit_over_the_app)
    monkeypatch.setattr("filament_kit.api.pending.load_or_wait",
                        lambda manager, sm, serial, label, spool_id, dry, **kw: {"preset": dict(PRESET), "dry_run": dry,
                                                                                  "confirmed": None, "spool": {"id": spool_id}})
    (tmp_path / "state" / "reader-fw").mkdir()
    (tmp_path / "state" / "reader-fw" / "manifest.json").write_text('{"build": 1}')
    port = config.load()["port"]
    return app, f"https://fkhub.local:{port}"


def test_a_reader_calls_all_five_routes_on_the_hubs_home_listener(hub):
    app, base = hub
    token = reader.TokenStore().issue("bench")[1]
    h = {"X-Auth-Token": token}
    c = TestClient(app, base_url=base, client=HOME)
    assert c.get("/reader/healthz", headers=h).text == "ok"
    assert c.get("/reader/fw/manifest.json", headers=h).json() == {"build": 1}
    printers = c.get("/reader/printers", headers=h)
    assert printers.status_code == 200 and [(p["id"], p["name"]) for p in printers.json()] == [(SERIAL, "Garage")]
    rows = c.get(f"/reader/printers/{SERIAL}/slots", headers=h)
    assert rows.status_code == 200 and [s["label"] for s in rows.json()] == ["A1", "A2"]
    scan = c.post("/reader/scan", headers=h, json={"uid": UID, "printer": SERIAL, "slot": "A1", "dry_run": True})
    assert scan.status_code == 200, scan.text
    assert scan.json()["status"] == "set" and scan.json()["filament"] == "SUNLU PLA+ Grey"
    assert scan.json()["preset"] == PRESET["name"]


def test_the_hubs_reader_routes_keep_the_token_and_home_network_checks(hub):
    app, base = hub
    token = reader.TokenStore().issue("bench")[1]
    home, away = TestClient(app, base_url=base, client=HOME), TestClient(app, base_url=base, client=AWAY)
    assert home.get("/reader/healthz").status_code == 401
    assert home.get("/reader/healthz", headers={"X-Auth-Token": "x" * 43}).status_code == 401
    assert away.get("/reader/healthz", headers={"X-Auth-Token": token}).status_code == 403
    assert away.get("/reader/printers", headers={"X-Auth-Token": token}).status_code == 403


def test_an_unknown_tag_and_a_missing_firmware_answer_as_the_web_unit_does(hub):
    app, base = hub
    h = {"X-Auth-Token": reader.TokenStore().issue("bench")[1]}
    c = TestClient(app, base_url=base, client=HOME)
    r = c.post("/reader/scan", headers=h, json={"uid": "4:aa:bb:cc:dd:ee:ff", "printer": SERIAL, "slot": "A1"})
    assert r.status_code == 200 and r.json()["status"] == "unknown_tag"
    assert c.get("/reader/fw/firmware.bin", headers=h).status_code == 404


def test_reader_config_and_ack_work_on_base_hub_without_web_feature(hub):
    app, base = hub
    tid, token = reader.TokenStore().issue("bench")
    reader_config.update(tid, {"buzzer_low": True, "scan_cooldown_ms": 1500})
    reader_config.queue_command(tid, "ota")
    c = TestClient(app, base_url=base, client=HOME)
    cfg_resp = c.get("/reader/config", headers={"X-Auth-Token": token})
    assert cfg_resp.status_code == 200
    body = cfg_resp.json()
    assert body["buzzer_low"] is True and body["scan_cooldown_ms"] == 1500
    assert body["ota_now"] is True and body["reboot_now"] is False
    ack_resp = c.post("/reader/config/ack", headers={"X-Auth-Token": token}, json={"cmd": "ota"})
    assert ack_resp.status_code == 200 and ack_resp.json() == {"ok": True, "cmd": "ota"}
    assert reader_config.pending(tid)["ota_now"] is False
