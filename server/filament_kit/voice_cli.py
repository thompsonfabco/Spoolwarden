"""The command line's part of the Weigh Station's voice (R88 VO6): `weigh-station voice-test` and the four values of the
`config` group "Weigh Station voice" (Speak, Speaker, Speed, Volume).

The commands run as root and never read or write voice.xml: the kit service is the one writer (R88 01:50). Everything here
goes through its routes, `GET` / `PATCH /api/voice` and `POST /api/voice/test`, with the command line's own token over
loopback (`api` is cli's `_server_api`, which gives None when no service answers). The ranges below are only for the
sentence said before a value is sent; the service checks them again.
"""
from __future__ import annotations

from . import output
from .station.page import catalogue_text
from .web.slots import SlotError

SPEAKER_MAX = 903                   # a many-speaker Piper voice: 904 speakers
SPEED_MIN, SPEED_MAX, SPEED_STEP = 50, 200, 10
FIELDS = ("speech", "speaker", "speed", "volume")

TEXT_NO_SERVICE = "The Hub is not running, so the voice cannot be changed or tested."


class VoiceRefused(Exception):
    """A value or a call the Hub refused: the str is the sentence to say."""


def view(api) -> dict | None:
    """The voice values as `GET /api/voice` answers them, or None when no service answers, the Hub has no Weigh Station
    (`station: false`) or it has no such route."""
    client = api() if callable(api) else api
    if client is None:
        return None
    try:
        got = client.get("/api/voice")
    except SlotError:
        return None
    return got if isinstance(got, dict) and got.get("station") else None


def check(field: str, n: int, got: dict) -> int:
    """`n` for `field`, or VoiceRefused with the sentence (the service checks it again)."""
    if field == "speaker":
        top = SPEAKER_MAX if got.get("speaker_range") else 0
        if not 0 <= n <= top:
            raise VoiceRefused(f"speaker must be a whole number from 0 to {top}.")
    elif field == "speed":
        if not SPEED_MIN <= n <= SPEED_MAX or n % SPEED_STEP:
            raise VoiceRefused(f"speed must be a whole number from {SPEED_MIN} to {SPEED_MAX}, in steps of {SPEED_STEP}.")
    elif field == "volume":
        if not 0 <= n <= 100:
            raise VoiceRefused("volume must be a whole number from 0 to 100.")
    return n


def save(api, values: dict) -> None:
    """Send the changed values to the service (`PATCH /api/voice`); VoiceRefused with the Hub's sentence."""
    client = api() if callable(api) else api
    if client is None:
        raise VoiceRefused(TEXT_NO_SERVICE)
    try:
        client.patch("/api/voice", values)
    except SlotError as e:
        raise VoiceRefused(str(e)) from None


def cmd_voice_test(api) -> int:
    """`weigh-station voice-test`: the Weigh Station says its test sentence, as the Hub's `POST /api/voice/test`."""
    client = api() if callable(api) else api
    if client is None:
        output.error(TEXT_NO_SERVICE, "start it, then try again.")
        return output.FAILED
    try:
        client.post("/api/voice/test", {})
    except SlotError as e:
        if e.code == "no_answer":
            output.error(str(e), "check that it is on, then try again.")
        elif e.code == "off":
            output.error(str(e), cmd="sudo spoolwarden config")
        else:
            output.error(str(e))
        return output.FAILED
    played = _played()
    output.emit({"ok": True, "message": played})
    output.result(played)
    return 0


def _played() -> str:
    return catalogue_text("voice_played", "Played on the Weigh Station.")


def speaker_hint() -> str:
    """The line beside the Speaker row."""
    return catalogue_text("voice_speaker_hint", "This voice has 904 speakers. Try a few with Test.")
