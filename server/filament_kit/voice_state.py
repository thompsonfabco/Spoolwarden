"""The Weigh Station's four voice values: Speak, Speaker, Speed, Volume.

One XML file, `voice.xml`, in the Hub's STATE folder:

    <voice version="1" speech="true" speaker="3" speed="100" volume="50"/>      (an attribute only for a value that was saved)

They are not settings. Owned like firstrun.xml: the state folder's owner, mode 644, written by a temporary file and a
rename under a lock, never through a symbolic link, by the kit service only (the Hub page's route, `PATCH /api/voice`,
the web twin, the command line: all through `save`). Nothing in it feeds a root decision: `apply`, the root helper, the
installer and the updater never read it. The service checks every value again on every read: one that is out of range, or
a file that is missing, a link, too big or not valid, reads as the default.

Where the file has no value: Speak and Volume are the settings `station.speech` and `station.speaker_volume` (the
station's start values, written by root only), Speaker is 0 and Speed 100.
"""
from __future__ import annotations

import errno
import logging
import os
import re
import stat
from pathlib import Path
from xml.sax.saxutils import quoteattr

import defusedxml.ElementTree as DET
from defusedxml import DefusedXmlException

from . import config, locks, pairing
from .profiles import statexml

log = logging.getLogger("filament_kit.voice_state")

FILE = "voice.xml"
VERSION = "1"
MODE = 0o644
FIELDS = ("speech", "speaker", "speed", "volume")
VOICE_CHOICE_FIELDS = ("tts", "voice")
TTS_CHOICES = ("auto", "espeak")
VOICE_MODEL_CHOICES = ("en_US-libritts_r-medium", "en_US-ljspeech-medium")

SPEAKER_MAX = 903                   # a many-speaker Piper voice: 904 speakers
SPEED_MIN, SPEED_MAX, SPEED_STEP = 50, 200, 10
DEFAULT_SPEAKER, DEFAULT_SPEED = 0, 100
ONE_CLEAR = "en_US-ljspeech-medium"

_LOCK = locks.ProcessLock(lambda: config.paths().state / "voice.lock")


class BadValue(ValueError):
    """A value that is not allowed; `field` is its name and the message says what is."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def voice_selection(cfg: dict) -> tuple[str | None, str | None]:
    """Read optional `tts` and `voice` overrides from `voice.xml`, returning `(None, None)` if missing or invalid."""
    try:
        raw = _read() or {}
    except (OSError, ValueError):
        return None, None
    tts = raw.get("tts")
    voice = raw.get("voice")
    return (
        tts if isinstance(tts, str) and tts in TTS_CHOICES else None,
        voice if isinstance(voice, str) and voice in VOICE_MODEL_CHOICES else None,
    )


def _many_speakers_for(cfg: dict, tts: str | None, voice: str | None) -> bool:
    st = cfg.get("station")
    st = st if isinstance(st, dict) else {}
    eff_tts = tts if tts is not None else st.get("tts")
    eff_voice = voice if voice is not None else st.get("voice")
    return eff_tts != "espeak" and eff_voice != ONE_CLEAR


def many_speakers(cfg: dict) -> bool:
    """Does the chosen voice (voice.xml override, else `station.tts`, `station.voice`) have many speakers."""
    tts, voice = voice_selection(cfg)
    return _many_speakers_for(cfg, tts, voice)


def speaker_range(cfg: dict) -> list | None:
    """`[0, 903]` for a many-speaker voice, None for a voice with one speaker."""
    return [0, SPEAKER_MAX] if many_speakers(cfg) else None


def _whole(v) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _problem(field: str, v, cfg: dict) -> str | None:
    """Why `v` is not allowed for `field` (None when it is)."""
    if field == "speech":
        return None if isinstance(v, bool) else "must be true or false"
    if field == "speaker":
        top = SPEAKER_MAX if many_speakers(cfg) else 0
        return None if _whole(v) and 0 <= v <= top else f"must be a whole number from 0 to {top}"
    if field == "speed":
        ok = _whole(v) and SPEED_MIN <= v <= SPEED_MAX and v % SPEED_STEP == 0
        return None if ok else f"must be from {SPEED_MIN} to {SPEED_MAX} in steps of {SPEED_STEP}"
    if field == "volume":
        return None if _whole(v) and 0 <= v <= 100 else "must be a whole number from 0 to 100"
    if field == "tts":
        return None if isinstance(v, str) and v in TTS_CHOICES else f"must be one of {', '.join(TTS_CHOICES)}"
    if field == "voice":
        return None if isinstance(v, str) and v in VOICE_MODEL_CHOICES else f"must be one of {', '.join(VOICE_MODEL_CHOICES)}"
    return "is not a voice value"


def check(body, cfg: dict) -> dict:
    """The fields of a save or a test, checked against the chosen voice: `{field: value}` (a field not sent stays out;
    a name that is not allowed is ignored). BadValue for a value that is not allowed."""
    if not isinstance(body, dict):
        raise BadValue("", "the request must be a JSON object")
    allowed = FIELDS + VOICE_CHOICE_FIELDS
    cur_tts, cur_voice = voice_selection(cfg)
    eff_tts = body["tts"] if body.get("tts") in TTS_CHOICES else cur_tts
    eff_voice = body["voice"] if body.get("voice") in VOICE_MODEL_CHOICES else cur_voice
    eff_many = _many_speakers_for(cfg, eff_tts, eff_voice)
    out = {}
    for f, v in body.items():
        if f not in allowed:
            continue
        if f == "speaker":
            top = SPEAKER_MAX if eff_many else 0
            why = None if _whole(v) and 0 <= v <= top else f"must be a whole number from 0 to {top}"
        else:
            why = _problem(f, v, cfg)
        if why:
            raise BadValue(f, f"{f} {why}")
        out[f] = v
    return out


def fallbacks(cfg: dict) -> dict:
    """The values where the file has none: Speak and Volume from the settings, Speaker 0, Speed 100."""
    st = cfg.get("station")
    st = st if isinstance(st, dict) else {}
    d = config.DEFAULTS["station"]
    speech, volume = st.get("speech"), st.get("speaker_volume")
    return {"speech": speech if isinstance(speech, bool) else d["speech"],
            "speaker": DEFAULT_SPEAKER, "speed": DEFAULT_SPEED,
            "volume": volume if _problem("volume", volume, cfg) is None else d["speaker_volume"]}


def _file() -> Path:
    return config.paths().state / FILE


def _from_xml(data: bytes) -> dict:
    root = DET.fromstring(data)
    if root.tag != "voice":
        raise ValueError("not a voice file")
    got = {}
    for f in FIELDS + VOICE_CHOICE_FIELDS:
        raw = root.get(f)
        if raw is None:
            continue
        raw = raw.strip()
        if f == "speech":
            got[f] = raw == "true" if raw in ("true", "false") else None
        elif f in VOICE_CHOICE_FIELDS:
            choices = TTS_CHOICES if f == "tts" else VOICE_MODEL_CHOICES
            got[f] = raw if raw in choices else None
        else:
            got[f] = int(raw) if re.fullmatch(r"[0-9]{1,5}", raw) else None
    return got


def _read() -> dict | None:
    """The attributes as written, None when there is no file. Raises OSError or ValueError for one that cannot be read."""
    f = _file()
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(f, flags)
    except FileNotFoundError:
        return None
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise ValueError(f"{f.name} is a symbolic link") from None
        raise
    with os.fdopen(fd, "rb") as fh:
        data = fh.read(65536)
    try:
        return _from_xml(data)
    except (DET.ParseError, DefusedXmlException, UnicodeDecodeError) as e:
        raise ValueError(f"{f.name} is not valid: {e}") from None


def _stored_all(cfg: dict) -> dict:
    """All valid attributes (the four voice values plus any `tts`/`voice` overrides) from voice.xml."""
    try:
        raw = _read() or {}
    except (OSError, ValueError) as e:
        log.warning("voice file not read, the defaults are used: %s", e)
        return {}
    return {f: v for f, v in raw.items() if v is not None and _problem(f, v, cfg) is None}


def stored(cfg: dict) -> dict:
    """The values the file holds that are allowed now; never raises. A broken file holds none."""
    return {f: v for f, v in _stored_all(cfg).items() if f in FIELDS}


def values(cfg: dict) -> dict:
    """`{speech, speaker, speed, volume}`: the file's value where it has an allowed one, else the fallback."""
    return {**fallbacks(cfg), **stored(cfg)}


def handover_values(cfg: dict) -> dict:
    """`values(cfg)` plus `tts` / `voice` when set in `voice.xml`, for `hello_voice` and `hand_over`."""
    out = values(cfg)
    tts, voice = voice_selection(cfg)
    if tts is not None:
        out["tts"] = tts
    if voice is not None:
        out["voice"] = voice
    return out


def _to_xml(got: dict) -> str:
    def fmt(v) -> str:
        return str(v).lower() if isinstance(v, bool) else str(v)

    attrs = "".join(f" {f}={quoteattr(fmt(got[f]))}" for f in FIELDS + VOICE_CHOICE_FIELDS if f in got)
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<voice version={quoteattr(VERSION)}{attrs}/>\n'


def _write(got: dict) -> None:
    f = _file()
    try:
        if stat.S_ISLNK(os.lstat(f).st_mode):
            raise OSError(errno.ELOOP, f"{f.name} is a symbolic link")
    except FileNotFoundError:
        pass
    pairing.make_state_dir(f.parent)
    pairing._write_atomic(f, _to_xml(got).encode("utf-8"), MODE, state_owned=True)


def _set_aside() -> None:
    """A voice.xml that is not valid (XML or schema) is kept as `voice.xml.unreadable` and noted, never written
    over. A symbolic link is not moved: `_write` refuses it. A passing read error (OSError: a lock, a permission blip)
    does not move a good file: it is raised, `save` writes nothing and says False."""
    f = _file()
    try:
        if stat.S_ISLNK(os.lstat(f).st_mode):
            return
        _read()
    except FileNotFoundError:
        return
    except ValueError as e:
        kept = f.with_name(f.name + ".unreadable")
        os.replace(f, kept)
        statexml.note_problem(kept, f"{f.name} could not be read and is kept aside ({e})")


def save(new: dict, cfg: dict) -> bool:
    """Write `new` (already checked, `check`) over what the file holds, the other values kept. A file that cannot be read
    is kept aside as `voice.xml.unreadable` first, never overwritten. False when the file could not be written (a
    symbolic link, no room, no right); never raises."""
    try:
        with _LOCK:
            _set_aside()
            allowed = FIELDS + VOICE_CHOICE_FIELDS
            _write({**_stored_all(cfg), **{f: v for f, v in new.items() if f in allowed}})
        return True
    except OSError as e:
        log.warning("voice file not written: %s", e)
        return False
