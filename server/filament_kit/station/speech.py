"""The panel talks ("1042 grams. Hold yellow to save."): Piper speech (the `voice` module's model, STATION_PIPER_VOICE)
or, without it, espeak-ng (STATION_TTS=espeak forces it), cut to the 8 kHz speech band by ffmpeg and streamed to the
panel's speaker in 256-byte `A <n>` frames (else played on the beep output with aplay). STATION_SPEECH=0 turns it off.
Piper's voice loads once (about 1 s), then makes a phrase in about 0.1 s; piper-tts is the `[station-tts]` extra.

A newer phrase cuts off the one playing, fading it out from where it is so the speaker's coupling capacitor doesn't
thump; each phrase is ramped up from 0 (off) before and back down after for the same reason. The C3 panel's amplifier
has no such capacitor: there 128 is silence, so phrases are padded with 128 and cut off toward 128."""
from __future__ import annotations

import io
import logging
import os
import struct
import subprocess
import threading
import time
import wave

from . import beep, voice, wire
from . import state as st

log = logging.getLogger("intake-station")

RATE = 8000  # panel speaker sample rate
RAMP = RATE * 3 // 20  # 150 ms ramp of the speaker's DC level in and out, so its coupling capacitor doesn't thump

FILTERS = {"esp8266": "highpass=f=450,highpass=f=450,equalizer=f=2500:t=q:w=1:g=8,lowpass=f=3600,"
                      "acompressor=threshold=0.03:ratio=10:attack=2:release=50",
           "esp32c3": "highpass=f=150,lowpass=f=3600,acompressor=threshold=0.1:ratio=3:attack=5:release=100"}

SENTENCE = "This is the Weigh Station. P L A, 812 grams."  # the one phrase `POST /voice/test` speaks

speech = {"text": None, "opts": None}  # opts: this phrase's own speaker / speed / volume (a test), else the live ones
speech_ready = threading.Event()
speech_lock = threading.Lock()  # a phrase's text and its opts are written and read as one pair


def say(text, opts=None):
    """Speak text on the panel's speaker; a newer phrase cuts off the one playing. `opts` ({speaker, speed, volume},
    any subset) replaces the live values for this phrase only."""
    if st.voice_value("speech"):
        with speech_lock:
            speech["opts"] = opts
            speech["text"] = text
        speech_ready.set()


piper = {}  # the loaded Piper voice, kept between phrases (loading takes ~1 s, a phrase ~0.1 s)
_piper_path = [None]  # model path currently loaded into `piper`


def piper_voice():
    """The Piper voice if STATION_TTS allows it and its model is installed, else None (then espeak-ng speaks)."""
    path = st.settings["piper_voice"]
    if st.settings["tts"] == "espeak" or not os.path.exists(path):
        return None
    if _piper_path[0] != path:
        piper.clear()
        _piper_path[0] = path
    if "voice" not in piper:
        try:
            from piper import PiperVoice  # the [station-tts] extra
            piper["voice"] = PiperVoice.load(path)
        except Exception as e:  # noqa: BLE001 - piper-tts missing or the model broken: don't retry every phrase
            log.warning("piper: %s; speaking with espeak-ng", e)
            piper["voice"] = None
    return piper["voice"]


def engine():
    """Which voice speaks: for the log at start (which also preloads Piper's)."""
    return f"Piper ({os.path.basename(st.settings['piper_voice'])})" if piper_voice() else "espeak-ng"


def piper_settings(voice, speaker, speed):
    """Piper's synthesis settings (piper-tts 1.8.0 `SynthesisConfig`, `PiperVoice.synthesize_wav(syn_config=)`): the
    length scale is 100 / speed (above 1 is slower); the speaker number only for a many-speaker voice (and inside the
    numbers it has: a single-speaker voice ignores it, a number past the last would fail)."""
    from piper import SynthesisConfig  # the [station-tts] extra
    speakers = getattr(getattr(voice, "config", None), "num_speakers", 1)
    return SynthesisConfig(length_scale=100 / speed, speaker_id=speaker if speakers > 1 and speaker < speakers else None)


def espeak_speed(speed):
    """espeak-ng's words per minute for a speed in percent: 165 at 100."""
    return round(165 * speed / 100)


def synth(text, opts=None):
    """The phrase as WAV bytes: Piper's natural voice when installed (libritts_r, the live speaker and speed), else
    espeak-ng's (the live speed); None if neither works. `opts` replaces the live speaker / speed for this phrase."""
    speaker = (opts or {}).get("speaker", st.voice_value("speaker"))
    speed = (opts or {}).get("speed", st.voice_value("speed"))
    voice = piper_voice()
    if voice:
        try:
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                voice.synthesize_wav(text, w, syn_config=piper_settings(voice, speaker, speed))
            return buf.getvalue()
        except Exception as e:  # noqa: BLE001 - one bad phrase: espeak-ng says it
            log.warning("piper: %s; this phrase with espeak-ng", e)
    try:
        p = subprocess.run(["espeak-ng", "-v", "en-us", "-s", str(espeak_speed(speed)), "--stdout", text],
                           capture_output=True, timeout=10)
        return p.stdout if p.returncode == 0 and p.stdout else None
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("speech: %s", e)
        return None


def cutoff(sample, chip):
    """The fade-out of a phrase cut off at `sample`, over RAMP samples: toward silence, which is 128 on the C3 panel
    (a byte is a sample) and 0 (off) on an old one."""
    mid = 128 if chip == "esp32c3" else 0
    return bytes(mid + round((sample - mid) * (RAMP - j) / RAMP) for j in range(RAMP))


def to_panel(wav, chip=None):
    """WAV -> the panel's speaker samples, voiced for a small unbaffled headset driver (user's A/B pick 2026-11-02):
    nothing under 450 Hz (it can't play it), +8 dB at 2.5 kHz (consonants), hard compression, normalised so all but
    the loudest 2 % of samples fit (those clip). Then PWM duty bytes around 128 (kept within 16-240), with a ramp up
    from 0 (off) before and back down after. For the C3 panel (chip "esp32c3": a MAX98357 into a sealed 8 ohm speaker,
    a byte is a sample and 128 is silence) the filter is FILTERS["esp32c3"] and there is no ramp from 0, which would be
    a full negative sample: just 50 ms of 128 on both ends."""
    c3 = chip == "esp32c3"
    p = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "wav", "-i", "pipe:0",
                        "-af", FILTERS["esp32c3" if c3 else "esp8266"],
                        "-ar", str(RATE), "-ac", "1", "-f", "s16le", "pipe:1"],
                       input=wav, capture_output=True, timeout=10)
    if p.returncode:
        raise OSError(p.stderr.decode(errors="replace").strip())
    pcm = struct.unpack(f"<{len(p.stdout) // 2}h", p.stdout[:len(p.stdout) // 2 * 2])
    mags = sorted(map(abs, pcm))
    gain = 112 / max(1, mags[int(len(mags) * 0.98)] if mags else 1)
    body = bytes(128 + max(-112, min(112, round(s * gain))) for s in pcm)
    if c3:
        return bytes([128]) * (RATE // 20) + body + bytes([128]) * (RATE // 20)
    ramp = bytes(round(128 * i / RAMP) for i in range(RAMP))
    return ramp + bytes([128]) * (RATE // 20) + body + bytes([128]) * (RATE // 20) + ramp[::-1]


def speak(text, opts=None):
    """One phrase: synthesise it and stream it in real time (0.25 s ahead) in 256-byte "A" frames between the lamp
    lines, or play it on the PC's own output when there is no panel. A volume in `opts` (a test) is set on the C3
    panel for this phrase only and put back to the live one after it."""
    wav = synth(text, opts)
    if not wav:
        return
    if not st.panel["ser"]:  # no panel: the PC's own output, if any
        dev = st.settings["beep_device"] or beep.beep_device()
        if dev:
            subprocess.run(["aplay", "-q", "-D", dev, "-"], input=wav, capture_output=True, timeout=15)
        return
    chip = st.ports["panel_chip"]  # once per phrase
    try:
        samples = to_panel(wav, chip)
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("speech: %s", e)
        return
    own_volume = chip == "esp32c3" and (opts or {}).get("volume") is not None
    if own_volume:
        wire.panel_send("V %d" % opts["volume"])
    try:
        start, lead = time.monotonic(), RATE // 4
        for i in range(0, len(samples), 256):
            if speech_ready.is_set():  # cut off by a newer phrase: fade out from where this one is
                chunk = cutoff(samples[i], chip)
                for k in range(0, RAMP, 256):
                    wire.panel_send(b"A %d\n" % len(chunk[k:k + 256]) + chunk[k:k + 256])
                break
            wait = start + (i - lead) / RATE - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            chunk = samples[i:i + 256]
            if not wire.panel_send(b"A %d\n" % len(chunk) + chunk):
                break
    finally:
        if own_volume:
            wire.panel_send("V %d" % st.voice_value("volume"))


def next_phrase():
    """`(text, opts)` of the phrase `say` left: always one phrase's own pair, never one's text with another's opts."""
    with speech_lock:
        return speech["text"], speech["opts"]


def speech_loop():
    """Speak each phrase `say` leaves, newest only."""
    if st.voice_value("speech"):
        log.info("speech: %s", engine())
    while True:
        speech_ready.wait()
        speech_ready.clear()
        try:
            speak(*next_phrase())
        except (OSError, subprocess.TimeoutExpired) as e:  # aplay missing or the output hung
            log.warning("speech: %s", e)


class VoiceRefused(Exception):
    """A voice request the station refuses: the HTTP status, the code word and the sentence."""

    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message


def apply_voice(values):
    """Keep these (already checked) voice values live, over the drop-in's, with no restart. The C3 panel keeps its own
    volume, so it is told the new one."""
    st.live.update(values)
    if "tts" in values:
        st.settings["tts"] = values["tts"]
    if "voice" in values:
        st.settings["piper_voice"] = voice.model_path(name=values["voice"])
        piper.clear()
        _piper_path[0] = None
    if "volume" in values and st.ports["panel_chip"] == "esp32c3":
        from . import panel
        panel.send_volume()


def set_voice(body):
    """`POST /voice`: any of speech, speaker, speed, volume. All of it or none: a value out of range refuses the whole
    request (VoiceRefused 400)."""
    values, problems = st.clean_voice(body)
    if problems:
        raise VoiceRefused(400, "bad_request", next(iter(problems.values())))
    apply_voice(values)


def apply_hello(body):
    """The `voice` of the Hub's hello answer (GET /api/station/hello): kept live like `POST /voice`; a bad or missing
    value is left alone (an older Hub says nothing, and the hello is never refused for it)."""
    voice = body.get("voice") if isinstance(body, dict) else None
    if not isinstance(voice, dict):
        return
    values, problems = st.clean_voice(voice)
    for name, why in problems.items():
        log.warning("hello: voice %s ignored (%s)", name, why)
    if values:
        apply_voice(values)


def test_voice(body):
    """`POST /voice/test`: speak SENTENCE (no text comes from the caller) with the body's speaker, speed and volume
    (any subset, for this phrase only; the live values stay). VoiceRefused: 400 a value out of range, 409 `off` with
    speech off, 409 `busy` while a step of the flow is open."""
    values, problems = st.clean_voice({k: v for k, v in body.items() if k != "speech"})
    if problems:
        raise VoiceRefused(400, "bad_request", next(iter(problems.values())))
    if not st.voice_value("speech"):
        raise VoiceRefused(409, "off", st.VOICE_TEXTS["off"])
    with st.lock:
        busy = st.flow["step"] != "idle"
    if busy:
        raise VoiceRefused(409, "busy", st.VOICE_TEXTS["busy"])
    say(SENTENCE, values)
