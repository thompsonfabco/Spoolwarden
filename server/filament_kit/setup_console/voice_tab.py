"""The Weigh Station's voice settings: the logic behind the
three kit routes (`GET` / `PATCH /api/voice`, `POST /api/voice/test`; api.py only mounts them) and the page's routes.

    GET  station/voice                                        the view read, no code: `get_voice`
    POST station/voice {speech?, speaker?, speed?, volume?}   the values as `get_voice`, or the refusal {error: sentence, code, field}
    POST station/voice/test {speaker?, speed?, volume?}       {} or the refusal {error: sentence, code}

Speak, Speaker, Speed and Volume live in voice.xml (voice_state), written by the kit service only: `save_voice` is the one
writer, for the page route, `PATCH /api/voice`, the web twin and the command line. It writes the file, then sends the
values to the station (`hand_over`); the station also gets them in the answer of its hello (`hello_voice`). A station that
does not answer never stops a save. The root helper and `apply` never read the file.
"""
from __future__ import annotations

import logging
import socket
import urllib.parse

import anyio

from .. import config, station_client, voice_state
from ..station.page import catalogue_text

log = logging.getLogger(__name__)

# the sentences: the string catalogue's, the English as the fallback
_t = catalogue_text
TEXT_VOICE_PLAYED = _t("voice_played", "Played on the Weigh Station.")
TEXT_VOICE_NO_ANSWER = _t("voice_no_answer", "The Weigh Station did not answer.")
TEXT_VOICE_BUSY = _t("voice_busy", "The Weigh Station is in use. Try again when it is idle.")
TEXT_VOICE_OFF = _t("voice_off", "Speaking is switched off.")
TEXT_VOICE_REMOTE = _t("voice_remote", "Chosen on the Weigh Station's own computer: {command} config.")
TEXT_VOICE_APPS_READONLY = _t("voice_apps_readonly", "Change the voice on the Hub page.")
TEXT_VOICE_NO_STATION = _t("voice_no_station", "This Hub has no Weigh Station.")
TEXT_VOICE_NOT_SAVED = _t("voice_not_saved", "The voice settings could not be saved.")

VOICE_MANY = "Piper, many speakers"
VOICE_ONE = "Piper, one clear speaker"
VOICE_ESPEAK = "espeak-ng, robotic, no download"


class VoiceError(Exception):
    """A refusal as the kit routes send it: `{error: code, message}` (and `field`) with `status`."""

    def __init__(self, status: int, code: str, message: str, field: str | None = None):
        super().__init__(message)
        self.status, self.code, self.message, self.field = status, code, message, field


def station_url(cfg: dict) -> str | None:
    st = cfg.get("station")
    url = st.get("url") if isinstance(st, dict) else None
    return url if isinstance(url, str) and url.strip() else None


def _this_computer(host: str) -> bool:
    """Does `host` name this computer: its name (also as `<name>.local` or the full name) or one of its addresses."""
    name = socket.gethostname().lower()
    if host in (name, name + ".local", socket.getfqdn().lower()):
        return True
    try:
        return host in socket.gethostbyname_ex(name)[2]
    except OSError:
        return False


def station_remote(cfg: dict) -> bool:
    """True when the Weigh Station is on another computer than the Hub (its address is not this computer)."""
    try:
        host = (urllib.parse.urlsplit(station_url(cfg) or "").hostname or "").lower()
    except ValueError:
        return False
    if not host:
        return False
    return not (host in ("localhost", "::1") or host.startswith("127.") or _this_computer(host))


def voice_name(cfg: dict) -> str:
    """The Voice row's choice, as shown."""
    st = cfg.get("station")
    st = st if isinstance(st, dict) else {}
    if st.get("tts") == "espeak":
        return VOICE_ESPEAK
    return VOICE_ONE if st.get("voice") == voice_state.ONE_CLEAR else VOICE_MANY


def voice_row(cfg: dict) -> dict:
    """The Voice row for the Hub page: `{value, editable, text}`; a station on another computer chooses its voice there."""
    remote = station_remote(cfg)
    return {"value": voice_name(cfg), "editable": not remote, "text": TEXT_VOICE_REMOTE if remote else None}


def hello_voice(cfg: dict) -> dict:
    """`voice` in the answer of `GET /api/station/hello`: `{speech, speaker, speed, volume}` from voice.xml."""
    return voice_state.values(cfg)


def hand_over(cfg: dict, only: tuple | list | None = None) -> bool:
    """Send the values to the station (`POST /voice`); True when it took them. False when no station is set or it does
    not answer or refuses them: the values are saved either way."""
    url = station_url(cfg)
    if not url:
        return False
    values = voice_state.values(cfg)
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
    """`GET /api/voice`; `station: false` when the Hub has no Weigh Station (the rest is then left out). The values are
    voice.xml's; `voice` (the voice and engine as a person sees them), `remote` (the Weigh Station runs on another
    computer) and `speaker_range` come from the settings, read-only. The Hub page's group reads only this answer."""
    if not station_url(cfg):
        return {"station": False}
    name = voice_name(cfg)
    return {"station": True, **voice_state.values(cfg), "voice": name,
            "remote": station_remote(cfg), "speaker_range": voice_state.speaker_range(cfg)}


def check_values(body, cfg: dict) -> dict:
    """The fields of a save or a test, checked: `{field: value}` for `speech`, `speaker`, `speed`, `volume` (a field
    that is not sent stays out). VoiceError 400 (with `field`) for a value out of range or of another type."""
    try:
        return voice_state.check(body, cfg)
    except voice_state.BadValue as e:
        raise VoiceError(400, "bad_value" if e.field else "bad_request", str(e), field=e.field or None) from None


def save_voice(body, *, load=None) -> dict:
    """The one writer: check every field, write voice.xml, hand the values to the station, answer as
    `get_voice`. Nothing is written when a field is bad. Used by `PATCH /api/voice`, its twin, the page route and the
    command line."""
    cfg = (load or config.load)()
    new = check_values(body, cfg)
    if not station_url(cfg):
        raise VoiceError(409, "no_station", TEXT_VOICE_NO_STATION)
    if new:
        if not voice_state.save(new, cfg):
            raise VoiceError(500, "not_saved", TEXT_VOICE_NOT_SAVED)
        hand_over(cfg, tuple(new))
    return get_voice(cfg)


patch_voice = save_voice        # `PATCH /api/voice`


def play_test(cfg: dict, body) -> None:
    """`POST /api/voice/test`: the station says its one sentence. VoiceError 409 `busy` / `off`, 502 `no_answer`."""
    values = {k: v for k, v in check_values(body, cfg).items() if k != "speech"}
    if not station_url(cfg):
        raise VoiceError(409, "no_station", TEXT_VOICE_NO_STATION)
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


# ---- the kit routes, for api.py to mount ----

def kit_routes(authed) -> list:
    """`GET` / `PATCH /api/voice` and `POST /api/voice/test` as Starlette routes, each wrapped in api.py's `_authed`. The
    service is the one writer: the apps and the command line (`spoolwarden config` runs as root and calls the PATCH over
    loopback with its own token, `slots.KitApi.patch`) never write voice.xml themselves. api.py mounts them with
    `*voice_tab.kit_routes(_authed)`."""
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    def refusal(e: VoiceError):
        extra = {"field": e.field} if e.field else {}
        return JSONResponse({"error": e.code, "message": e.message, **extra}, status_code=e.status)

    async def body_of(request):
        try:
            return await request.json()
        except ValueError:
            return None             # `check` refuses it as "the request must be a JSON object"

    async def get(request):
        return JSONResponse(await anyio.to_thread.run_sync(lambda: get_voice(config.load())))

    async def patch(request):
        body = await body_of(request)
        try:
            return JSONResponse(await anyio.to_thread.run_sync(lambda: patch_voice(body)))
        except VoiceError as e:
            return refusal(e)

    async def test(request):
        body = await body_of(request)
        try:
            await anyio.to_thread.run_sync(lambda: play_test(config.load(), body))
        except VoiceError as e:
            return refusal(e)
        return JSONResponse({})

    return [Route("/api/voice", authed(get)), Route("/api/voice", authed(patch), methods=["PATCH"]),
            Route("/api/voice/test", authed(test), methods=["POST"])]


# ---- the Hub page's route ----

def routes(page) -> list[tuple]:
    from ..setup_page import Refused

    def refuse(e: VoiceError):
        extra = {"code": e.code}
        if e.field:
            extra["field"] = e.field
        return Refused(e.status, e.message, **extra)

    async def save(request, body):
        try:
            return await anyio.to_thread.run_sync(lambda: save_voice(body))
        except VoiceError as e:
            raise refuse(e) from None

    async def test(request, body):
        try:
            await anyio.to_thread.run_sync(lambda: play_test(config.load(), body))
        except VoiceError as e:
            raise refuse(e) from None
        return {}

    async def view(request, body):
        return await anyio.to_thread.run_sync(lambda: get_voice(config.load()))

    return [("/station/voice", page.guard(view), "GET"),
            ("/station/voice", page.guard(save, write=True), "POST"),
            ("/station/voice/test", page.guard(test, write=True), "POST")]
