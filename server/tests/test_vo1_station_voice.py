"""VO1 (R88 section 2): the Weigh Station's voice settings. Speaker and speed in both engines (piper-tts 1.8.0 `SynthesisConfig`
length scale 100 / speed and speaker id; espeak-ng words per minute 165 x speed / 100), the live values over the drop-in's,
`POST /voice`, `POST /voice/test` and the `voice` of the hello answer (station/speech.py, state.py, http.py, server_api.py)."""
from __future__ import annotations

import io
import sys
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


def test_the_environment_gives_the_drop_in_values_clamped(monkeypatch):
    monkeypatch.setenv("STATION_VOICE_SPEAKER", "5000")
    monkeypatch.setenv("STATION_VOICE_SPEED", "10")
    s = st.settings_from_env()
    assert (s["voice_speaker"], s["voice_speed"]) == (903, 50)
    monkeypatch.setenv("STATION_VOICE_SPEAKER", "x")
    monkeypatch.delenv("STATION_VOICE_SPEED")
    s = st.settings_from_env()
    assert (s["voice_speaker"], s["voice_speed"]) == (0, 100)


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
