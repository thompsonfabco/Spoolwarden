"""the Weigh Station's voice settings. Speaker and speed in both engines (piper-tts 1.8.0 `SynthesisConfig`
length scale 100 / speed and speaker id; espeak-ng words per minute 165 x speed / 100), the live values over the drop-in's,
`POST /voice`, `POST /voice/test` and the `voice` of the hello answer (station/speech.py, state.py, http.py, server_api.py)."""
from __future__ import annotations

import io
import json
import sys
import threading
import types
import wave

import pytest

from filament_kit.station import server_api, speech, wire
from filament_kit.station import state as st
from test_ev3_station_page import Station  # noqa: F401  (the same real listener)


class Done:
    """What subprocess.run returns."""
    def __init__(self, out=b"", code=0):
        self.stdout, self.returncode, self.stderr = out, code, b""


class Config:
    def __init__(self, num_speakers):
        self.num_speakers = num_speakers


class SynthesisConfig:
    def __init__(self, speaker_id=None, length_scale=None):
        self.speaker_id, self.length_scale = speaker_id, length_scale


class FakePiper:
    """A Piper voice that keeps the settings of each call."""
    num_speakers = 904
    calls: list = []

    @classmethod
    def load(cls, path):
        return cls()

    def __init__(self):
        self.config = Config(self.num_speakers)

    def synthesize_wav(self, text, w, syn_config=None):
        FakePiper.calls.append((text, syn_config))
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x00\x01" * 10)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """No live values, speech on, the station idle, nothing queued, nothing sent to a panel."""
    st.live.clear()
    monkeypatch.setitem(st.settings, "speech", True)
    monkeypatch.setitem(st.settings, "voice_speaker", 0)
    monkeypatch.setitem(st.settings, "voice_speed", 100)
    monkeypatch.setitem(st.settings, "speaker_volume", 50)
    monkeypatch.setitem(st.flow, "step", "idle")
    monkeypatch.setitem(st.ports, "panel_chip", None)
    monkeypatch.setitem(st.panel, "ser", None)
    speech.speech_ready.clear()
    speech.speech.update(text=None, opts=None)
    FakePiper.calls = []
    FakePiper.num_speakers = 904
    yield
    st.live.clear()
    speech.speech_ready.clear()


@pytest.fixture
def piper(tmp_path, monkeypatch):
    model = tmp_path / "v.onnx"
    model.write_bytes(b"x")
    monkeypatch.setitem(st.settings, "tts", "auto")
    monkeypatch.setitem(st.settings, "piper_voice", str(model))
    monkeypatch.setitem(sys.modules, "piper", types.SimpleNamespace(PiperVoice=FakePiper, SynthesisConfig=SynthesisConfig))
    speech.piper.clear()
    yield
    speech.piper.clear()


@pytest.fixture
def espeak(monkeypatch):
    seen = []
    monkeypatch.setitem(st.settings, "tts", "espeak")
    monkeypatch.setattr(speech.subprocess, "run", lambda a, **kw: seen.append(a) or Done(b"RIFFespeak"))
    return seen


@pytest.fixture
def hub(tmp_path):
    s = Station(tmp_path, {"127.0.0.1"})
    yield s
    s.close()


@pytest.fixture
def elsewhere(tmp_path):
    s = Station(tmp_path, set())
    yield s
    s.close()


# ---- the engines

def test_piper_takes_the_speaker_and_the_length_scale(piper):
    st.live.update(speaker=5, speed=50)
    speech.synth("Saved.")
    (text, cfg), = FakePiper.calls
    assert text == "Saved." and cfg.speaker_id == 5 and cfg.length_scale == 2.0     # 100 / 50: slower


@pytest.mark.parametrize("speed, scale", [(100, 1.0), (200, 0.5), (50, 2.0), (80, 1.25)])
def test_the_length_scale_is_100_over_the_speed(piper, speed, scale):
    st.live["speed"] = speed
    speech.synth("x")
    assert FakePiper.calls[0][1].length_scale == scale


def test_the_speaker_is_for_the_many_speaker_voice_only(piper):
    FakePiper.num_speakers = 1                     # one clear speaker: the number is not passed
    st.live["speaker"] = 7
    speech.synth("x")
    assert FakePiper.calls[0][1].speaker_id is None
    FakePiper.num_speakers = 904
    speech.piper.clear()                           # the voice is loaded once: load it again with its new count
    st.live["speaker"] = 903                       # the last speaker of 904
    speech.synth("x")
    assert FakePiper.calls[1][1].speaker_id == 903
    FakePiper.num_speakers = 100
    speech.piper.clear()                           # a voice with fewer speakers than the number: not passed (it would fail)
    speech.synth("x")
    assert FakePiper.calls[2][1].speaker_id is None


def test_a_piper_without_the_settings_class_says_the_phrase_with_espeak(piper, monkeypatch):
    monkeypatch.setitem(sys.modules, "piper", types.SimpleNamespace(PiperVoice=FakePiper))   # an old piper: no SynthesisConfig
    seen = []
    monkeypatch.setattr(speech.subprocess, "run", lambda a, **kw: seen.append(a) or Done(b"RIFFespeak"))
    assert speech.synth("x") == b"RIFFespeak" and seen and FakePiper.calls == []


@pytest.mark.parametrize("speed, wpm", [(100, "165"), (200, "330"), (50, "82"), (130, "214")])
def test_espeak_words_per_minute_are_165_times_the_speed_over_100(espeak, speed, wpm):
    st.live["speed"] = speed
    speech.synth("x")
    argv = espeak[0]
    assert argv[argv.index("-s") + 1] == wpm


def test_espeak_has_no_speaker(espeak):
    st.live["speaker"] = 9
    speech.synth("x")
    assert "9" not in espeak[0] and espeak[0][0] == "espeak-ng"


def test_the_drop_in_values_speak_until_a_live_one_comes(espeak, monkeypatch):
    monkeypatch.setitem(st.settings, "voice_speed", 200)             # the drop-in's
    speech.synth("x")
    assert espeak[0][espeak[0].index("-s") + 1] == "330"
    st.live["speed"] = 100                                           # the Hub's, no restart
    speech.synth("x")
    assert espeak[1][espeak[1].index("-s") + 1] == "165"


def test_the_environment_does_not_set_the_speaker_or_the_speed(monkeypatch):
    """The speaker and the speed are never drop-in settings; only the Hub's live values move them."""
    monkeypatch.setenv("STATION_VOICE_SPEAKER", "5")
    monkeypatch.setenv("STATION_VOICE_SPEED", "150")
    s = st.settings_from_env()
    assert (s["voice_speaker"], s["voice_speed"]) == (0, 100)
    assert not hasattr(st, "int_from_env")


def test_a_phrase_with_its_own_values_leaves_the_live_ones(piper):
    st.live.update(speaker=1, speed=100)
    speech.synth("x", {"speaker": 9, "speed": 200})
    cfg = FakePiper.calls[0][1]
    assert (cfg.speaker_id, cfg.length_scale) == (9, 0.5) and st.live == {"speaker": 1, "speed": 100}


# ---- the live values

def test_speech_off_live_silences_say_and_back_on_without_a_restart():
    st.live["speech"] = False
    speech.say("quiet")
    assert speech.speech["text"] is None and not speech.speech_ready.is_set()
    st.live["speech"] = True
    speech.say("loud")
    assert speech.speech["text"] == "loud" and speech.speech["opts"] is None and speech.speech_ready.is_set()


def test_a_plain_phrase_after_a_test_has_no_own_values():
    speech.say("a", {"volume": 90})
    speech.say("b")
    assert speech.speech["opts"] is None


def test_the_volume_goes_to_a_c3_panel_when_it_changes(monkeypatch):
    sent = []
    monkeypatch.setattr(wire, "panel_send", lambda d: sent.append(d) or True)
    monkeypatch.setitem(st.ports, "panel_chip", "esp32c3")
    speech.set_voice({"volume": 30})
    assert sent == ["V 30"] and st.voice_value("volume") == 30
    speech.set_voice({"speed": 80})                                  # nothing to tell the panel
    assert sent == ["V 30"]
    monkeypatch.setitem(st.ports, "panel_chip", None)                # an old panel / none: the value is kept, nothing sent
    speech.set_voice({"volume": 20})
    assert sent == ["V 30"] and st.live["volume"] == 20


def test_a_test_phrase_sets_its_volume_on_the_panel_and_puts_the_live_one_back(monkeypatch):
    sent = []
    monkeypatch.setattr(wire, "panel_send", lambda d: sent.append(d) or True)
    monkeypatch.setitem(st.panel, "ser", object())
    monkeypatch.setitem(st.ports, "panel_chip", "esp32c3")
    monkeypatch.setattr(speech, "synth", lambda text, opts=None: b"wav")
    monkeypatch.setattr(speech, "to_panel", lambda wav, chip=None: bytes([128]) * 300)
    st.live["volume"] = 35
    speech.speak(speech.SENTENCE, {"volume": 90})
    assert sent[0] == "V 90" and sent[-1] == "V 35"
    assert any(isinstance(d, bytes) and d.startswith(b"A ") for d in sent)
    sent.clear()
    speech.speak("plain")                                            # no volume of its own: no V lines at all
    assert not any(isinstance(d, str) for d in sent)


# ---- POST /voice

def post(station, path, body, proof=True):
    """The Hub's call: the body signed with the pairing proof for `voice` (`proof=False`: no header at all)."""
    raw = json.dumps(body).encode()
    status, d, _ = station.json("POST", path, body, station.proof("voice", raw) if proof else None)
    return status, d


def test_voice_takes_any_subset_and_keeps_it_live(hub):
    assert post(hub, "/voice", {"speaker": 12}) == (200, {})
    assert st.voice_values() == {"speech": True, "speaker": 12, "speed": 100, "volume": 50}
    assert post(hub, "/voice", {"speech": False, "speed": 150, "volume": 0}) == (200, {})
    assert st.voice_values() == {"speech": False, "speaker": 12, "speed": 150, "volume": 0}
    assert post(hub, "/voice", {}) == (200, {})                      # an empty subset changes nothing
    assert st.voice_values() == {"speech": False, "speaker": 12, "speed": 150, "volume": 0}
    assert st.settings["voice_speaker"] == 0                          # the drop-in's value stays under it


@pytest.mark.parametrize("name, low, high", [("speaker", 0, 903), ("speed", 50, 200), ("volume", 0, 100)])
def test_the_range_ends_are_taken_and_one_past_is_refused(hub, name, low, high):
    assert post(hub, "/voice", {name: low}) == (200, {}) and st.voice_value(name) == low
    assert post(hub, "/voice", {name: high}) == (200, {}) and st.voice_value(name) == high
    for bad in (low - 1, high + 1):
        status, d = post(hub, "/voice", {name: bad})
        assert status == 400 and set(d) == {"error", "message"} and d["error"] == "bad_request"
        assert name in d["message"]
        assert st.voice_value(name) == high                           # the refused value was not kept


@pytest.mark.parametrize("body", [{"speaker": "3"}, {"speaker": 3.5}, {"speed": True}, {"volume": None}, {"speech": "yes"},
                                  {"speech": 1}, {"speaker": [1]}])
def test_a_value_of_the_wrong_kind_is_refused(hub, body):
    status, d = post(hub, "/voice", body)
    assert status == 400 and d["error"] == "bad_request" and d["message"]
    assert st.live == {}


def test_one_bad_value_refuses_the_whole_request(hub):
    status, d = post(hub, "/voice", {"speaker": 4, "speed": 500})
    assert status == 400 and "speed" in d["message"]
    assert st.live == {}                                              # not even the good speaker


def test_a_body_that_is_not_an_object_is_refused(hub):
    status, d, _ = hub.json("POST", "/voice", [1, 2], hub.proof("voice", json.dumps([1, 2]).encode()))
    assert status == 400 and d["error"] == "bad_request"


def test_voice_is_for_the_allow_list_only(elsewhere):
    for path in ("/voice", "/voice/test"):
        status, d = post(elsewhere, path, {"speed": 100})
        assert status == 403 and d["error"] == "forbidden" and d["message"]
    assert st.live == {} and speech.speech["text"] is None


def test_a_browser_session_does_not_open_the_voice_routes(elsewhere):
    ticket = elsewhere.access.new_ticket()
    status, d, r = elsewhere.json("POST", "/session", {"ticket": ticket})
    assert status == 200
    cookie = {"Cookie": r.getheader("Set-Cookie").split(";")[0]}
    for path in ("/voice", "/voice/test"):
        status, d, _ = elsewhere.json("POST", path, {"volume": 10}, cookie)
        assert status == 403 and d["error"] == "forbidden"
    assert st.live == {}


def test_the_voice_routes_answer_post_only(hub):
    assert hub.json("GET", "/voice")[0] == 404 and hub.json("GET", "/voice/test")[0] == 404


def settings_post(station, body, headers=None):
    """A change on the page's settings route: the Hub's call (the proof) unless `headers` is a page session's."""
    raw = json.dumps(body).encode()
    status, d, _ = station.json("POST", "/settings", body, station.proof("settings", raw) if headers is None else headers)
    return status, d


def listed(station, id_):
    return next(s for s in station.store.listing() if s["id"] == id_)


def test_before_the_hub_sends_a_value_the_page_changes_speak_and_volume_as_before(hub):
    assert st.live == {}
    for id_ in ("speech", "volume"):
        assert "readonly" not in listed(hub, id_) and "note" not in listed(hub, id_)
    status, d = settings_post(hub, {"speech": False, "volume": 20})
    assert status == 200
    assert {s["id"]: s["value"] for s in d["settings"] if s["id"] in ("speech", "volume")} == {"speech": False, "volume": 20}


def test_once_the_hub_sent_values_speak_and_volume_show_the_hubs_values_read_only(hub):
    post(hub, "/voice", {"speech": False, "volume": 77})
    for id_, value in (("speech", False), ("volume", 77)):
        row = listed(hub, id_)
        assert row["value"] == value == st.voice_value(id_)           # the list never contradicts the value in force
        assert row["readonly"] is True
        assert row["note"] == "Speaking and volume are set on the Hub: Settings > Weigh Station voice."
    assert "readonly" not in listed(hub, "beep") and "readonly" not in listed(hub, "panel_swap")


def test_the_hello_values_lock_the_page_too(hub):
    speech.apply_hello({"voice": {"speed": 90}})
    assert listed(hub, "volume")["readonly"] is True and listed(hub, "speech")["readonly"] is True


def test_a_page_change_of_speak_or_volume_is_refused_and_the_live_value_stands(hub):
    post(hub, "/voice", {"speech": False, "volume": 77})
    for body in ({"volume": 20}, {"speech": True}, {"volume": 77}):
        status, d = settings_post(hub, body)
        assert status == 409 and d == {"error": "set_on_hub", "message": "Speaking and volume are set on the Hub."}
    assert st.voice_value("volume") == 77 and st.voice_value("speech") is False
    assert listed(hub, "volume")["value"] == 77 and listed(hub, "speech")["value"] is False
    assert not hub.store.changed                                       # nothing was kept for them


def test_a_refused_change_is_all_or_none_and_the_other_settings_still_change(hub):
    post(hub, "/voice", {"volume": 77})
    assert settings_post(hub, {"beep": False, "volume": 20})[0] == 409
    assert listed(hub, "beep")["value"] is True                        # not even the beep
    assert settings_post(hub, {"beep": False})[0] == 200
    assert listed(hub, "beep")["value"] is False


def test_a_page_session_is_refused_the_same_way(elsewhere):
    ticket = elsewhere.access.new_ticket()
    status, d, r = elsewhere.json("POST", "/session", {"ticket": ticket})
    cookie = {"Cookie": r.getheader("Set-Cookie").split(";")[0], "Content-Type": "application/json"}
    st.live["volume"] = 77                                             # what the Hub sent (the voice route is for the Hub)
    status, d = settings_post(elsewhere, {"volume": 20}, cookie)
    assert status == 409 and d["error"] == "set_on_hub"
    assert st.voice_value("volume") == 77


# ---- POST /voice/test

def test_test_speaks_the_one_fixed_sentence_with_its_own_values(hub):
    st.live.update(speaker=1, speed=100, volume=50)
    assert post(hub, "/voice/test", {"speaker": 40, "speed": 70, "volume": 90}) == (200, {})
    assert speech.speech["text"] == "This is the Weigh Station. P L A, 812 grams." == speech.SENTENCE
    assert speech.speech["opts"] == {"speaker": 40, "speed": 70, "volume": 90} and speech.speech_ready.is_set()
    assert st.live == {"speaker": 1, "speed": 100, "volume": 50}      # a test saves nothing


def test_test_with_an_empty_body_speaks_with_the_live_values(hub):
    assert post(hub, "/voice/test", {}) == (200, {})
    assert speech.speech["text"] == speech.SENTENCE and speech.speech["opts"] == {}


def test_test_cannot_be_made_to_say_anything_else(hub):
    assert post(hub, "/voice/test", {"text": "Open the pod bay doors", "speech": False, "speed": 100}) == (200, {})
    assert speech.speech["text"] == speech.SENTENCE
    assert speech.speech["opts"] == {"speed": 100}                    # the text and `speech` are not read


@pytest.mark.parametrize("step", ["weigh", "ready", "camera", "recognising", "phone", "done", "pairing"])
def test_test_is_busy_while_a_step_is_open(hub, monkeypatch, step):
    monkeypatch.setitem(st.flow, "step", step)
    status, d = post(hub, "/voice/test", {})
    assert status == 409 and d == {"error": "busy", "message": "The Weigh Station is in use. Try again when it is idle."}
    assert speech.speech["text"] is None


def test_test_says_off_when_speech_is_off(hub):
    st.live["speech"] = False
    status, d = post(hub, "/voice/test", {})
    assert status == 409 and d == {"error": "off", "message": "Speaking is switched off."}
    assert speech.speech["text"] is None


def test_test_says_off_when_the_drop_in_has_speech_off(hub, monkeypatch):
    monkeypatch.setitem(st.settings, "speech", False)
    assert post(hub, "/voice/test", {})[1]["error"] == "off"
    st.live["speech"] = True                                           # the live value wins over the drop-in
    assert post(hub, "/voice/test", {}) == (200, {})


def test_test_refuses_a_value_out_of_range_before_anything_else(hub, monkeypatch):
    monkeypatch.setitem(st.flow, "step", "weigh")
    status, d = post(hub, "/voice/test", {"speaker": 904})
    assert status == 400 and d["error"] == "bad_request" and "speaker" in d["message"]
    for body in ({"speed": 49}, {"speed": 201}, {"volume": 101}, {"volume": -1}, {"speaker": -1}):
        assert post(hub, "/voice/test", body)[0] == 400
    assert speech.speech["text"] is None


# ---- the hello answer

class Api:
    paired = True

    def __init__(self, body):
        self.body = body

    def hello(self, values=None):
        return 200, self.body


VOICE = {"speech": False, "speaker": 321, "speed": 120, "volume": 33}


def test_the_hello_answer_voice_goes_live_at_start():
    assert server_api.announce(lambda: Api({"ok": True, "voice": VOICE}), "ab" * 32, 5003, tries=1)
    assert st.voice_values() == VOICE


def test_the_hello_answer_voice_goes_live_at_a_check_in():
    taken = server_api.checkin(lambda: Api({"voice": VOICE}), "ab" * 32, 5003, sleep=lambda s: None, rounds=1)
    assert taken == 1 and st.voice_values() == VOICE


def test_an_older_hub_with_no_voice_leaves_the_values_alone():
    st.live.update(speed=60)
    assert server_api.announce(lambda: Api({"ok": True}), "ab" * 32, 5003, tries=1)
    assert st.voice_values()["speed"] == 60
    for odd in ("loud", None, [1], 7):
        assert server_api.announce(lambda: Api({"voice": odd}), "ab" * 32, 5003, tries=1)
    assert st.voice_values()["speed"] == 60


def test_a_bad_value_in_the_hello_is_skipped_and_the_good_ones_are_taken():
    assert server_api.announce(lambda: Api({"voice": {"speaker": 5000, "speed": 90, "volume": "loud", "other": 1}}),
                               "ab" * 32, 5003, tries=1)
    assert st.live == {"speed": 90}


def test_the_hello_is_taken_even_if_applying_the_voice_fails(monkeypatch):
    monkeypatch.setattr(speech, "apply_hello", lambda body: 1 / 0)
    assert server_api.announce(lambda: Api({"voice": VOICE}), "ab" * 32, 5003, tries=1)


def test_the_hello_volume_goes_to_a_c3_panel(monkeypatch):
    sent = []
    monkeypatch.setattr(wire, "panel_send", lambda d: sent.append(d) or True)
    monkeypatch.setitem(st.ports, "panel_chip", "esp32c3")
    server_api.announce(lambda: Api({"voice": {"volume": 12}}), "ab" * 32, 5003, tries=1)
    assert sent == ["V 12"]


def test_the_station_says_the_test_sentence_end_to_end(hub, piper):
    """The queued test phrase reaches Piper with the speaker and the length scale from the request."""
    post(hub, "/voice/test", {"speaker": 77, "speed": 200})
    wav = speech.synth(speech.speech["text"], speech.speech["opts"])
    (text, cfg), = FakePiper.calls
    assert text == speech.SENTENCE and (cfg.speaker_id, cfg.length_scale) == (77, 0.5)
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getnframes() == 10


# ---- the texts are the catalogue's (D13)

def _state_copy():
    """A second copy of station/state.py, read fresh, so its texts are resolved again against the page's catalogue."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("filament_kit.station.state_copy", st.__file__)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_the_voice_refusal_sentences_come_from_the_catalogue_with_the_english_as_fallback(monkeypatch):
    from filament_kit.station import page
    english = {"bad_speech": "speech must be true or false.",
               "bad_speaker": "speaker must be a whole number from 0 to 903.",
               "bad_speed": "speed must be a whole number from 50 to 200, in steps of 10.",
               "bad_volume": "volume must be a whole number from 0 to 100.",
               "busy": "The Weigh Station is in use. Try again when it is idle.",
               "off": "Speaking is switched off.",
               "set_on_hub_note": "Speaking and volume are set on the Hub: Settings > Weigh Station voice.",
               "set_on_hub": "Speaking and volume are set on the Hub."}
    monkeypatch.setattr(page, "_CATALOGUE", {})                       # no catalogue key yet: the English
    assert _state_copy().VOICE_TEXTS == english
    monkeypatch.setattr(page, "_CATALOGUE", {f"station_voice_{k}": f"[{k}]" for k in english})
    assert _state_copy().VOICE_TEXTS == {k: f"[{k}]" for k in english}  # a catalogue key wins


# ---- the speed goes in steps of 10 at the station too

@pytest.mark.parametrize("speed", [55, 51, 199, 101])
def test_a_speed_off_the_steps_of_ten_is_refused(hub, speed):
    status, d = post(hub, "/voice", {"speed": speed})
    assert status == 400 and d["error"] == "bad_request" and "steps of 10" in d["message"]
    assert st.live == {}
    assert post(hub, "/voice/test", {"speed": speed})[0] == 400 and speech.speech["text"] is None


@pytest.mark.parametrize("speed", [50, 60, 100, 130, 200])
def test_a_speed_on_the_steps_of_ten_is_taken(hub, speed):
    assert post(hub, "/voice", {"speed": speed}) == (200, {}) and st.voice_value("speed") == speed


def test_a_hello_speed_off_the_steps_is_skipped():
    speech.apply_hello({"voice": {"speed": 95, "volume": 10}})
    assert st.live == {"volume": 10}


# ---- a phrase's text and its own values are taken as one pair

def test_a_phrase_is_never_read_with_another_phrases_values(monkeypatch):
    class Racing(dict):
        """The moment the loop has read the text, a newer phrase (a test, with its own volume) arrives."""
        fired = False
        newer = None

        def __getitem__(self, key):
            value = super().__getitem__(key)
            if key == "text" and not Racing.fired:
                Racing.fired = True
                Racing.newer = threading.Thread(target=speech.say, args=("test sentence", {"volume": 90}))
                Racing.newer.start()
                Racing.newer.join(0.3)  # with the lock the newer phrase waits until the pair is taken
            return value

    monkeypatch.setattr(speech, "speech", Racing(text="Cancelled.", opts=None))
    pair = speech.next_phrase()
    assert pair == ("Cancelled.", None)
    Racing.newer.join(5)                # the newer phrase writes once the lock is free: on a loaded runner after the next read
    assert speech.next_phrase() == ("test sentence", {"volume": 90})


# ---- the routes need the pairing proof as /settings does

@pytest.mark.parametrize("path", ["/voice", "/voice/test"])
def test_the_voice_routes_refuse_a_listed_address_with_no_proof(hub, path):
    status, d = post(hub, path, {"volume": 10, "speed": 100}, proof=False)
    assert status == 403 and d["error"] == "forbidden" and d["message"]
    assert st.live == {} and speech.speech["text"] is None


@pytest.mark.parametrize("path", ["/voice", "/voice/test"])
def test_the_voice_routes_refuse_a_proof_for_another_purpose_body_or_key(hub, path):
    from filament_kit.station import page
    body = {"speed": 100}
    raw = json.dumps(body).encode()
    other_body = json.dumps({"speed": 90}).encode()
    wrong_key = {page.PROOF_HEADER: page.make_proof(page.token_key("another token 0123456789"), "voice", raw, now=hub.clock())}
    for h in (hub.proof("settings", raw), hub.proof("ticket", raw), hub.proof("voice", other_body), wrong_key,
              {page.PROOF_HEADER: "garbage"}):
        status, d, _ = hub.json("POST", path, body, h)
        assert status == 403 and d["error"] == "forbidden"
    assert st.live == {} and speech.speech["text"] is None


@pytest.mark.parametrize("path", ["/voice", "/voice/test"])
def test_the_voice_routes_refuse_a_replayed_or_stale_proof(hub, path):
    from filament_kit.station import page
    raw = json.dumps({"speed": 100}).encode()
    h = hub.proof("voice", raw)
    assert hub.json("POST", path, {"speed": 100}, h)[0] == 200
    assert hub.json("POST", path, {"speed": 100}, h)[0] == 403            # the same proof again: a replay
    old = {page.PROOF_HEADER: page.make_proof(hub.key, "voice", raw, now=hub.clock() - 120)}
    assert hub.json("POST", path, {"speed": 100}, old)[0] == 403


def test_the_voice_routes_refuse_a_right_proof_from_an_address_off_the_list(elsewhere):
    for path in ("/voice", "/voice/test"):
        status, d = post(elsewhere, path, {"speed": 100})
        assert status == 403 and d["error"] == "forbidden"
    assert st.live == {} and speech.speech["text"] is None
