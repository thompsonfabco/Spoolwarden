"""VO2: the voice keys, handing Speak/Speaker/Speed/Volume to the Weigh Station, the logic of the kit
routes, and the page route for Test. The station is a stub: `station_client.call` is replaced by a recorder."""
from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import anyio
import pytest
from test_console_general import app  # noqa: F401
from test_setup_page import client, env, post  # noqa: F401

from filament_kit import config, setup_helper, station_client, voice_state
from filament_kit.profiles import statexml
from filament_kit.setup_console import voice_tab
from filament_kit.setup_page import Refused
from filament_kit.station_client import StationCallError


class Station:
    """A stub Weigh Station: records the calls, answers as set."""

    def __init__(self):
        self.calls, self.answers = [], {}

    def __call__(self, url, method, path, body=None, cap=0, timeout=0):
        self.calls.append((url, method, path, json.loads(body) if body else None))
        a = self.answers.get(path, (200, {}))
        if isinstance(a, Exception):
            raise a
        return a[0], "application/json", json.dumps(a[1]).encode()


@pytest.fixture
def station(monkeypatch):
    s = Station()
    monkeypatch.setattr(station_client, "call", s)
    return s


def _cfg(url="http://127.0.0.1:5003", **st):
    cfg = config.load() if False else json.loads(json.dumps(config.DEFAULTS))
    cfg["station"]["url"] = url
    cfg["station"].update(st)
    return cfg


# ---- voice.xml (voice_state) ----

def _file():
    return config.paths().state / "voice.xml"


def test_the_withdrawn_keys_are_not_settings():
    assert "voice_speaker" not in config.DEFAULTS["station"] and "voice_speed" not in config.DEFAULTS["station"]
    for k in ("station.voice_speaker", "station.voice_speed", "station.speech", "station.speaker_volume"):
        assert k not in setup_helper.GENERAL
    assert not hasattr(setup_helper, "_hand_voice") and not hasattr(config, "voice_values")


def test_without_a_file_the_values_are_the_fallbacks():
    assert voice_state.values({}) == {"speech": True, "speaker": 0, "speed": 100, "volume": 50}
    assert voice_state.values(_cfg(speech=False, speaker_volume=20)) ==         {"speech": False, "speaker": 0, "speed": 100, "volume": 20}
    assert voice_state.values(_cfg(speech="yes", speaker_volume=101)) ==         {"speech": True, "speaker": 0, "speed": 100, "volume": 50}


def test_the_file_overrides_the_settings_value_by_value():
    cfg = _cfg(speech=False, speaker_volume=20)
    assert voice_state.save({"speed": 130, "volume": 70}, cfg) is True
    assert voice_state.values(cfg) == {"speech": False, "speaker": 0, "speed": 130, "volume": 70}
    assert voice_state.save({"speech": True, "speaker": 9}, cfg) is True        # the other values are kept
    assert voice_state.values(cfg) == {"speech": True, "speaker": 9, "speed": 130, "volume": 70}


@pytest.mark.parametrize("raw", [b"", b"not xml at all", b"<other/>", b"<voice speed='1", b"\xff\xfe",
                                 b'<!DOCTYPE v [<!ENTITY a "b">]><voice speed="&a;"/>', b"<voice " + b"x" * 70000])
def test_a_broken_file_reads_as_the_defaults(raw):
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_bytes(raw)
    assert voice_state.values(_cfg()) == voice_state.fallbacks(_cfg())


@pytest.mark.parametrize("attrs,expected", [
    ('speed="45"', {"speed": 100}), ('speed="105"', {"speed": 100}), ('speed="210"', {"speed": 100}),
    ('speed="50"', {"speed": 50}), ('speed="200"', {"speed": 200}), ('speed="fast"', {"speed": 100}),
    ('speaker="904"', {"speaker": 0}), ('speaker="903"', {"speaker": 903}), ('speaker="-1"', {"speaker": 0}),
    ('volume="101"', {"volume": 50}), ('volume="0"', {"volume": 0}), ('volume="1.5"', {"volume": 50}),
    ('speech="yes"', {"speech": True}), ('speech="false"', {"speech": False}),
    ('speed="45" volume="30"', {"speed": 100, "volume": 30}),        # one bad value does not spoil the others
])
def test_a_value_out_of_range_in_the_file_reads_as_the_default(attrs, expected):
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().write_text(f'<voice version="1" {attrs}/>')
    assert voice_state.values(_cfg()) == {**voice_state.fallbacks(_cfg()), **expected}


def test_a_speaker_beyond_the_chosen_voice_reads_as_zero():
    voice_state.save({"speaker": 500}, _cfg())
    assert voice_state.values(_cfg())["speaker"] == 500
    assert voice_state.values(_cfg(voice="en_US-ljspeech-medium"))["speaker"] == 0
    assert voice_state.values(_cfg(tts="espeak"))["speaker"] == 0


@pytest.mark.parametrize("body,field", [({"speaker": 904}, "speaker"), ({"speed": 55}, "speed"), ({"speed": 210}, "speed"),
                                        ({"volume": 101}, "volume"), ({"volume": True}, "volume"),
                                        ({"speech": 1}, "speech"), ({"speaker": "3"}, "speaker")])
def test_check_refuses_what_is_out_of_range(body, field):
    with pytest.raises(voice_state.BadValue) as e:
        voice_state.check(body, _cfg())
    assert e.value.field == field


def test_check_refuses_a_speaker_the_voice_does_not_have():
    with pytest.raises(voice_state.BadValue):
        voice_state.check({"speaker": 1}, _cfg(voice="en_US-ljspeech-medium"))
    assert voice_state.check({"speaker": 0}, _cfg(tts="espeak")) == {"speaker": 0}


def test_the_write_is_atomic_state_owned_and_leaves_no_temporary_file(monkeypatch):
    calls = []
    real = voice_state.pairing._write_atomic
    monkeypatch.setattr(voice_state.pairing, "_write_atomic", lambda *a, **k: (calls.append((a, k)), real(*a, **k))[1])
    assert voice_state.save({"speed": 80}, _cfg()) is True
    (args, kw), = calls
    assert args[0] == _file() and args[2] == 0o644 and kw == {"state_owned": True}
    assert [p.name for p in _file().parent.iterdir() if p.name.endswith(".tmp")] == []
    assert 'speed="80"' in _file().read_text() and _file().read_text().startswith("<?xml")


def test_a_failed_write_changes_nothing_and_never_raises(monkeypatch):
    voice_state.save({"speed": 80}, _cfg())
    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(voice_state.pairing, "_write_atomic", boom)
    assert voice_state.save({"speed": 90}, _cfg()) is False
    assert voice_state.values(_cfg())["speed"] == 80


@pytest.mark.skipif(not hasattr(os, "symlink") or os.name == "nt", reason="symbolic links")
def test_a_symbolic_link_is_never_followed(tmp_path):
    target = tmp_path / "elsewhere.xml"
    target.write_text('<voice speed="150"/>')
    _file().parent.mkdir(parents=True, exist_ok=True)
    _file().symlink_to(target)
    assert voice_state.values(_cfg())["speed"] == 100
    assert voice_state.save({"speed": 80}, _cfg()) is False and target.read_text() == '<voice speed="150"/>'


def test_apply_the_helper_the_installer_and_the_updater_never_read_it():
    root = Path(voice_state.__file__).parent
    for name in ("apply.py", "setup_helper.py", "updater.py", "update.py", "backup.py"):
        if (root / name).exists():
            text = (root / name).read_text(encoding="utf-8")
            assert "voice.xml" not in text and "voice_state" not in text, name


def test_the_state_backup_takes_the_file_with_the_state_folder():
    from filament_kit import backup
    state = dict(backup.roots())["var/lib/filament-kit"]
    assert state == config.paths().state and _file().parent == state


# ---- hello, local and remote ----

def test_the_hello_voice_holds_the_four_values():
    voice_state.save({"speaker": 7, "speed": 120}, _cfg())
    assert voice_tab.hello_voice(_cfg(speaker_volume=30, speech=False)) == \
        {"speech": False, "speaker": 7, "speed": 120, "volume": 30}


def test_the_settings_alone_never_carry_speaker_or_speed():
    assert voice_tab.hello_voice(_cfg(voice_speaker=7, voice_speed=120)) == {"speech": True, "speaker": 0, "speed": 100, "volume": 50}


def test_a_remote_station_gets_the_values_and_the_voice_row_says_where_it_is_chosen(station):
    remote = _cfg("http://station.example:5003")
    voice_state.save({"speed": 80}, remote)
    assert voice_tab.hand_over(remote) is True and station.calls[0][0] == "http://station.example:5003"
    assert station.calls[0][3]["speed"] == 80
    row = voice_tab.voice_row(remote)
    assert row["editable"] is False and row["text"] == "Chosen on the Weigh Station's own computer: spoolwarden config."


@pytest.mark.parametrize("url", ["http://127.0.0.1:5003", "http://localhost:5003", "https://[::1]:5003",
                                 "http://Hubhost:5003"])
def test_a_local_station_keeps_its_voice_row(url, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "hubhost")
    row = voice_tab.voice_row(_cfg(url))
    assert row["editable"] is True and row["text"] is None and row["value"] == "Piper, many speakers"


@pytest.mark.parametrize("st,name", [({}, "Piper, many speakers"),
                                     ({"voice": "en_US-ljspeech-medium"}, "Piper, one clear speaker"),
                                     ({"tts": "espeak"}, "espeak-ng, robotic, no download")])
def test_the_voice_is_shown_by_name(st, name):
    assert voice_tab.voice_name(_cfg(**st)) == name


# ---- the kit routes' logic ----

def test_get_voice_without_a_station():
    assert voice_tab.get_voice(_cfg(url=None)) == {"station": False}


def test_get_voice_with_a_station():
    voice_state.save({"speaker": 5}, _cfg())
    got = voice_tab.get_voice(_cfg())
    assert got == {"station": True, "speech": True, "speaker": 5, "speed": 100, "volume": 50,
                   "voice": "Piper, many speakers", "remote": False,
                   "speaker_range": [0, 903]}
    assert voice_tab.get_voice(_cfg(voice="en_US-ljspeech-medium"))["speaker_range"] is None
    assert voice_tab.get_voice(_cfg(tts="espeak"))["speaker_range"] is None


def test_patch_saves_hands_over_and_answers_as_get(station):
    cfg = _cfg()
    got = voice_tab.patch_voice({"speed": 130, "speaker": 9}, load=lambda: cfg)
    assert voice_state.stored(cfg) == {"speed": 130, "speaker": 9} and "voice_speed" not in cfg["station"]
    assert got["speed"] == 130 and got["speaker"] == 9 and got["station"] is True
    assert station.calls[0][2] == "/voice" and station.calls[0][3] == {"speaker": 9, "speed": 130}


@pytest.mark.parametrize("body,field", [({"speaker": 904}, "speaker"), ({"speed": 55}, "speed"),
                                        ({"volume": 101}, "volume"), ({"speech": 1}, "speech"),
                                        ({"speed": 100, "volume": -1}, "volume")])
def test_patch_refuses_a_bad_value_with_its_field_and_saves_nothing(station, body, field):
    with pytest.raises(voice_tab.VoiceError) as e:
        voice_tab.patch_voice(body, load=lambda: _cfg())
    assert e.value.status == 400 and e.value.field == field and e.value.code and e.value.message
    assert not _file().exists() and station.calls == []


def test_patch_with_a_silent_station_still_saves(monkeypatch):
    monkeypatch.setattr(station_client, "call", lambda *a, **k: (_ for _ in ()).throw(StationCallError(502, "x")))
    got = voice_tab.patch_voice({"volume": 10}, load=lambda: _cfg())
    assert voice_state.stored(_cfg()) == {"volume": 10} and got["volume"] == 10


def test_patch_when_the_file_cannot_be_written_is_a_500_and_tells_the_station_nothing(station, monkeypatch):
    monkeypatch.setattr(voice_state, "save", lambda *a, **k: False)
    with pytest.raises(voice_tab.VoiceError) as e:
        voice_tab.patch_voice({"volume": 10}, load=lambda: _cfg())
    assert (e.value.status, e.value.code) == (500, "not_saved") and station.calls == []


def test_patch_needs_no_write_access_to_the_settings(station, monkeypatch):
    monkeypatch.setattr(config, "save", lambda *a, **k: pytest.fail("the settings were written"))
    voice_tab.patch_voice({"volume": 10}, load=lambda: _cfg())


def test_patch_without_a_station_is_refused():
    with pytest.raises(voice_tab.VoiceError) as e:
        voice_tab.patch_voice({"volume": 10}, load=lambda: _cfg(url=None))
    assert e.value.status == 409


# ---- Test ----

def test_test_sends_only_the_given_values_and_never_a_text(station):
    voice_tab.play_test(_cfg(), {"speaker": 3, "text": "say this", "speech": False})
    assert station.calls == [("http://127.0.0.1:5003", "POST", "/voice/test", {"speaker": 3})]


@pytest.mark.parametrize("answer,status,code,text", [
    ((409, {"error": "busy"}), 409, "busy", "The Weigh Station is in use. Try again when it is idle."),
    ((409, {"error": "off"}), 409, "off", "Speaking is switched off."),
    ((500, {}), 502, "no_answer", "The Weigh Station did not answer."),
    (StationCallError(502, "x"), 502, "no_answer", "The Weigh Station did not answer."),
])
def test_the_stations_answers_become_the_refusals(station, answer, status, code, text):
    station.answers["/voice/test"] = answer
    with pytest.raises(voice_tab.VoiceError) as e:
        voice_tab.play_test(_cfg(), {})
    assert (e.value.status, e.value.code, e.value.message) == (status, code, text)


def test_test_with_a_bad_value_is_a_400_and_asks_nothing(station):
    with pytest.raises(voice_tab.VoiceError) as e:
        voice_tab.play_test(_cfg(), {"speed": 5})
    assert e.value.status == 400 and e.value.field == "speed" and station.calls == []


def test_the_texts_are_the_ruled_ones():
    assert voice_tab.TEXT_VOICE_PLAYED == "Played on the Weigh Station."
    assert voice_tab.TEXT_VOICE_APPS_READONLY == "Change the voice on the Hub page."


class _Page:
    def guard(self, fn, write=False):
        return fn


def _route():
    (fn, method), = [(f, m) for p, f, m in voice_tab.routes(_Page()) if p == "/station/voice/test"]
    assert method == "POST"
    return fn


def test_the_page_route_answers_empty_when_it_played(station, env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "load", lambda: _cfg())
    assert anyio.run(_route(), None, {"speaker": 2, "speed": 90, "volume": 40}) == {}
    assert station.calls[0][3] == {"speaker": 2, "speed": 90, "volume": 40}


@pytest.mark.parametrize("answer,status,text,code", [
    ((409, {"error": "busy"}), 409, "The Weigh Station is in use. Try again when it is idle.", "busy"),
    ((409, {"error": "off"}), 409, "Speaking is switched off.", "off"),
    (StationCallError(502, "x"), 502, "The Weigh Station did not answer.", "no_answer"),
])
def test_the_page_route_refuses_with_the_sentence(station, env, monkeypatch, answer, status, text, code):  # noqa: F811
    monkeypatch.setattr(config, "load", lambda: _cfg())
    station.answers["/voice/test"] = answer
    with pytest.raises(Refused) as e:
        anyio.run(_route(), None, {})
    assert (e.value.status, e.value.message, e.value.extra) == (status, text, {"code": code})


# ---- the page route that saves ----

def _save_route():
    fn, = [f for p, f, m in voice_tab.routes(_Page()) if p == "/station/voice" and m == "POST"]
    return fn


def test_the_page_route_saves_to_the_file_and_hands_over(station, env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "load", lambda: _cfg())
    got = anyio.run(_save_route(), None, {"speed": 120, "volume": 30})
    assert got["speed"] == 120 and got["volume"] == 30 and voice_state.stored(_cfg()) == {"speed": 120, "volume": 30}
    assert [(c[1], c[2], c[3]) for c in station.calls] == [("POST", "/voice", {"speed": 120, "volume": 30})]


def test_the_page_route_refuses_a_bad_value_with_sentence_code_and_field(station, env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "load", lambda: _cfg())
    with pytest.raises(Refused) as e:
        anyio.run(_save_route(), None, {"speed": 55})
    assert e.value.status == 400 and e.value.extra == {"code": "bad_value", "field": "speed"} and "speed" in e.value.message
    assert not _file().exists() and station.calls == []


def test_the_group_no_longer_writes_the_voice_settings_through_the_helper(app):  # noqa: F811
    for key, value in (("station.speech", False), ("station.speaker_volume", 10), ("station.voice_speed", 80),
                       ("station.voice_speaker", 3)):
        assert post(client(app), "settings/general", {"key": key, "value": value}).status_code == 400
    assert "voice_speed" not in config.load()["station"]


def test_the_page_route_works_with_a_silent_station(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "load", lambda: _cfg())
    monkeypatch.setattr(station_client, "call", lambda *a, **k: (_ for _ in ()).throw(StationCallError(502, "x")))
    assert anyio.run(_save_route(), None, {"speed": 70})["speed"] == 70


@pytest.mark.parametrize("host", ["Hubhost.local", "hubhost"])
def test_a_station_named_by_the_hubs_lan_name_or_address_is_not_remote(host, monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "hubhost")
    monkeypatch.setattr(socket, "getfqdn", lambda: "hubhost.lan")
    assert voice_tab.station_remote(_cfg(f"http://{host}:5003")) is False
    monkeypatch.setattr(socket, "gethostbyname_ex", lambda n: (n, [], ["192.168.1.20"]))
    assert voice_tab.station_remote(_cfg("http://192.168.1.20:5003")) is False
    assert voice_tab.station_remote(_cfg("http://192.168.1.21:5003")) is True


# ---- rework 2 ----

def test_a_broken_file_is_kept_aside_and_never_overwritten():
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"<voice speed=1")
    assert voice_state.save({"speed": 80}, _cfg()) is True
    assert (f.parent / "voice.xml.unreadable").read_bytes() == b"<voice speed=1"
    assert voice_state.stored(_cfg()) == {"speed": 80}
    assert any("voice.xml" in p for p in statexml.problems())


# ---- a passing read error is not a broken file (R626/R629) ----

def test_a_transient_read_error_leaves_a_good_file_in_place(monkeypatch):
    voice_state.save({"speed": 80}, _cfg())
    f = _file()
    good = f.read_bytes()
    real = voice_state._read

    def blip():
        raise PermissionError(13, "a passing blip")
    monkeypatch.setattr(voice_state, "_read", blip)
    assert voice_state.stored(_cfg()) == {}                      # no value for that read
    assert voice_state.save({"volume": 10}, _cfg()) is False     # nothing written over the good file
    assert f.read_bytes() == good and not (f.parent / "voice.xml.unreadable").exists()
    monkeypatch.setattr(voice_state, "_read", real)
    assert voice_state.stored(_cfg()) == {"speed": 80}           # the next read succeeds


def test_a_schema_broken_file_is_still_kept_aside():
    f = _file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b'<other speed="80"/>')
    assert voice_state.save({"speed": 90}, _cfg()) is True
    assert (f.parent / "voice.xml.unreadable").read_bytes() == b'<other speed="80"/>'
    assert voice_state.stored(_cfg()) == {"speed": 90}


def test_the_real_router_mounts_the_voice_routes_and_writes_voice_xml(app, station):  # noqa: F811
    cfg = config.load()
    cfg["station"]["url"] = "http://127.0.0.1:5003"
    config.save(cfg)
    r = post(client(app), "station/voice", {"speed": 120, "volume": 30})
    assert r.status_code == 200 and r.json()["speed"] == 120
    assert _file().exists() and voice_state.stored(config.load()) == {"speed": 120, "volume": 30}
    assert [(c[1], c[2]) for c in station.calls] == [("POST", "/voice")]
    assert post(client(app), "station/voice", {"speed": 55}).status_code == 400
    assert post(client(app), "station/voice/test", {}).status_code == 200
    assert post(client(app), "station/voice", {"speed": 120}, code="wrong").status_code in (401, 403)


# ---- the kit routes: the service's entry for the apps and the command line ----

def _kit_client(authed=lambda fn: fn):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient
    return TestClient(Starlette(routes=voice_tab.kit_routes(authed)))


def test_the_kit_routes_save_in_the_service_and_refuse_with_code_message_field(station, env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "load", lambda: _cfg())
    c = _kit_client()
    r = c.patch("/api/voice", json={"speed": 90, "volume": 20})
    assert r.status_code == 200 and r.json()["speed"] == 90 and voice_state.stored(_cfg()) == {"speed": 90, "volume": 20}
    assert c.get("/api/voice").json()["volume"] == 20
    bad = c.patch("/api/voice", json={"speed": 55})
    assert bad.status_code == 400 and bad.json()["error"] == "bad_value" and bad.json()["field"] == "speed"
    assert c.patch("/api/voice", content=b"[").status_code == 400
    assert c.post("/api/voice/test", json={}).status_code == 200


def test_the_kit_routes_go_through_the_callers_auth(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "load", lambda: _cfg())
    c = _kit_client(lambda fn: _no)
    assert c.patch("/api/voice", json={"speed": 90}).status_code == 401 and not _file().exists()


async def _no(request):
    from starlette.responses import JSONResponse
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def test_the_root_side_never_touches_voice_xml_it_asks_the_service():
    server = Path(voice_state.__file__).parent
    for p in (server / "cli.py", server / "setup_helper.py", server / "apply.py", server / "updates.py",
              server.parent.parent / "install" / "install.sh"):
        text = p.read_text(encoding="utf-8")
        for word in ("voice.xml", "voice_state", "voice_tab", "save_voice", "patch_voice"):
            assert word not in text, (p.name, word)


# ---- the sender attaches the pairing proof (R594 ruling) ----

@pytest.fixture
def wire(monkeypatch):
    """The real `call` path with the network replaced: records the headers and the body of each call."""
    from filament_kit.station import page
    seen = []
    key = page.token_key("a station token 0123456789")
    monkeypatch.setattr(station_client, "station_endpoint", lambda url: ("127.0.0.1", 5003, "ab" * 32))
    monkeypatch.setattr(station_client, "proof_key", lambda *, pinned=True: key)       # the real signature (D11)

    def bounded(host, port, fp, method, path, body=None, headers=None, cap=0, timeout=0, **kw):
        seen.append((path, dict(headers or {}), body))
        return 200, "application/json", b"{}"
    monkeypatch.setattr(station_client, "bounded_call", bounded)
    return seen, key, monkeypatch


@pytest.mark.parametrize("send", [lambda: station_client.send_voice("http://h", {"speed": 90}),
                                  lambda: station_client.voice_test("http://h", {"speed": 90})])
def test_the_voice_calls_carry_a_proof_the_station_accepts(wire, send):
    from filament_kit.station import page
    seen, key, _ = wire
    send()
    path, headers, body = seen[0]
    access = page.Access(lambda: key)
    assert access.proof_ok("voice", headers.get(page.PROOF_HEADER), body)


@pytest.mark.parametrize("send", [lambda: station_client.send_voice("http://h", {"speed": 90}),
                                  lambda: station_client.voice_test("http://h", {"speed": 90})])
def test_the_voice_calls_need_the_proof_and_go_plain_only_to_a_station_with_no_certificate(wire, send):
    """A voice call needs the pairing proof, as the station's /settings does. Integration: with the wave's
    rule, a station that told the Hub no certificate is called over plain HTTP for a change, carrying
    the proof; so without a key nothing is sent at all, and without a certificate it is sent plain WITH the proof."""
    from filament_kit.station import page
    seen, key, mp = wire
    mp.setattr(station_client, "proof_key", lambda *, pinned=True: None)
    with pytest.raises(StationCallError):
        send()
    assert seen == []                                                  # no proof key: nothing leaves
    mp.setattr(station_client, "proof_key", lambda *, pinned=True: key)
    mp.setattr(station_client, "station_endpoint", lambda url: ("127.0.0.1", 5003, None))
    send()
    path, headers, body = seen[0]
    assert page.PROOF_HEADER in headers and page.Access(lambda: key).proof_ok("voice", headers[page.PROOF_HEADER], body)


def test_a_proof_made_for_other_values_or_another_key_is_not_good(wire):
    from filament_kit.station import page
    seen, key, _ = wire
    station_client.send_voice("http://h", {"speed": 90})
    _, headers, body = seen[0]
    assert not page.Access(lambda: key).proof_ok("voice", headers[page.PROOF_HEADER], b'{"speed": 200}')
    assert not page.Access(lambda: page.token_key("another token 0123456789")).proof_ok(
        "voice", headers[page.PROOF_HEADER], body)


@pytest.mark.parametrize("send", [lambda: station_client.send_voice("http://h", {"speed": 90}),
                                  lambda: station_client.voice_test("http://h", {"speed": 90})])
def test_the_sender_proof_passes_the_station_check_of_both_voice_routes(wire, send):
    """Cross-check with the station's own verifier (station.page.Access.proof_ok). The station's routes on branch
    vo1-station-voice (server/filament_kit/station/http.py:203, 10f2386c) accept a /voice or /voice/test call only when
    `caller == guard.LISTED and proof is not None and access.proof_ok("voice", proof, raw)`, `raw` being the exact body
    bytes; the same call is made here with the sender's proof. A proof of purpose "settings" is not good there."""
    from filament_kit.station import page
    seen, key, _ = wire
    send()
    path, headers, body = seen[0]
    proof = headers.get(page.PROOF_HEADER)
    assert proof is not None
    assert not page.Access(lambda: key).proof_ok("settings", proof, body)
    assert page.Access(lambda: key).proof_ok("voice", proof, body)


# ---- the read answer the Hub page's group and the apps use ----

def test_the_read_answer_has_the_four_values_remote_and_voice(monkeypatch):
    monkeypatch.setattr(socket, "gethostname", lambda: "hubhost")
    monkeypatch.setattr(socket, "getfqdn", lambda: "hubhost.lan")
    monkeypatch.setattr(socket, "gethostbyname_ex", lambda n: (n, [], ["192.168.1.20"]))
    local = voice_tab.get_voice(_cfg("http://127.0.0.1:5003"))
    assert local["remote"] is False and local["voice"] == voice_tab.VOICE_MANY
    assert {"speech", "speaker", "speed", "volume"} <= set(local)
    far = voice_tab.get_voice(_cfg("http://192.168.1.99:5003", tts="espeak"))
    assert far["remote"] is True and far["voice"] == voice_tab.VOICE_ESPEAK
    assert voice_tab.get_voice(_cfg(""))  == {"station": False}


def test_the_page_route_with_no_values_reads_the_answer(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(config, "load", lambda: _cfg())
    got = anyio.run(_save_route(), None, {})
    assert got["remote"] is False and got["voice"] == voice_tab.VOICE_MANY and got["speed"] == 100
    assert not _file().exists()


ANSWER_KEYS = {"station", "remote", "speech", "voice", "speaker", "speaker_range", "speed", "volume"}


def test_the_view_read_answers_without_a_code_with_the_six_fields(app, station):  # noqa: F811
    cfg = config.load()
    cfg["station"]["url"] = "http://127.0.0.1:5003"
    config.save(cfg)
    r = client(app).get("/setup/api/station/voice")
    assert r.status_code == 200
    assert set(r.json()) == ANSWER_KEYS
    assert r.json()["remote"] is False and r.json()["voice"] == voice_tab.VOICE_MANY and r.json()["speaker_range"] == [0, 903]
    assert not _file().exists() and station.calls == []


def test_a_change_through_post_still_needs_the_code(app, station):  # noqa: F811
    cfg = config.load()
    cfg["station"]["url"] = "http://127.0.0.1:5003"
    config.save(cfg)
    assert post(client(app), "station/voice", {"speed": 120}, code=None).status_code in (401, 403)
    assert not _file().exists() and station.calls == []


def test_every_answer_has_the_one_shape(app, station):  # noqa: F811
    cfg = config.load()
    cfg["station"]["url"] = "http://127.0.0.1:5003"
    config.save(cfg)
    assert set(client(app).get("/setup/api/station/voice").json()) == ANSWER_KEYS          # G, no code
    assert set(post(client(app), "station/voice", {}).json()) == ANSWER_KEYS               # P, read form
    assert set(post(client(app), "station/voice", {"speed": 120}).json()) == ANSWER_KEYS   # P, after a change
    assert set(voice_tab.patch_voice({"speed": 130}, load=config.load)) == ANSWER_KEYS     # PATCH /api/voice
    kit = _kit_client()
    assert set(kit.get("/api/voice").json()) == ANSWER_KEYS
    assert set(kit.patch("/api/voice", json={"volume": 20}).json()) == ANSWER_KEYS


def test_the_speaker_range_is_null_for_a_one_speaker_voice_and_the_station_false_answer():
    assert voice_tab.get_voice(_cfg(voice="en_US-ljspeech-medium"))["speaker_range"] is None
    assert voice_tab.get_voice(_cfg())["speaker_range"] == [0, 903]
    assert voice_tab.get_voice(_cfg(url=None)) == {"station": False}
