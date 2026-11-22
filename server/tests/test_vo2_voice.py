"""VO2 (R88 section 2): the voice keys, handing Speak/Speaker/Speed/Volume to the Weigh Station, the logic of the kit
routes, and the page route for Test. The station is a stub: `station_client.call` is replaced by a recorder."""
from __future__ import annotations

import json
import socket

import anyio
import pytest
from test_console_general import app  # noqa: F401
from test_setup_page import client, env, post  # noqa: F401

from filament_kit import config, setup_helper, station_client
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


# ---- the keys ----

def test_the_keys_have_their_defaults():
    st = config.DEFAULTS["station"]
    assert st["speech"] is True and st["voice_speaker"] == 0 and st["voice_speed"] == 100 and st["speaker_volume"] == 50
    assert config.voice_values({}) == {"speech": True, "speaker": 0, "speed": 100, "volume": 50}


@pytest.mark.parametrize("st,expected", [
    ({"voice_speaker": 903}, {"speaker": 903}),
    ({"voice_speaker": 904}, {"speaker": 0}),
    ({"voice_speaker": -1}, {"speaker": 0}),
    ({"voice_speaker": True}, {"speaker": 0}),
    ({"voice_speed": 50}, {"speed": 50}),
    ({"voice_speed": 200}, {"speed": 200}),
    ({"voice_speed": 45}, {"speed": 100}),
    ({"voice_speed": 205}, {"speed": 100}),
    ({"voice_speed": 105}, {"speed": 100}),
    ({"speaker_volume": 0}, {"volume": 0}),
    ({"speaker_volume": 101}, {"volume": 50}),
    ({"speech": "yes"}, {"speech": True}),
    ({"speech": False}, {"speech": False}),
])
def test_stored_values_are_read_within_their_ranges(st, expected):
    assert {**config.voice_values({"station": st})} == {**config.voice_values({}), **expected}


@pytest.mark.parametrize("key,value", [
    ("station.voice_speaker", 904), ("station.voice_speaker", -1), ("station.voice_speaker", "3"),
    ("station.voice_speed", 49), ("station.voice_speed", 55), ("station.voice_speed", 210),
    ("station.speaker_volume", 101), ("station.speaker_volume", -1), ("station.speech", "on"),
])
def test_the_page_refuses_values_out_of_range(app, station, key, value):  # noqa: F811
    r = post(client(app), "settings/general", {"key": key, "value": value})
    assert r.status_code == 400 and station.calls == []


@pytest.mark.parametrize("key,value,read", [
    ("station.speech", False, lambda c: c["station"]["speech"] is False),
    ("station.voice_speaker", 903, lambda c: c["station"]["voice_speaker"] == 903),
    ("station.voice_speed", 150, lambda c: c["station"]["voice_speed"] == 150),
    ("station.speaker_volume", 0, lambda c: c["station"]["speaker_volume"] == 0),
])
def test_a_saved_value_goes_to_the_station_without_restart_or_apply(app, station, monkeypatch, key, value, read):  # noqa: F811
    monkeypatch.setattr(setup_helper, "_restart_kit", lambda: pytest.fail("restarted"))
    monkeypatch.setattr(setup_helper, "_start_apply", lambda checked=False: pytest.fail("applied"))
    cfg = config.load()
    cfg["station"]["url"] = "http://127.0.0.1:5003"
    config.save(cfg)
    r = post(client(app), "settings/general", {"key": key, "value": value})
    assert r.status_code == 200 and r.json()["restarts"] is False and r.json()["applies"] is False
    assert read(config.load())
    (url, method, path, body), = station.calls
    assert (method, path) == ("POST", "/voice") and set(body) == {"speech", "speaker", "speed", "volume"}


def test_a_group_save_hands_over_once(app, station):  # noqa: F811
    cfg = config.load()
    cfg["station"]["url"] = "http://127.0.0.1:5003"
    config.save(cfg)
    r = post(client(app), "settings/general", {"values": {"station.voice_speed": 80, "station.speaker_volume": 20}})
    assert r.status_code == 200
    assert len(station.calls) == 1 and station.calls[0][3]["speed"] == 80 and station.calls[0][3]["volume"] == 20


def test_the_voice_row_alone_hands_nothing_over(app, station, monkeypatch):  # noqa: F811
    monkeypatch.setattr(setup_helper, "_start_apply", lambda checked=False: None)
    assert post(client(app), "settings/general", {"key": "station.tts", "value": "espeak"}).status_code == 200
    assert station.calls == []


def test_a_silent_station_never_stops_a_save(app, monkeypatch):  # noqa: F811
    def dead(*a, **k):
        raise StationCallError(502, "the Weigh Station does not answer (ConnectionRefusedError)")
    monkeypatch.setattr(station_client, "call", dead)
    cfg = config.load()
    cfg["station"]["url"] = "http://127.0.0.1:5003"
    config.save(cfg)
    r = post(client(app), "settings/general", {"key": "station.voice_speed", "value": 70})
    assert r.status_code == 200 and config.load()["station"]["voice_speed"] == 70


def test_a_real_closed_port_is_a_quiet_false():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert voice_tab.hand_over(_cfg(f"http://127.0.0.1:{port}")) is False


def test_no_station_hands_nothing_over(station):
    assert voice_tab.hand_over(_cfg(url=None)) is False and station.calls == []


def test_a_refusal_of_the_station_is_a_false(station):
    station.answers["/voice"] = (400, {"error": "bad_value"})
    assert voice_tab.hand_over(_cfg()) is False


# ---- hello, local and remote ----

def test_the_hello_voice_holds_the_four_values():
    assert voice_tab.hello_voice(_cfg(voice_speaker=7, voice_speed=120, speaker_volume=30, speech=False)) == \
        {"speech": False, "speaker": 7, "speed": 120, "volume": 30}


def test_a_remote_station_gets_the_values_and_the_voice_row_says_where_it_is_chosen(station):
    remote = _cfg("http://station.example:5003", voice_speed=80)
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
    got = voice_tab.get_voice(_cfg(voice_speaker=5))
    assert got == {"station": True, "speech": True, "speaker": 5, "speed": 100, "volume": 50,
                   "voice": "Piper, many speakers", "speaker_range": [0, 903]}
    assert voice_tab.get_voice(_cfg(voice="en_US-ljspeech-medium"))["speaker_range"] is None
    assert voice_tab.get_voice(_cfg(tts="espeak"))["speaker_range"] is None


def test_patch_saves_hands_over_and_answers_as_get(station):
    cfg, saved = _cfg(), []
    got = voice_tab.patch_voice({"speed": 130, "speaker": 9}, load=lambda: cfg, save=saved.append)
    assert saved == [cfg] and cfg["station"]["voice_speed"] == 130 and cfg["station"]["voice_speaker"] == 9
    assert got["speed"] == 130 and got["speaker"] == 9 and got["station"] is True
    assert station.calls[0][2] == "/voice" and station.calls[0][3] == {"speaker": 9, "speed": 130}


@pytest.mark.parametrize("body,field", [({"speaker": 904}, "speaker"), ({"speed": 55}, "speed"),
                                        ({"volume": 101}, "volume"), ({"speech": 1}, "speech"),
                                        ({"speed": 100, "volume": -1}, "volume")])
def test_patch_refuses_a_bad_value_with_its_field_and_saves_nothing(station, body, field):
    saved = []
    with pytest.raises(voice_tab.VoiceError) as e:
        voice_tab.patch_voice(body, load=lambda: _cfg(), save=saved.append)
    assert e.value.status == 400 and e.value.field == field and e.value.code and e.value.message
    assert saved == [] and station.calls == []


def test_patch_with_a_silent_station_still_saves(monkeypatch):
    monkeypatch.setattr(station_client, "call", lambda *a, **k: (_ for _ in ()).throw(StationCallError(502, "x")))
    cfg, saved = _cfg(), []
    got = voice_tab.patch_voice({"volume": 10}, load=lambda: cfg, save=saved.append)
    assert saved == [cfg] and got["volume"] == 10


def test_patch_without_a_station_is_refused():
    with pytest.raises(voice_tab.VoiceError) as e:
        voice_tab.patch_voice({"volume": 10}, load=lambda: _cfg(url=None), save=lambda c: None)
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
    (path, fn, method), = voice_tab.routes(_Page())
    assert (path, method) == ("/station/voice/test", "POST")
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
