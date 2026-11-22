"""The Weigh Station's voice settings (R88 section 2 and the ruling of 2026-11-22 01:35): what the Hub hands the station,
the logic behind the three kit routes (`GET` / `PATCH /api/voice`, `POST /api/voice/test`; api.py only mounts them) and the
page route `POST station/voice/test`.

    POST station/voice/test {speaker?, speed?, volume?}     {} or the refusal {error: sentence, code}

Speak, Speaker, Speed and Volume apply without a restart: the Hub sends them to the station when they are saved
(`hand_over`) and in the answer of the station's hello (`hello_voice`). A station that does not answer never stops a save.
"""
from __future__ import annotations

import logging
import socket
import urllib.parse

import anyio

from .. import config, station_client

log = logging.getLogger(__name__)

TEXT_VOICE_PLAYED = "Played on the Weigh Station."
TEXT_VOICE_NO_ANSWER = "The Weigh Station did not answer."
TEXT_VOICE_BUSY = "The Weigh Station is in use. Try again when it is idle."
TEXT_VOICE_OFF = "Speaking is switched off."
TEXT_VOICE_REMOTE = "Chosen on the Weigh Station's own computer: spoolwarden config."
TEXT_VOICE_APPS_READONLY = "Change the voice on the Hub page."

VOICE_MANY = "Piper, many speakers"
VOICE_ONE = "Piper, one clear speaker"
VOICE_ESPEAK = "espeak-ng, robotic, no download"
_ONE_CLEAR = "en_US-ljspeech-medium"

KEYS = ("station.speech", "station.voice_speaker", "station.voice_speed", "station.speaker_volume")
FIELDS = {"speech": "station.speech", "speaker": "station.voice_speaker", "speed": "station.voice_speed",
          "volume": "station.speaker_volume"}


class VoiceError(Exception):
    """A refusal as the kit routes send it: `{error: code, message}` (and `field`) with `status`."""

    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.field = status, code, message, field


def station_url(cfg: dict) -> str | None:
    st = cfg.get("station")
    url = st.get("url") if isinstance(st, dict) else None
    return url if isinstance(url, str) and url.strip() else None


def station_remote(cfg: dict) -> bool:
    """True when the Weigh Station is on another computer than the Hub (its address is not this computer)."""
    try:
        host = (urllib.parse.urlsplit(station_url(cfg) or "").hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return not (host in ("localhost", "::1") or host.startswith("127.") or host == socket.gethostname().lower())


def voice_name(cfg: dict) -> str:
    """The Voice row's choice, as shown."""
    st = cfg.get("station")
    st = st if isinstance(st, dict) else {}
    if st.get("tts") == "espeak":
        return VOICE_ESPEAK
    return VOICE_ONE if st.get("voice") == _ONE_CLEAR else VOICE_MANY


def voice_row(cfg: dict) -> dict:
    """The Voice row for the Hub page: `{value, editable, text}`; a station on another computer chooses its voice there."""
    remote = station_remote(cfg)
    return {"value": voice_name(cfg), "editable": not remote, "text": TEXT_VOICE_REMOTE if remote else None}


def hello_voice(cfg: dict) -> dict:
    """`voice` in the answer of `GET /api/station/hello`: `{speech, speaker, speed, volume}`."""
    return config.voice_values(cfg)


def hand_over(cfg: dict, only: tuple | list | None = None) -> bool:
    """Send the values to the station (`POST /voice`); True when it took them. False when no station is set or it does
    not answer or refuses them: the settings are saved either way."""
    url = station_url(cfg)
    if not url:
        return False
    values = config.voice_values(cfg)
    if only is not None:
        values = {k: v for k, v in values.items() if k in only}
    try:
        station_client.send_voice(url, values)
    except (station_client.StationCallError, OSError, ValueError) as e:
        log.warning("Weigh Station voice: not handed over (%s)", e)
        return False
    return True


# ---- the kit routes' logic ----

def get_voice(cfg: dict) -> dict:
    """`GET /api/voice`; `station: false` when the Hub has no Weigh Station (the rest is then left out)."""
    if not station_url(cfg):
        return {"station": False}
    name = voice_name(cfg)
    return {"station": True, **config.voice_values(cfg), "voice": name,
            "speaker_range": [0, config.VOICE_SPEAKER_MAX] if name == VOICE_MANY else None}


def check_values(body) -> dict:
    """The fields of a PATCH or a test, checked: `{field: value}` for `speech`, `speaker`, `speed`, `volume` (a field
    that is not sent stays out). VoiceError 400 (with `field`) for a value out of range or of another type."""
    if not isinstance(body, dict):
        raise VoiceError(400, "bad_request", "the request must be a JSON object")
    out = {}
    for f, v in body.items():
        whole = isinstance(v, int) and not isinstance(v, bool)
        if f == "speech":
            ok, why = isinstance(v, bool), "must be true or false"
        elif f == "speaker":
            ok, why = whole and 0 <= v <= config.VOICE_SPEAKER_MAX, f"must be a whole number from 0 to {config.VOICE_SPEAKER_MAX}"
        elif f == "speed":
            ok = whole and config.VOICE_SPEED_MIN <= v <= config.VOICE_SPEED_MAX and v % config.VOICE_SPEED_STEP == 0
            why = f"must be from {config.VOICE_SPEED_MIN} to {config.VOICE_SPEED_MAX} in steps of {config.VOICE_SPEED_STEP}"
        elif f == "volume":
            ok, why = whole and 0 <= v <= 100, "must be a whole number from 0 to 100"
        else:
            continue            # an unknown field is ignored, as the other kit routes do
        if not ok:
            raise VoiceError(400, "bad_value", f"{f} {why}", field=f)
        out[f] = v
    return out


def patch_voice(body, *, load=config.load, save=config.save) -> dict:
    """`PATCH /api/voice`: check every field, save through the config save path, hand the values to the station, answer
    as `get_voice`. Nothing is saved when a field is bad. `load` / `save` are the caller's (the kit server holds no
    write access to the settings itself)."""
    new = check_values(body)
    cfg = load()
    if not station_url(cfg):
        raise VoiceError(409, "no_station", "This Hub has no Weigh Station.")
    st = cfg.setdefault("station", {})
    for f, v in new.items():
        st[FIELDS[f].split(".", 1)[1]] = v
    if new:
        save(cfg)
        hand_over(cfg, tuple(new))
    return get_voice(cfg)


def play_test(cfg: dict, body) -> None:
    """`POST /api/voice/test`: the station says its one sentence. VoiceError 409 `busy` / `off`, 502 `no_answer`."""
    values = {k: v for k, v in check_values(body).items() if k != "speech"}
    if not station_url(cfg):
        raise VoiceError(409, "no_station", "This Hub has no Weigh Station.")
    try:
        status, code = station_client.voice_test(station_url(cfg), values)
    except (station_client.StationCallError, OSError, ValueError):
        raise VoiceError(502, "no_answer", TEXT_VOICE_NO_ANSWER) from None
    if status == 200:
        return
    if status == 409 and code == "busy":
        raise VoiceError(409, "busy", TEXT_VOICE_BUSY)
    if status == 409 and code == "off":
        raise VoiceError(409, "off", TEXT_VOICE_OFF)
    raise VoiceError(502, "no_answer", TEXT_VOICE_NO_ANSWER)


# ---- the Hub page's route ----

def routes(page) -> list[tuple]:
    from ..setup_page import Refused

    async def test(request, body):
        try:
            await anyio.to_thread.run_sync(lambda: play_test(config.load(), body))
        except VoiceError as e:
            raise Refused(e.status, e.message, code=e.code) from None
        return {}

    return [("/station/voice/test", page.guard(test, write=True), "POST")]
