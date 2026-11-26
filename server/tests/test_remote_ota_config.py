"""Remote OTA and configuration: station settings, station OTA/restart HTTP + guard,
reader_config XML store + web /reader/config routes, and setup_console device_tab."""
from __future__ import annotations

import json
import os
import stat
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from filament_kit import config, reader_config, reader_tokens
from filament_kit.setup_console import device_tab
from filament_kit.setup_page import Refused
from filament_kit.station import flow, guard, ota
from filament_kit.station import http as station_http
from filament_kit.station import page as station_page
from filament_kit.station import settings as station_settings
from filament_kit.station import state as st
from filament_kit.web import reader as web_reader


@pytest.fixture
def fk_env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir(exist_ok=True)
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    config.save({"modules": {"spoolman": True, "server": True, "web": True},
                 "web": {"public_url": "https://hub.example.test/filament", "lan": ["192.168.1.0/24"]}})
    return tmp_path


# ---------- 1. Weigh Station settings.Store (new settings) ----------

def _make_station_store(tmp_path, **installed):
    target = {
        "beep": True,
        "speech": True,
        "tts": "auto",
        "speaker_volume": 50,
        "panel_swap": False,
        "flow_timeout": 600,
        "recognise_timeout": 90,
        "min_grams": 20,
        "camera": "auto",
        "panel_flash": True,
        "auto_ota": True,
        **installed,
    }
    lamps: dict = {}
    told: list = []
    store = station_settings.Store(tmp_path / "state" / "settings.xml", target, lamps, after=told.append)
    return store, target, lamps, told


def test_station_settings_includes_new_specs_and_persists(tmp_path):
    store, target, _, told = _make_station_store(tmp_path)
    by_id = {s["id"]: s for s in store.listing()}
    for key in ("flow_timeout", "recognise_timeout", "min_grams", "camera", "panel_flash", "auto_ota"):
        assert key in by_id
    assert by_id["flow_timeout"]["min"] == 30 and by_id["flow_timeout"]["max"] == 3600
    assert by_id["recognise_timeout"]["min"] == 15 and by_id["recognise_timeout"]["max"] == 300
    assert by_id["min_grams"]["min"] == 1 and by_id["min_grams"]["max"] == 500
    assert by_id["camera"]["choices"] == ["auto", "off"]

    out = store.change({
        "flow_timeout": 300,
        "recognise_timeout": 45,
        "min_grams": 35,
        "camera": "off",
        "panel_flash": False,
        "auto_ota": False,
    })
    values = {s["id"]: s["value"] for s in out}
    assert values["flow_timeout"] == 300
    assert values["recognise_timeout"] == 45
    assert values["min_grams"] == 35
    assert values["camera"] == "off"
    assert values["panel_flash"] is False
    assert values["auto_ota"] is False
    assert target["flow_timeout"] == 300 and target["recognise_timeout"] == 45 and target["min_grams"] == 35
    assert target["camera"] == "off" and target["panel_flash"] is False and target["auto_ota"] is False
    assert told == [{"flow_timeout", "recognise_timeout", "min_grams", "camera", "panel_flash", "auto_ota"}]

    reloaded, target2, _, _ = _make_station_store(tmp_path)
    assert reloaded.changed is True
    assert target2["flow_timeout"] == 300 and target2["auto_ota"] is False and target2["camera"] == "off"

    store.reset()
    assert store.changed is False
    assert target["flow_timeout"] == 600 and target["auto_ota"] is True and target["camera"] == "auto"


@pytest.mark.parametrize("bad", [
    {"flow_timeout": 29},
    {"flow_timeout": 3601},
    {"recognise_timeout": 14},
    {"recognise_timeout": 301},
    {"min_grams": 0},
    {"min_grams": 501},
    {"camera": "usb"},
    {"panel_flash": "maybe"},
    {"auto_ota": "yes"},
])
def test_station_settings_rejects_invalid_new_values(tmp_path, bad):
    store, *_ = _make_station_store(tmp_path)
    with pytest.raises(station_settings.SettingsError):
        store.change(bad)


def test_after_change_recognise_timeout_clears_cached_api(monkeypatch):
    monkeypatch.setattr(flow, "_api", {"key": ("k",), "api": object()})
    station_settings.after_change({"recognise_timeout"})
    assert flow._api["api"] is None


# ---------- 2. station/ota.py & station/http.py ----------

@pytest.fixture
def reset_ota(monkeypatch):
    with ota._lock:
        ota._status.update(state="idle", target_version=None, started_at=None,
                           finished_at=None, error=None, last_result=None)
    monkeypatch.setitem(st.settings, "auto_ota", True)
    yield
    with ota._lock:
        ota._status.update(state="idle", target_version=None, started_at=None,
                           finished_at=None, error=None, last_result=None)


def test_ota_trigger_update_and_status(reset_ota):
    done = threading.Event()
    seen = []

    def runner(ver):
        seen.append(ver)
        done.set()

    code, body = ota.trigger_update("0.9.0", runner=runner)
    assert code == 200 and body["ok"] is True and body["state"] == "updating"
    assert done.wait(5)
    deadline = time.time() + 5
    while ota.status()["state"] == "updating" and time.time() < deadline:
        time.sleep(0.01)
    assert seen == ["0.9.0"]
    s = ota.status()
    assert s["state"] == "idle" and s["last_result"] == "ok" and s["error"] is None


def test_ota_refuses_when_auto_ota_disabled_or_concurrent(reset_ota, monkeypatch):
    monkeypatch.setitem(st.settings, "auto_ota", False)
    code, body = ota.trigger_update("0.9.0", runner=lambda: None)
    assert code == 409 and body["error"] == "auto_ota_disabled"

    monkeypatch.setitem(st.settings, "auto_ota", True)
    gate = threading.Event()
    started = threading.Event()

    def slow():
        started.set()
        gate.wait(5)

    code1, _ = ota.trigger_update("0.9.0", runner=slow)
    assert code1 == 200 and started.wait(5)
    code2, body2 = ota.trigger_update("0.9.1", runner=lambda: None)
    assert code2 == 409 and body2["error"] == "ota_in_progress"
    code3, body3 = ota.trigger_restart(runner=lambda: None)
    assert code3 == 409 and body3["error"] == "ota_in_progress"
    gate.set()


def test_ota_trigger_restart_runs_runner(reset_ota):
    done = threading.Event()
    code, body = ota.trigger_restart(runner=done.set)
    assert code == 200 and body == {"ok": True, "restarting": True}
    assert done.wait(5)


def _http_call(url: str, method: str = "GET", body: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def test_station_http_ota_routes_proof_and_guard(tmp_path, reset_ota, monkeypatch):
    token = "StationTokenForOtaHttpTest_0123456789"
    key = station_page.token_key(token)
    access = station_page.Access(lambda: key)
    store, *_ = _make_station_store(tmp_path)
    monkeypatch.setattr(ota, "_default_update_runner", lambda ver=None: None)
    monkeypatch.setattr(ota, "_default_restart_runner", lambda: None)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), station_http.make_handler(
        st.view, {"127.0.0.1"}, access=access, store=store))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        code, body = _http_call(f"{base}/ota/status")
        assert code == 200 and body["state"] == "idle"

        # Missing or wrong-purpose proof is 403
        raw = json.dumps({"version": "1.2.3"}).encode()
        code, _ = _http_call(f"{base}/ota/update", "POST", raw, {"Content-Type": "application/json"})
        assert code == 403
        code, _ = _http_call(f"{base}/ota/update", "POST", raw, {
            "Content-Type": "application/json",
            station_page.PROOF_HEADER: station_page.make_proof(key, "restart", raw),
        })
        assert code == 403

        # Valid ota proof succeeds
        code, body = _http_call(f"{base}/ota/update", "POST", raw, {
            "Content-Type": "application/json",
            station_page.PROOF_HEADER: station_page.make_proof(key, "ota", raw),
        })
        assert code == 200 and body["ok"] is True

        # Wait for update thread to finish before restart
        deadline = time.time() + 5
        while ota.status()["state"] == "updating" and time.time() < deadline:
            time.sleep(0.01)

        # Valid restart proof succeeds; wrong purpose fails
        rraw = b"{}"
        code, _ = _http_call(f"{base}/restart", "POST", rraw, {
            "Content-Type": "application/json",
            station_page.PROOF_HEADER: station_page.make_proof(key, "ota", rraw),
        })
        assert code == 403
        code, body = _http_call(f"{base}/restart", "POST", rraw, {
            "Content-Type": "application/json",
            station_page.PROOF_HEADER: station_page.make_proof(key, "restart", rraw),
        })
        assert code == 200 and body == {"ok": True, "restarting": True}
    finally:
        srv.shutdown()

    # Non-allow-listed home caller with session cannot access /ota/status, /ota/update, or /restart
    assert guard.allowed(guard.HOME, "GET", "/ota/status", has_session=True) is False
    assert guard.allowed(guard.HOME, "POST", "/ota/update", has_session=True) is False
    assert guard.allowed(guard.HOME, "POST", "/restart", has_session=True) is False


# ---------- 3. reader_config.py & web/reader.py ----------

def test_reader_config_crud_commands_and_xml_persistence(fk_env):
    p = fk_env / "state" / "reader-config.xml"
    assert len(reader_config.specs()) == 11
    eff = reader_config.effective("r1", path=p)
    assert eff["buzzer_enabled"] is True and eff["dim_after_s"] == 120 and eff["default_printer"] == ""

    listed = reader_config.update("r1", {"buzzer_enabled": False, "dim_after_s": 45, "default_printer": "H2D"}, path=p)
    by_id = {s["id"]: s["value"] for s in listed}
    assert by_id["buzzer_enabled"] is False and by_id["dim_after_s"] == 45 and by_id["default_printer"] == "H2D"
    assert reader_config.pending("r1", path=p)["config_version"] == 1
    assert p.exists()
    if os.name == "posix":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600

    reader_config.queue_command("r1", "ota", path=p)
    reader_config.queue_command("r1", "reboot", path=p)
    assert reader_config.pending("r1", path=p) == {"config_version": 1, "ota_now": True, "reboot_now": True}

    reader_config.ack_command("r1", "ota", path=p)
    assert reader_config.pending("r1", path=p) == {"config_version": 1, "ota_now": False, "reboot_now": True}

    reset_listed = reader_config.reset("r1", path=p)
    assert {s["id"]: s["value"] for s in reset_listed}["dim_after_s"] == 120
    assert reader_config.pending("r1", path=p)["config_version"] == 2

    with pytest.raises(reader_config.ReaderConfigError):
        reader_config.update("r1", {"dim_after_s": 5}, path=p)
    with pytest.raises(reader_config.ReaderConfigError):
        reader_config.queue_command("r1", "wipe", path=p)


def test_web_reader_config_ack_and_build_tracking(fk_env, monkeypatch):
    tid, tok = reader_tokens.TokenStore().issue("bench")
    seen_buf = reader_tokens.SeenBuffer(lambda: 1_800_000_000.0)
    kit_calls = []

    class FakeKit:
        def post(self, path, body):
            kit_calls.append((path, body))
            return {"ok": True}

    r = web_reader.Reader(SimpleNamespace(kit_bridge=lambda: FakeKit()), None, seen=seen_buf)
    monkeypatch.setattr(r, "_hand_over", lambda: None)
    monkeypatch.setattr(reader_tokens, "offered_build", lambda: 42)
    reader_config.update(tid, {"buzzer_low": True, "scan_cooldown_ms": 1500})
    reader_config.queue_command(tid, "ota")

    app = Starlette(routes=r.routes())
    with TestClient(app, client=("127.0.0.1", 50000)) as c:
        resp = c.get("/reader/config", headers={"X-Auth-Token": tok, "X-Reader-Build": "39"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["buzzer_low"] is True and body["scan_cooldown_ms"] == 1500
        assert body["config_version"] == 1 and body["ota_now"] is True and body["reboot_now"] is False
        assert body["offered_build"] == 42

        # X-Reader-Build was recorded in SeenBuffer
        assert reader_tokens.flush(seen_buf, FakeKit()) is True
        assert kit_calls[0][0] == "/api/readers/web/seen"
        assert kit_calls[0][1]["readers"][0]["build"] == 39

        ack_bad = c.post("/reader/config/ack", headers={"X-Auth-Token": tok}, json={"cmd": "bad"})
        assert ack_bad.status_code == 400
        ack_ok = c.post("/reader/config/ack", headers={"X-Auth-Token": tok}, json={"cmd": "ota"})
        assert ack_ok.status_code == 200 and ack_ok.json() == {"ok": True, "cmd": "ota"}
        assert reader_config.pending(tid)["ota_now"] is False


# ---------- 4. setup_console/device_tab.py ----------

class _FakePage:
    def __init__(self):
        self.audits: list[str] = []

    def audit(self, request, message: str):
        self.audits.append(message)

    def guard(self, fn, write: bool = False):
        async def endpoint(request):
            raw = await request.body()
            body = json.loads(raw) if write and raw else {}
            try:
                return JSONResponse(await fn(request, body))
            except Refused as e:
                return JSONResponse({"error": e.message, **e.extra}, status_code=e.status)
        return endpoint


def test_device_tab_readers_merge_detail_settings_ota_and_restart(fk_env, monkeypatch):
    tid, _ = reader_tokens.TokenStore().issue("bench")
    reader_tokens.record_seen([{"id": tid, "build": 77, "seen_at": "2026-11-25T10:00:00+00:00"}])
    merged = device_tab._readers()
    assert len(merged) == 1 and merged[0]["id"] == tid and merged[0]["build"] == 77
    assert merged[0]["last_seen"] == "2026-11-25T10:00:00+00:00"

    monkeypatch.setattr(device_tab, "_pairings", lambda: [
        {"id": "st1", "name": "Weigh Station", "kind": "station", "version": "0.8.0"},
    ])
    monkeypatch.setattr(device_tab, "_station_for", lambda did: "https://127.0.0.1:5003")
    monkeypatch.setattr(device_tab.station_client, "station_reach", lambda devs, url: {"st1": True})
    monkeypatch.setattr(device_tab.station_client, "station_panel", lambda url: "usb")
    monkeypatch.setattr(device_tab.station_client, "station_flash_notice", lambda url: None)
    monkeypatch.setattr(device_tab.station_client, "station_endpoint", lambda url: ("127.0.0.1", 5003, "ab" * 32))
    monkeypatch.setattr(device_tab.station_client, "read_state",
                        lambda url: {"photo": False, "ota": {"state": "idle"}})
    monkeypatch.setattr(device_tab.station_client, "call_json",
                        lambda url, m, p: {"settings": [{"id": "beep", "label": "Beep", "kind": "switch", "value": True}]})
    ota_calls = []
    restart_calls = []
    monkeypatch.setattr(device_tab.station_client, "trigger_ota",
                        lambda url, ver=None: (ota_calls.append((url, ver)), {"ok": True, "state": "updating"})[1])
    monkeypatch.setattr(device_tab.station_client, "restart_station",
                        lambda url: (restart_calls.append(url), {"ok": True, "restarting": True})[1])
    monkeypatch.setattr(reader_tokens, "offered_build", lambda: 80)

    fp = _FakePage()
    app = Starlette(routes=[Route(p, h, methods=[m]) for p, h, m in device_tab.routes(fp)])
    with TestClient(app) as c:
        # Reader detail, settings update/reset, OTA, restart
        rd = c.get(f"/device/reader-{tid}")
        assert rd.status_code == 200
        rbody = rd.json()
        assert rbody["ota_capable"] is True and rbody["offered_build"] == 80
        assert rbody["pending_ota"] is False and rbody["pending_reboot"] is False

        rs = c.post(f"/device/reader-{tid}/settings", json={"buzzer_enabled": False, "dim_after_s": 90})
        assert rs.status_code == 200
        assert {s["id"]: s["value"] for s in rs.json()["settings"]}["dim_after_s"] == 90

        rs_reset = c.post(f"/device/reader-{tid}/settings", json={"reset": True})
        assert rs_reset.status_code == 200
        assert {s["id"]: s["value"] for s in rs_reset.json()["settings"]}["dim_after_s"] == 120

        ro = c.post(f"/device/reader-{tid}/ota", json={})
        assert ro.status_code == 200 and ro.json() == {"ok": True, "queued": "ota"}
        rr = c.post(f"/device/reader-{tid}/restart", json={})
        assert rr.status_code == 200 and rr.json() == {"ok": True, "queued": "reboot"}
        assert reader_config.pending(tid)["ota_now"] is True and reader_config.pending(tid)["reboot_now"] is True

        # Station detail, OTA, restart
        sd = c.get("/device/st1")
        assert sd.status_code == 200
        assert sd.json()["ota_capable"] is True and sd.json()["ota"] == {"state": "idle"}

        so = c.post("/device/st1/ota", json={"version": "0.9.0"})
        assert so.status_code == 200 and so.json() == {"ok": True, "state": "updating"}
        assert ota_calls == [("https://127.0.0.1:5003", "0.9.0")]

        sr = c.post("/device/st1/restart", json={})
        assert sr.status_code == 200 and sr.json() == {"ok": True, "restarting": True}
        assert restart_calls == ["https://127.0.0.1:5003"]
