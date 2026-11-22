"""R88 voice routes, the web twins, hello's voice and the command line (the VO routes step).

The Weigh Station in these tests is a stub (`station_client.call` is replaced by a recorder)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from filament_kit import cli, config, pairing, station_client, voice_cli, voice_state, web_access
from filament_kit.api import create_app
from filament_kit.setup_console import voice_tab
from filament_kit.spoolman import Spoolman
from filament_kit.web import mcp_tools
from filament_kit.web.intake_app import IntakeApp
from filament_kit.web.slots import INTAKE_DEVICE, SlotError

KEYS = {"station", "remote", "speech", "voice", "speaker", "speaker_range", "speed", "volume"}


class StubStation:
    def __init__(self):
        self.calls = []
        self.answers = {}

    def __call__(self, url, method, path, body=None, cap=0, timeout=0):
        self.calls.append((url, method, path, json.loads(body) if body else None))
        ans = self.answers.get(path, (200, {}))
        if isinstance(ans, Exception):
            raise ans
        return ans[0], "application/json", json.dumps(ans[1]).encode()


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FK_ETC", str(tmp_path / "etc"))
    monkeypatch.setenv("FK_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("FK_NO_SYSTEMD", "1")
    (tmp_path / "etc").mkdir(parents=True, exist_ok=True)
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    pairing.make_cert("fkhost", [], tmp_path / "etc")
    cfg = json.loads(json.dumps(config.DEFAULTS))
    cfg["station"]["url"] = "http://127.0.0.1:5003"
    config.save(cfg)
    return tmp_path


@pytest.fixture
def station(monkeypatch):
    s = StubStation()
    monkeypatch.setattr(station_client, "call", s)
    return s


@pytest.fixture
def app(spoolman_url):
    sm = Spoolman(spoolman_url)
    sm.ensure_fields()
    return create_app(sm=sm)


def _auth(name="Phone", role=None, scope=None):
    code = pairing.issue(name, role=role, scope=scope).code
    return {"Authorization": "Bearer " + pairing.redeem_code(code)}


def test_station_hello_includes_voice_values(app):
    voice_state.save({"speech": False, "speaker": 7, "speed": 120, "volume": 80}, config.load())
    with TestClient(app) as c:
        r = c.get("/api/station/hello", headers=_auth("station", scope="station"))
    assert r.status_code == 200
    assert r.json() == {"ok": True, "voice": {"speech": False, "speaker": 7, "speed": 120, "volume": 80}}


def test_kit_voice_routes_without_a_station(app):
    cfg = config.load()
    cfg["station"]["url"] = None
    config.save(cfg)
    h = _auth("Phone")
    with TestClient(app) as c:
        g = c.get("/api/voice", headers=h)
        assert g.status_code == 200 and g.json() == {"station": False}
        p = c.patch("/api/voice", headers=h, json={"speed": 110})
        assert p.status_code == 409 and p.json()["error"] == "no_station"
        t = c.post("/api/voice/test", headers=h, json={})
        assert t.status_code == 409 and t.json()["error"] == "no_station"


def test_kit_voice_routes_caller_rules_and_refusals(app, station):
    phone = _auth("Phone")
    viewer = _auth("Viewer", role=pairing.VIEWER_ROLE)
    home_web = _auth(INTAKE_DEVICE, role=web_access.WEB_ROLE)
    pub_web = _auth("web (MCP)", role=web_access.WEB_ROLE)
    with TestClient(app) as c:
        assert c.get("/api/voice").status_code == 401                                  # no token
        for hdr in (phone, viewer, home_web, pub_web):                                  # everyone paired may read
            r = c.get("/api/voice", headers=hdr)
            assert r.status_code == 200 and set(r.json()) == KEYS
        for hdr in (viewer, pub_web):                                                   # not a viewer; the web unit only at home
            assert c.patch("/api/voice", headers=hdr, json={"speed": 110}).status_code == 403
            assert c.post("/api/voice/test", headers=hdr, json={}).status_code == 403
        for hdr in (phone, home_web):
            ok = c.patch("/api/voice", headers=hdr, json={"speed": 120, "volume": 65})
            assert ok.status_code == 200 and set(ok.json()) == KEYS
            assert ok.json()["speed"] == 120 and ok.json()["volume"] == 65
            assert c.post("/api/voice/test", headers=hdr, json={"speed": 120}).status_code == 200
        assert voice_state.values(config.load())["speed"] == 120                        # the service wrote voice.xml

        bad = c.patch("/api/voice", headers=phone, json={"speed": 125})
        assert bad.status_code == 400 and bad.json()["error"] == "bad_value" and bad.json()["field"] == "speed"
        assert voice_state.values(config.load())["speed"] == 120                        # nothing written for a bad value
        assert c.patch("/api/voice", headers=phone, content=b"[1]").status_code == 400

        for answer, code, status in (((409, {"error": "busy"}), "busy", 409), ((409, {"error": "off"}), "off", 409),
                                     (OSError("down"), "no_answer", 502)):
            station.answers["/voice/test"] = answer
            r = c.post("/api/voice/test", headers=phone, json={})
            assert r.status_code == status and r.json()["error"] == code


class RecordingKit:
    """The Hub as the web app's twins see it: records what they pass on; a refusal raises as KitApi raises it."""

    def __init__(self):
        self.calls, self.refuse = [], None

    def _call(self, method, path, body=None):
        self.calls.append((method, path, body))
        if self.refuse:
            raise self.refuse
        return {"station": True, "speed": 100} if method == "GET" else {}

    def get(self, path):
        return self._call("GET", path)

    def post(self, path, body):
        return self._call("POST", path, body)

    def patch(self, path, body):
        return self._call("PATCH", path, body)


def _intake(spoolman_url, kit, public_url=None):
    sm = Spoolman(spoolman_url)
    sm.ensure_fields()
    tools = mcp_tools.Tools(sm, kit_api=kit, kit_writes=True)
    return Starlette(routes=IntakeApp(tools, public_url=public_url, lan_cidrs=["192.168.1.0/24"], push=False,
                                      host_ok=lambda r: True, kit_api=kit).routes())


def test_the_web_twins_pass_the_three_routes_on_at_home(spoolman_url):
    kit = RecordingKit()
    origin = {"origin": "https://hub.local:8710"}
    with TestClient(_intake(spoolman_url, kit), base_url="https://hub.local:8710", client=("192.168.1.50", 1234)) as c:
        assert c.get("/intake/api/voice").json() == {"station": True, "speed": 100}
        assert c.patch("/intake/api/voice", json={"volume": 75}).status_code == 403          # a change needs the page's origin
        assert c.patch("/intake/api/voice", json={"volume": 75}, headers=origin).status_code == 200
        assert c.post("/intake/api/voice/test", json={"speed": 90}, headers=origin).status_code == 200
        kit.refuse = SlotError("speed must be a whole number from 50 to 200, in steps of 10.", code="bad_value",
                               http=400, field="speed")
        bad = c.patch("/intake/api/voice", json={"speed": 125}, headers=origin)
        assert bad.status_code == 400 and bad.json()["error"] == "bad_value" and bad.json()["field"] == "speed"
        kit.refuse = SlotError("The Weigh Station is in use. Try again when it is idle.", code="busy", http=409)
        busy = c.post("/intake/api/voice/test", json={}, headers=origin)
        assert busy.status_code == 409 and busy.json()["error"] == "busy"
    with TestClient(_intake(spoolman_url, RecordingKit()), base_url="https://hub.local:8710", client=("8.8.8.8", 1)) as away:
        assert away.get("/intake/api/voice").status_code == 403                                # not on the home network
    assert kit.calls == [("GET", "/api/voice", None), ("PATCH", "/api/voice", {"volume": 75}),
                         ("POST", "/api/voice/test", {"speed": 90}), ("PATCH", "/api/voice", {"speed": 125}),
                         ("POST", "/api/voice/test", {})]


def test_the_web_twins_refuse_a_change_on_the_public_address(spoolman_url):
    kit = RecordingKit()
    origin = {"origin": "https://hub.example"}
    with TestClient(_intake(spoolman_url, kit, "https://hub.example/filament"), base_url="https://hub.example",
                    client=("192.168.1.50", 1234)) as c:
        assert c.get("/intake/api/voice").status_code == 200                                  # reading is allowed
        for r in (c.patch("/intake/api/voice", json={"volume": 50}, headers=origin),
                  c.post("/intake/api/voice/test", json={}, headers=origin)):
            assert r.status_code == 403 and r.json()["message"] == "Change this at home."
    assert kit.calls == [("GET", "/api/voice", None)]                                         # nothing reached the Hub


class Service:
    """The Hub's service as the command line's `_server_api()` reaches it: the same voice_tab functions, a refusal raised
    as KitApi raises it."""

    def _run(self, fn):
        try:
            return fn()
        except voice_tab.VoiceError as e:
            raise SlotError(e.message, code=e.code, http=e.status, field=e.field) from None

    def get(self, path):
        assert path == "/api/voice"
        return voice_tab.get_voice(config.load())

    def patch(self, path, body):
        assert path == "/api/voice"
        return self._run(lambda: voice_tab.save_voice(body))

    def post(self, path, body):
        assert path == "/api/voice/test"
        return self._run(lambda: voice_tab.play_test(config.load(), body)) or {}


def test_weigh_station_voice_test_goes_through_the_service(capsys, monkeypatch, station):
    monkeypatch.setattr(cli, "_server_api", lambda: Service())
    assert cli.main(["weigh-station", "voice-test"]) == 0
    assert "Played on the Weigh Station." in capsys.readouterr().out
    assert [c[2] for c in station.calls] == ["/voice/test"]
    for answer, text in (((409, {"error": "off"}), "Speaking is switched off."),
                         ((409, {"error": "busy"}), "in use"), (OSError("down"), "did not answer")):
        station.answers["/voice/test"] = answer
        assert cli.main(["weigh-station", "voice-test"]) == 1
        assert text in capsys.readouterr().err


def test_weigh_station_voice_test_without_a_service(capsys, monkeypatch, station):
    monkeypatch.setattr(cli, "_server_api", lambda: None)
    assert cli.main(["weigh-station", "voice-test"]) == 1
    assert "not running" in capsys.readouterr().err
    assert station.calls == []


def test_the_command_line_never_writes_voice_xml_itself(monkeypatch, station):
    monkeypatch.setattr(cli, "_server_api", lambda: Service())
    calls = []
    real = voice_state.save
    monkeypatch.setattr(voice_state, "save", lambda *a, **k: calls.append(1) or real(*a, **k))
    assert cli.main(["weigh-station", "voice-test"]) == 0
    assert calls == []                                                   # a test writes nothing
    for name in ("cli.py", "voice_cli.py"):
        src = (Path(cli.__file__).parent / name).read_text(encoding="utf-8")
        assert "voice_state" not in src and "voice_tab" not in src and "save_voice" not in src


def test_the_config_voice_items_read_and_save_through_the_service(monkeypatch, station):
    monkeypatch.setattr(cli, "_server_api", lambda: Service())
    menu = cli._ConfigMenu(config.load(), None)
    got = menu.voice_view()
    assert got["station"] is True and got["speaker_range"] == [0, 903]
    assert menu.stored(cli._ITEMS["voice_speed"]) == 100
    voice_cli.save(cli._server_api, {"speech": False, "speaker": 12, "speed": 130, "volume": 80})
    assert voice_state.values(config.load()) == {"speech": False, "speaker": 12, "speed": 130, "volume": 80}
    assert station.calls[-1][2] == "/voice"                              # the service handed the values on
    with pytest.raises(voice_cli.VoiceRefused, match="speed must be"):
        voice_cli.check("speed", 125, got)
    with pytest.raises(voice_cli.VoiceRefused, match="speed must be"):
        voice_cli.save(cli._server_api, {"speed": 125})
    monkeypatch.setattr(cli, "_server_api", lambda: None)
    assert cli._ConfigMenu(config.load(), None).voice_view() is None     # no service: the voice items are left out
