"""HTTPS JSON API for the phone app.

Every route but /api/info, POST /api/pair and /app.apk needs `Authorization: Bearer <token>`; the intake station's token
(`pair --station`) reaches only /api/station/*, the web unit's only WEB_ROUTES (403 elsewhere). Errors are `{"error": <code>, "message": <text>}`;
tracebacks are logged here and never sent.
"""
from __future__ import annotations

import functools
import json
import logging
import math
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from . import (API_VERSION, __version__, address_texts, alerts_api, analytics, config, csv_io, demo, event_sink, events,
               health, hostaddr, hub_view, intake, intake_history, inventory, labels, modules, pairing, profiles_api,
               reader_corrections, reader_tokens, setup_page, station_flow, station_requests, storage, storage_api, tags, undo)
from .intake import CapReached, PhotoError, ReadCap, ReadError, RefundRefused
from .bridge import auto_slot, camera, family, hardware, loading, mapping, pending, registry, usage
from .bridge.klipper import discovery
from .bridge.connection import ACTIONS, DEVELOPER_MODE_REASON, Manager, NoReply, PrinterOffline, UnknownPrinter
from .bridge.slottest import NoStatus, PrinterBusy
from .bridge.models import camera_kind, model_name
from .inventory import AddFailed, TagConflict
from .profiles.errors import ProfileError
from .setup_console import voice_tab
from .spoolman import Spoolman, SpoolmanError
from .station_client import StationClient, StationUnreachable

log = logging.getLogger(__name__)

MAX_HINT = 2000
APK_TYPE = "application/vnd.android.package-archive"


class BadRequest(ValueError):
    """A refusal of the request; `field` names the body key when it is about that one field (an optional extra key of the
    reply, for a form to show the text beside the field)."""

    def __init__(self, message: str = "", field: str | None = None):
        super().__init__(message)
        self.field = field


def _err(status: int, code: str, message: str, **extra) -> JSONResponse:
    return JSONResponse({"error": code, "message": message, **extra}, status_code=status)


def _http_error(e: HTTPException) -> JSONResponse:
    code = {404: "not_found", 405: "method_not_allowed"}.get(e.status_code, "bad_request")
    return _err(e.status_code, code, str(e.detail))


def _to_response(e: Exception) -> JSONResponse:
    if isinstance(e, HTTPException):  # e.g. a malformed or oversized multipart body from request.form()
        return _http_error(e)
    if isinstance(e, demo.DemoError):      # a stop of the demo (409), or a demo refusal (400)
        return _err(409 if isinstance(e, demo.NotInDemo) else 400, e.code, e.message)
    if isinstance(e, ProfileError):
        if e.status >= 500:
            log.error("profiles error %s", e.code, exc_info=e)
        return _err(e.status, e.code, e.message, **e.extra)
    if isinstance(e, PhotoError):
        return _err(400, "bad_photo", str(e))
    if isinstance(e, CapReached):
        return _err(429, "daily_cap", str(e))
    if isinstance(e, RefundRefused):
        return _err(409, "refund_refused", str(e))
    if isinstance(e, ReadError):
        return _err(404 if e.code == "not_found" else 502, e.code, e.message, read_id=e.read_id)
    if isinstance(e, TagConflict):
        return _err(409, "tag_conflict", str(e), reason=e.reason, other_spool=e.other)
    if isinstance(e, inventory.NotCheckedOut):      # a return, or a home change, of a spool that is not checked out
        return _err(409, "not_checked_out", str(e))
    if isinstance(e, AddFailed):
        return _err(502, "spoolman_error", str(e), partial_spool_ids=e.partial.spool_ids)
    if isinstance(e, SpoolmanError):
        if e.status is None:
            return _err(503, "spoolman_unreachable",
                        "Spoolman is not reachable; check that the spoolman service is running")
        if e.status == 404:
            return _err(404, "not_found", f"not found in Spoolman: {e}")
        return _err(502, "spoolman_error", str(e))
    bridge = _bridge_error(e)
    if bridge is not None:
        return bridge
    if isinstance(e, ValueError):
        field = getattr(e, "field", None)
        problems = getattr(e, "problems", None)         # a refused add: every problem, with its item and field
        return _err(400, "bad_request", str(e), **({"field": field} if isinstance(field, str) else {}),
                    **({"problems": problems} if isinstance(problems, list) else {}))
    log.error("internal error", exc_info=e)
    return _err(500, "internal", "internal error")


OFFLINE = "the printer is offline; check that it is on and on the network"
CERT_CHANGED = camera.CERT_CHANGED   # one text for the API and the camera


def _bridge_error(e: Exception) -> JSONResponse | None:
    """Slot mapping, loading and usage errors (route-contract.md); None for anything else."""
    if isinstance(e, auto_slot.AutoError):
        return _err(e.status, e.code, e.message)
    if isinstance(e, usage.UsageError):
        return _err(e.status, e.code, str(e))
    if isinstance(e, mapping.AlreadyRunning):   # also LoadRunning: a load is writing to the printer
        return _err(409, "already_running", e.message)
    if isinstance(e, mapping.CertChanged):
        return _err(409, "cert_changed", CERT_CHANGED)
    if isinstance(e, PrinterBusy):
        return _err(409, "printer_busy", f"the printer is busy ({e}); try again once it is idle")
    if isinstance(e, NoStatus):
        return _err(409, "printer_offline", str(e))
    if isinstance(e, PrinterOffline):
        return _err(409, "printer_offline", OFFLINE)
    if isinstance(e, loading.SlotInUse):
        return _err(409, "slot_in_use", str(e))
    if isinstance(e, loading.NoPreset):
        return _err(409, "no_preset", str(e))
    if isinstance(e, UnknownPrinter):
        return _err(404, "not_found", "no such printer")
    if isinstance(e, loading.NotFound):
        return _err(404, "not_found", str(e))
    if isinstance(e, family.ModuleOff):
        return _module_off(e.family)
    if isinstance(e, family.NotSupported):
        return _err(409, "not_supported", str(e) or "this printer does not support that")
    if isinstance(e, family.RecordedByMoonraker):
        return _err(409, "recorded_by_moonraker", "Moonraker recorded this usage in Spoolman; correct it in "
                    "Spoolman (the kit cannot undo it)")
    return None


def _module_off(module: str) -> JSONResponse:
    return _err(409, "module_off", f"the {module} module is turned off on this server", module=module)


def _gated(routes: list[Route], module: str, on: frozenset[str]) -> list[Route]:
    """`routes` as they are when `module` is on; otherwise the same paths and methods, each answering 409
    `module_off` (after the usual token check, so an unpaired caller still gets 401)."""
    if module in on:
        return routes

    @_authed
    async def off(request: Request):
        return _module_off(module)
    return [Route(r.path, off, methods=sorted(r.methods or ())) for r in routes]


def _handle(fn):
    @functools.wraps(fn)
    async def wrapper(request: Request):
        try:
            return await fn(request)
        except Exception as e:  # noqa: BLE001 - every failure becomes a JSON error
            return _to_response(e)
    return wrapper


STATION_PREFIX = "/api/station/"  # all a `pair --station` token may call (and /api/info, which needs none)
# All the web unit's token may call: exact method and route, because /api/printers/... mixes what its pages use with
# registry writes. A new route is refused until it is listed here. test_web_token_scope.py holds this to the app's
# routes and to what filament_kit/web calls.
WEB_ROUTES = (
    ("GET", "/api/printers"),
    ("POST", "/api/printers"),
    ("POST", "/api/printers/scan"),     # the web app's Add printer search
    ("POST", "/api/printers/test"),     # test a Klipper printer before adding (under printer_changes)
    ("GET", "/api/printers/{id}"),
    ("PATCH", "/api/printers/{id}"),
    ("DELETE", "/api/printers/{id}"),
    ("GET", "/api/printers/{id}/usage"),
    ("GET", "/api/printers/{id}/map"),
    ("POST", "/api/printers/{id}/map"),
    ("POST", "/api/printers/{id}/report"),
    ("GET", "/api/printers/{id}/manual-map"),
    ("POST", "/api/printers/{id}/manual-map/start"),
    ("POST", "/api/printers/{id}/manual-map/confirm"),
    ("POST", "/api/printers/{id}/manual-map/skip"),
    ("POST", "/api/printers/{id}/manual-map/stop"),
    ("POST", "/api/printers/{id}/manual-map/offer"),
    ("POST", "/api/printers/{id}/command"),
    ("POST", "/api/printers/{id}/slots/auto/load"),
    ("POST", "/api/printers/{id}/auto/choose"),
    ("POST", "/api/printers/{id}/auto/cancel"),
    ("POST", "/api/printers/{id}/slots/{label}/load"),
    ("POST", "/api/printers/{id}/slots/{label}/unload"),
    ("POST", "/api/printers/{id}/slots/{label}/clear"),
    ("GET", "/api/printers/{id}/camera.mjpeg"),
    ("GET", "/api/printers/{id}/camera/{name:path}"),
    ("POST", "/api/usage/{entry_id}/undo"),
    ("POST", "/api/intake/cap/take"),
    ("POST", "/api/intake/cap/refund"),
    ("GET", "/api/intake/orders"),
    ("GET", "/api/intake/share-corrections"),
    ("GET", "/api/library/local"),
    ("POST", "/api/library/local/form"),
    ("DELETE", "/api/library/local/{id}"),
    ("POST", "/api/readers/web/seen"),
    ("POST", "/api/profiles/web/line_add"),
    ("POST", "/api/profiles/web/line_new"),
    ("POST", "/api/profiles/web/generate"),
    ("POST", "/api/profiles/web/updates_accept"),
    ("POST", "/api/profiles/web/restore_deleted"),
    ("GET", "/api/events"),
    ("POST", "/api/events/web"),
    ("POST", "/api/events/{id}/undo"),
    ("GET", "/api/alerts"),
    ("POST", "/api/alerts/test"),
    ("GET", "/api/spools/low"),
    ("GET", "/api/storage-use"),
    ("GET", "/api/health"),
    ("GET", "/api/analytics"),
    ("GET", "/api/export/{set}.csv"),
    ("GET", "/api/hub"),
    ("GET", "/api/voice"),
    ("GET", "/api/spools/{id:int}/label.svg"),
    ("POST", "/api/spools/{id}/return"),    # the web app's Return (the spool-change rule: no printer switch)
)
_WEB_MATCH = {(m, re.compile(re.sub(r"\{\w+(?::int)?\}", "[^/]+", re.sub(r"\{\w+:path\}", ".+", r)))) for m, r in WEB_ROUTES}
HOME_WEB_ROUTES = (("PATCH", "/api/voice"), ("POST", "/api/voice/test"))
WEB_ONLY_PREFIX = "/api/profiles/web/"  # the web unit's preset writes: no phone or PC calls them
WEB_ONLY_PATHS = ("/api/events/web", "/api/readers/web/seen")   # the web unit's hand-over of its spool events and seen readers

VIEWER_ROUTES = (
    ("GET", "/api/info"),
    ("GET", "/api/hub"),
    ("GET", "/api/voice"),
    ("GET", "/api/events"),
    ("GET", "/api/alerts"),
    ("GET", "/api/printers"),
    ("GET", "/api/printers/{id}"),
    ("GET", "/api/printers/{id}/usage"),
    ("GET", "/api/printers/{id}/camera.mjpeg"),
    ("GET", "/api/printers/{id}/camera/{name:path}"),
    ("GET", "/api/spools"),
    ("GET", "/api/spools/{id}"),
    ("GET", "/api/spools/low"),
    ("GET", "/api/storage"),
    ("GET", "/api/storage-use"),
    ("GET", "/api/health"),
    ("GET", "/api/analytics"),
)
_VIEWER_MATCH = {(m, re.compile(re.sub(r"\{\w+\}", "[^/]+", re.sub(r"\{\w+:path\}", ".+", r)))) for m, r in VIEWER_ROUTES}


MAX_IMPORT_BYTES = 5 * 1024 * 1024      # a CSV import's body, as on the Hub page (setup_console/history_tab.py)


def _current_checks() -> list[dict]:
    """The Overview's rows as the health history knows them: `{key, group, text, state}`, redacted as `GET api/hub` does,
    so a check that never failed shows with its group. An empty list when the rows cannot be read: the history stands alone."""
    from . import redact, status
    try:
        cfg = config.load()
        known = redact.known_values(cfg)
        return [{"key": r.key, "group": r.group, "text": redact.text(r.text, known),
                 "state": "ok" if r.state == status.OK else "look"}
                for r in status.collect(cfg) if r.key and r.state in (status.OK, status.FAIL)]
    except Exception:  # noqa: BLE001 - the history is still worth showing
        log.exception("health: the current checks cannot be read")
        return []


def _device_name(device: str | None) -> str:
    """The paired device's name for a log or a history record (its id when it has none)."""
    if not device:
        return ""
    try:
        return next((d.get("name") or device for d in pairing.devices() if d.get("id") == device), device)
    except Exception:  # noqa: BLE001 - a name is a courtesy
        return device


def _is_intake_device(device: str) -> bool:
    """The token is the kit's own phone web app's pairing (INTAKE_DEVICE), not another web unit's."""
    try:
        return any(d["id"] == device and d["name"] == INTAKE_DEVICE for d in pairing.devices())
    except Exception:  # noqa: BLE001
        return False


def _actor_name(actor: dict, device: str) -> str:
    """The name of the device an event is written for, looked up when the first event is written (`event_sink.acting`).
    The command line's own pairing (CLI_DEVICE, the one `spoolwarden` asks this server with) writes as `cli`:
    the actor becomes {type: cli} here, before the event takes it."""
    name = _device_name(device)
    if name == CLI_DEVICE:
        actor.pop("id", None)
        actor["type"] = "cli"
        return ""
    return name


def _web_may(method: str, path: str) -> bool:
    return any(m == method and rx.fullmatch(path) for m, rx in _WEB_MATCH)


def _home_web_may(method: str, path: str) -> bool:
    return (method, path) in HOME_WEB_ROUTES


def _viewer_may(method: str, path: str) -> bool:
    return any(m == method and rx.fullmatch(path) for m, rx in _VIEWER_MATCH)


# The printer writes a web token may make (add, edit, forget). The kit's own phone web app (the built-in pairing
# INTAKE_DEVICE, home network only) always may; the web module's (its public address) only while the owner's switch
# "Allow printer changes from the public address" (config web.printer_changes) is on. The web module checks the same
# switch; this holds it even if that internet-facing process does not.
_PRINTER_WRITE = re.compile(r"/api/printers(?:/[^/]+)?")
PUBLIC_PRINTER_CHANGES_OFF = ("Printer changes from the public address are off. Turn on \"Allow printer changes from "
                              "the public address\" on the setup page (Settings, General), or use the page on the home "
                              "network.")


def _printer_write(method: str, path: str) -> bool:
    return ((method == "POST" and path in ("/api/printers", "/api/printers/test"))
            or (method in ("PATCH", "DELETE") and path != "/api/printers" and bool(_PRINTER_WRITE.fullmatch(path))))


def _web_printer_changes_ok(device: str) -> bool:
    if (config.load().get("web") or {}).get("printer_changes") is True:
        return True
    return any(d["id"] == device and d["name"] == INTAKE_DEVICE for d in pairing.devices())


def _authed(fn):
    @functools.wraps(fn)
    async def wrapper(request: Request):
        header = request.headers.get("authorization", "")
        scheme, _, tok = header.partition(" ")
        got = None
        if scheme.lower() == "bearer" and tok.strip():
            got = await run_in_threadpool(pairing.authorize, tok.strip(), request.headers.get("x-spoolwarden-client"))
        if got is None:
            return _err(401, "unauthorized", "pair this phone with the server first (missing or revoked token)")
        device, scope = got
        if scope == "station" and not request.url.path.startswith(STATION_PREFIX):
            return _err(403, "forbidden", "the intake station's token is for the station's routes only")
        # the path the router matched, not the URL's raw text
        path = request.scope["path"]
        if (scope == "web" and not _web_may(request.method, path)
                and not (_home_web_may(request.method, path) and await run_in_threadpool(_is_intake_device, device))):
            return _err(403, "forbidden", "the web unit's token is for the web unit's routes only")
        if scope == "viewer" and not _viewer_may(request.method, path):
            return _err(403, "forbidden", "a viewer token may only read Hub status, printers, spools and health")
        if (scope == "web" and _printer_write(request.method, path)
                and not await run_in_threadpool(_web_printer_changes_ok, device)):
            return _err(403, "public_printer_changes_off", PUBLIC_PRINTER_CHANGES_OFF)
        if scope != "web" and (path.startswith(WEB_ONLY_PREFIX) or path in WEB_ONLY_PATHS):
            return _err(403, "forbidden", "this route is for the web unit only")
        request.state.device = device
        # what this request changes is written as an event done by this device; only a change writes one
        # the name is looked up when an event is written, not for every request (pairing.devices() reads a file)
        actor = {"type": "device", "id": device}
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            actor["name"] = functools.partial(_actor_name, actor, device)
        with event_sink.acting(actor):
            return await fn(request)
    return _handle(wrapper)


async def _json(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise BadRequest("the request body must be JSON") from None
    if not isinstance(body, dict):
        raise BadRequest("the request body must be a JSON object")
    return body


_KINDS = {bool: "true or false", float: "a number", str: "text"}


def _opt(body: dict, key: str, kind: type, default=None):
    """Optional body field of `kind` (bool, float = any JSON number, or str); None/absent gives `default`."""
    v = body.get(key)
    if v is None:
        return default
    if kind is float:
        ok = isinstance(v, (int, float)) and not isinstance(v, bool)
    else:
        ok = isinstance(v, kind)
    if not ok:
        raise BadRequest(f"{key} must be {_KINDS[kind]}", key)
    return v


def _apk_version() -> str | None:
    f = config.paths().opt / "app" / "version.txt"
    try:
        return f.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


# ---- printer routes --------------------------------------------------------------------------------------
FIRST_STATE_WAIT = 8.0  # seconds POST /api/printers waits for the first connection result


_UNSET = object()


def _printer_view(p: registry.Printer, printers, *, st=_UNSET) -> dict:
    """The public shape of a printer. Built field by field so the Access Code can never leak."""
    state = "not_configured"
    if p.configured:
        state = printers.state(p.serial)
        if state == "not_configured":  # configured, but the manager has no connection for it (yet)
            state = "offline"
    try:
        summary = printers.summary(p.serial)
    except Exception:  # noqa: BLE001 - one odd report must not fail the whole printer list
        log.exception("printer %s: cannot summarize its report", p.serial)
        summary = None
    if st is _UNSET:
        try:
            st = printers.status(p.serial) if hasattr(printers, "status") else None
        except Exception:  # noqa: BLE001 - one odd report must not fail the whole printer list
            log.exception("printer %s: cannot read its status", p.serial)
            st = None
    auto_possible = bool(st["auto_possible"]) if isinstance(st, dict) and "auto_possible" in st else auto_slot.is_auto_possible(st)
    auto = st["auto"] if isinstance(st, dict) and "auto" in st else auto_slot.default_store().active_view(p.serial)
    fam = getattr(p, "family", "bambu")
    view = {"id": p.serial, "name": registry.display_name(p), "model": p.model, "serial": p.serial, "ip": p.ip,
            "configured": p.configured, "state": state, "camera": {"kind": camera_kind(p.model)},
            "model_name": model_name(p.model),
            "summary": summary, "family": fam, "pinned": bool(p.cert_fp),   # a certificate is pinned, never which
            "auto_possible": auto_possible, "auto": auto}
    if fam == "klipper":
        kf = getattr(printers, "klipper", None)
        view["host"] = p.host
        view["verified"] = bool(kf.verified(p.serial)) if kf is not None else None
        view["camera"] = {"kind": kf.camera_kind(p.serial) if kf is not None else None}
        view["model_name"] = _klipper_model_name(p, kf)
    return view


def _klipper_model_name(p: registry.Printer, kf) -> str:
    """A Klipper printer's name from klipper_hardware.xml ("Sovol SV08 MAX"); the model text when nothing matched."""
    try:
        m = kf.model(p.serial)[0] if kf is not None else None
    except Exception:  # noqa: BLE001 - an unreadable detection must not fail the printer list
        m = None
    return m.name if m is not None and m.key != "generic" else (p.model or "").strip()


def _hardware_view() -> dict:
    """The printer hardware data files' state (`bambu_hardware.xml`, `klipper_hardware.xml`): errors = a refused
    override (the last good data stays in use), warnings = unknown elements/attributes that were ignored. Each entry
    names its file."""
    from .bridge.klipper import hardware as klipper_hardware
    out = {"errors": [], "warnings": []}
    for module in (hardware, klipper_hardware):
        try:
            hw = module.load()
        except Exception as e:  # noqa: BLE001 - the status must still answer
            log.exception("hardware data cannot be loaded")
            out["errors"].append(f"{type(e).__name__}: {e}")
            continue
        out["errors"] += hw.errors
        out["warnings"] += hw.warnings
    return out


PATCH_KEYS = frozenset({"name", "address", "port", "access_code", "api_key", "unpin"})
# What a paired device may not do alone: make the server send a secret it holds to a host the device
# names. A Bambu address change keeps the certificate pin, so the stored Access Code only goes to the same printer.
NEEDS_CODE_TO_UNPIN = ("trusting a new certificate needs the printer's Access Code in the same request "
                       "(access_code): the stored one is never sent to a certificate the kit hasn't seen")
NEEDS_CODE_TO_MOVE = ("this printer has no certificate pinned yet: give its Access Code (access_code) with the new "
                      "address, so the stored one is never sent to whatever answers there")
NEEDS_KEY_TO_MOVE = ("this printer has a Moonraker API key stored: give it (api_key, or \"\" to clear it) with the "
                     "new address or port, so the stored key is never sent to whatever answers there")


class _ModuleOffError(Exception):
    def __init__(self, fam: str):
        super().__init__(fam)
        self.family = fam


class _Conflict(Exception):
    pass


NOT_SEEN = "That printer hasn't been seen on the network yet"
HOST = re.compile(r"[A-Za-z0-9.:\[\]-]{1,253}")   # a host name, IPv4 or bracketed IPv6


def _printer_routes(printers) -> list[Route]:
    def known(serial: str) -> registry.Printer:
        p = registry.load().get(serial)
        if p is None:
            raise HTTPException(404, "no such printer")
        return p

    @_authed
    async def printer_list(request: Request):
        def build() -> list[dict]:
            return [_printer_view(p, printers) for p in registry.load().values()]
        return JSONResponse({"printers": await run_in_threadpool(build)})

    async def klipper_add(body: dict):
        from .setup_console.printers_tab import API_KEY, BAD_API_KEY
        host, name, port = body.get("host"), _opt(body, "name", str), _opt(body, "port", int, 7125)
        raw_key = body.get("api_key")
        if raw_key is not None and not isinstance(raw_key, str):
            raise BadRequest(BAD_API_KEY, "api_key")
        key = raw_key.strip() if isinstance(raw_key, str) else ""
        if key and not API_KEY.fullmatch(key):
            raise BadRequest(BAD_API_KEY, "api_key")
        if not isinstance(host, str) or not HOST.fullmatch(host.strip()):
            raise BadRequest("host is required: the Moonraker host name or address (letters, digits, . - :)", "host")
        if isinstance(port, bool) or not 1 <= port <= 65535:
            raise BadRequest("port must be between 1 and 65535", "port")

        def run() -> dict:
            try:
                p = discovery.add((name or "").strip() or host.strip(), host.strip(), port)
            except registry.DuplicatePrinter as e:
                raise BadRequest(str(e), "host") from None
            if key:
                registry.set_api_key(p.serial, key)
                p = registry.load().get(p.serial, p)
            printers.sync()
            printers.wait_settled(p.serial, FIRST_STATE_WAIT)
            return _printer_view(p, printers)
        return JSONResponse({"printer": await run_in_threadpool(run)})

    @_authed
    async def printer_add(request: Request):
        body = await _json(request)
        demo.stop()
        fam = body.get("family", "bambu")
        if fam == "klipper":
            return await klipper_add(body)
        if fam != "bambu":
            raise BadRequest("family must be bambu or klipper", "family")
        if "address" in body or "ip" in body:
            if family_off("bambu"):
                return _module_off("printers")
            from . import setup_page

            def run_by_address() -> dict:
                try:
                    res = setup_page.bambu_add_by_address({**body, "require_certificate": True}, allow_replace=False)
                except setup_page.Refused as e:
                    raise BadRequest(e.message, e.extra.get("field")) from None
                serial = res["serial"]
                printers.sync()
                printers.wait_settled(serial, FIRST_STATE_WAIT)
                return _printer_view(registry.load()[serial], printers)
            try:
                view = await run_in_threadpool(run_by_address)
            except setup_page.AlreadySetUpError:
                return _err(409, "exists", "this printer is already set up")
            return JSONResponse({"printer": view})
        serial, code = body.get("serial"), body.get("access_code")
        if not isinstance(serial, str) or not serial.strip():
            raise BadRequest("serial is required", "serial")
        if not isinstance(code, str) or not code.strip():
            raise BadRequest("access_code is required", "access_code")
        name = _opt(body, "name", str)
        serial, code = serial.strip(), code.strip()
        problem = registry.check_fields(serial, code)
        if problem:
            raise BadRequest(problem, "serial" if problem == registry.BAD_SERIAL else "access_code")

        def run() -> dict | None:
            old = registry.load().get(serial)
            if old is None or not old.ip:  # the Access Code would have nowhere to go
                raise HTTPException(404, NOT_SEEN)
            # a connected printer keeps its code; a wrong-code or offline one is re-keyed with the new one
            replace = old is not None and old.configured and printers.state(serial) != "connected"
            try:
                p = registry.set_code(serial, code, name, replace=replace)
            except registry.AlreadyConfigured:
                return None
            printers.sync()
            printers.wait_settled(serial, FIRST_STATE_WAIT)
            return _printer_view(p, printers)
        view = await run_in_threadpool(run)
        if view is None:
            return _err(409, "exists", "this printer is already set up")
        return JSONResponse({"printer": view})

    @_authed
    async def printer_get(request: Request):
        serial = request.path_params["id"]

        def build() -> dict:
            p = known(serial)
            st = printers.status(serial) if hasattr(printers, "status") else None
            pv = _printer_view(p, printers, st=st)
            return {"printer": pv, "status": st, "hardware": _hardware_view(), "auto": pv["auto"]}
        return JSONResponse(await run_in_threadpool(build))

    @_authed
    async def printer_command(request: Request):
        """`POST /api/printers/{id}/command {action}`. The success body nests the Manager's result inside the
        route's own envelope, and the app parses exactly this shape:

            {"ok": true,                      # the request reached the printer and it answered
             "reply": {"ok": <bool>,          # every reply said "success" (false: refused, see reply.reply)
                       "reply": {...}}}       # the printer's raw reply object (result, reason, err_code ...)

        Errors: 400 `bad_request` (unknown action), 404 `not_found`, 409 `printer_offline`, 504 `no_reply`."""
        serial = request.path_params["id"]
        action = (await _json(request)).get("action")
        if action not in ACTIONS:
            raise BadRequest("action must be one of: " + ", ".join(ACTIONS))
        device = request.state.device

        def run() -> dict:
            result = "ok"
            try:
                return printers.command(serial, action)
            except Exception as e:
                result = type(e).__name__
                raise
            finally:
                log.info("printer command device_id=%s serial=%s action=%s result=%s", device, serial, action, result)
        try:
            reply = await run_in_threadpool(run)
        except PrinterOffline:
            return _err(409, "printer_offline", "the printer is offline; check that it is on and on the network")
        except NoReply:
            return _err(504, "no_reply", "the printer did not answer in time")
        except UnknownPrinter:
            return _err(404, "not_found", "no such printer")
        except family.ModuleOff as e:
            return _module_off(e.family)
        return JSONResponse({"ok": True, "reply": reply})

    @_authed
    async def printer_scan(request: Request):
        """`POST /api/printers/scan {subnet?}`: the owner's Scan for Klipper printers (Moonraker on this machine's
        /24 unless a subnet is given). Found printers are listed unconfigured; add one with POST /api/printers."""
        body = await _json(request)
        subnet = _opt(body, "subnet", str)
        kf = getattr(printers, "klipper", None)
        if kf is None:
            return _module_off("klipper")
        try:
            found = await run_in_threadpool(kf.scan, subnet)
        except ValueError as e:
            raise BadRequest(str(e)) from None
        return JSONResponse({"found": found})

    test_lock = threading.Lock()
    last_test = [-setup_page.CHECK_GAP]

    @_authed
    async def printer_test(request: Request):
        """`POST /api/printers/test {family: "klipper", host, port?, api_key?}`: test a Klipper printer's Moonraker
        address before adding it; saves nothing."""
        from .bridge.klipper import moonraker
        from .setup_console.printers_tab import API_KEY, BAD_API_KEY, checked_address, port_of

        body = await _json(request)
        demo.stop()
        fam = body.get("family")
        if fam == "bambu":
            raise BadRequest("A Bambu Lab printer is tested when it is added.", "family")
        if fam != "klipper":
            raise BadRequest("family must be klipper", "family")
        if family_off("klipper"):
            return _module_off("klipper")
        raw_key = body.get("api_key")
        if raw_key is not None and not isinstance(raw_key, str):
            raise BadRequest(BAD_API_KEY, "api_key")
        key = raw_key.strip() if isinstance(raw_key, str) else ""
        if key and not API_KEY.fullmatch(key):
            raise BadRequest(BAD_API_KEY, "api_key")
        try:
            port = port_of(body)
            host, _, _ = await run_in_threadpool(checked_address, body, "host")
        except setup_page.Refused as e:
            raise BadRequest(e.message, e.extra.get("field")) from None

        with test_lock:
            now = time.monotonic()
            if now - last_test[0] < setup_page.CHECK_GAP:
                return _err(429, "rate_limited", f"wait {setup_page.CHECK_GAP:.0f} s between checks")
            last_test[0] = now

        def run() -> bool:
            try:
                info = moonraker.Moonraker(host, port, key).info()
            except moonraker.Unauthorized:
                raise BadRequest("Moonraker wants a valid API key", "api_key") from None
            except moonraker.MoonrakerError:
                return False
            return isinstance(info, dict) and isinstance(info.get("state"), str)

        return JSONResponse({"reachable": await run_in_threadpool(run)})

    def family_off(fam: str) -> bool:
        """True when `fam`'s module is off here (Printers holds None for it; a bare Manager is Bambu only)."""
        if isinstance(printers, family.Printers):
            return (printers.klipper if fam == "klipper" else printers.bambu) is None
        return fam == "klipper" and getattr(printers, "klipper", None) is None

    def editable(serial: str) -> registry.Printer:
        """The entry a PATCH or DELETE acts on: 404 when there is none; module_off when its family's module is off."""
        if not registry.SERIAL.fullmatch(serial):
            raise HTTPException(404, "no such printer")
        p = known(serial)
        if family_off(p.family):
            raise _ModuleOffError(p.family)
        return p

    def patch_checked(p: registry.Printer, body: dict) -> dict:
        """What a PATCH body asks for, every key checked against the printer's family before anything is written."""
        from .setup_console.printers_tab import API_KEY, BAD_API_KEY
        klipper = p.family == "klipper"
        want = {}
        if "name" in body:
            name = body["name"]
            if not isinstance(name, str) or len(name) > 64 or not name.isprintable() or not name.strip():
                raise BadRequest("give the printer a name (printable text, at most 64 characters)", "name")
            want["name"] = name.strip()
        if "access_code" in body:
            if klipper:
                raise BadRequest("a Klipper printer has no Access Code (a Moonraker API key instead, when it wants one)",
                                 "access_code")
            code = body["access_code"]
            code = code.strip() if isinstance(code, str) else ""
            if not registry.ACCESS_CODE.fullmatch(code):
                raise BadRequest(registry.BAD_ACCESS_CODE, "access_code")
            want["access_code"] = code
        if "unpin" in body:
            if klipper:
                raise BadRequest("unpin is for a Bambu printer", "unpin")
            if body["unpin"] is not True:
                raise BadRequest("unpin must be true", "unpin")
            if "access_code" not in body:      # the stored code never goes to a certificate it hasn't seen
                raise BadRequest(NEEDS_CODE_TO_UNPIN, "access_code")
            want["unpin"] = True
        if "api_key" in body:
            if not klipper:
                raise BadRequest("api_key is for a Klipper printer", "api_key")
            key = body["api_key"]
            key = key.strip() if isinstance(key, str) else None
            if key is None or (key and not API_KEY.fullmatch(key)):
                raise BadRequest(BAD_API_KEY, "api_key")
            want["api_key"] = key
        port = None
        if "port" in body:
            if not klipper:
                raise BadRequest("port is for a Klipper printer", "port")
            port = body["port"]
            if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
                raise BadRequest("the port must be a number from 1 to 65535", "port")
        if "address" in body or port is not None:
            if "address" in body:
                host, problem, note = hostaddr.inspect(body["address"])
                if problem:
                    raise BadRequest(problem, "address")
            else:
                host, note = p.host, None
            if klipper:
                port = port if port is not None else p.port
                other = registry.klipper_at(registry.load(), host, port)
                if other is not None and other.serial != p.serial:
                    raise _Conflict(f"{registry.display_name(other)} is already at {host}:{port}")
                # Moonraker has no certificate pin, so a stored key would go to whatever answers there
                if p.api_key and "api_key" not in body:
                    raise BadRequest(NEEDS_KEY_TO_MOVE, "api_key")
            elif p.access_code and not p.cert_fp and "access_code" not in body:
                # the pin keeps the stored code with the same printer; with none pinned yet, the next leaf would be
                raise BadRequest(NEEDS_CODE_TO_MOVE, "access_code")
            want["address"] = (host, port, note)
        return want

    @_authed
    async def printer_patch(request: Request):
        """`PATCH /api/printers/{id}` {name?, address?, port?, access_code?, api_key?, unpin?}: what the setup page's
        printer routes do (rename, address, Access Code, API key, unpin), for a paired device. Every key is checked
        before anything is written. Reply: {printer, changed, note?}; after a connection change the reply waits up to
        FIRST_STATE_WAIT for the new connection's first result."""
        serial = request.path_params["id"]
        body = await _json(request)
        unknown = sorted(set(body) - PATCH_KEYS)
        if unknown:
            raise BadRequest("unknown key: " + ", ".join(unknown) + " (allowed: " + ", ".join(sorted(PATCH_KEYS)) + ")")
        if not body:
            raise BadRequest("nothing to change: give one or more of " + ", ".join(sorted(PATCH_KEYS)))
        device = request.state.device

        def run() -> dict:
            want = patch_checked(editable(serial), body)
            changed = []
            if "name" in want:
                registry.rename(serial, want["name"])
                changed.append("name")
            if "address" in want:
                host, port, _ = want["address"]
                registry.set_address(serial, host, port)
                changed += (["address"] if "address" in body else []) + (["port"] if "port" in body else [])
            if "access_code" in want:
                registry.set_code(serial, want["access_code"], replace=True)   # clears the pin, as adding again
                changed.append("access_code")
            if "api_key" in want:
                registry.set_api_key(serial, want["api_key"])
                changed.append("api_key")
            if "unpin" in want:
                registry.clear_pin(serial)
                changed.append("unpin")
            printers.sync()
            if set(changed) - {"name"}:
                printers.wait_settled(serial, FIRST_STATE_WAIT)
            out = {"printer": _printer_view(known(serial), printers), "changed": changed}
            note = want["address"][2] if "address" in want else None
            if note:
                out["note"] = note
            return out
        try:
            out = await run_in_threadpool(run)
        except _ModuleOffError as e:
            return _module_off("klipper" if e.family == "klipper" else "printers")
        except _Conflict as e:
            return _err(409, "exists", str(e), field="address")
        log.info("printer edit device_id=%s serial=%s changed=%s", device, serial, ",".join(out["changed"]))
        return JSONResponse(out)

    @_authed
    async def printer_delete(request: Request):
        """`DELETE /api/printers/{id}`: forget the printer (its registry entry and connection), as the setup page's
        Forget. Reply {ok, id}."""
        serial = request.path_params["id"]
        device = request.state.device

        def run() -> bool:
            editable(serial)
            gone = registry.remove(serial)
            if gone:
                auto_slot.default_store().drop_printer(serial)
            printers.sync()
            return gone
        try:
            gone = await run_in_threadpool(run)
        except _ModuleOffError as e:
            return _module_off("klipper" if e.family == "klipper" else "printers")
        if not gone:
            return _err(404, "not_found", "no such printer")
        log.info("printer forget device_id=%s serial=%s", device, serial)
        return JSONResponse({"ok": True, "id": serial})

    return [
        Route("/api/printers", printer_list),
        Route("/api/printers", printer_add, methods=["POST"]),
        Route("/api/printers/scan", printer_scan, methods=["POST"]),
        Route("/api/printers/test", printer_test, methods=["POST"]),
        Route("/api/printers/{id}", printer_get),
        Route("/api/printers/{id}", printer_patch, methods=["PATCH"]),
        Route("/api/printers/{id}", printer_delete, methods=["DELETE"]),
        Route("/api/printers/{id}/command", printer_command, methods=["POST"]),
    ]
# ---- end printer routes ----------------------------------------------------------------------------------


# ---- slot mapping, loading and usage routes (route-contract.md) ------------------------------------------
LABEL = re.compile(r"[A-Za-z0-9-]{1,16}")    # A1-D4, HT-A ..., Ext
MAX_DAYS = 366.0


def _bridge_routes(printers, mapper: mapping.Mapper, sm) -> list[Route]:
    def known(serial: str) -> registry.Printer:
        p = registry.load().get(serial)
        if p is None:
            raise UnknownPrinter(serial)
        return p

    def klipper_of(serial: str):
        """The Klipper family when `serial` is a Klipper printer, else None (a Bambu printer)."""
        p = registry.load().get(serial)
        if p is None or p.family != "klipper":
            return None
        kf = getattr(printers, "klipper", None)
        if kf is None:
            raise family.ModuleOff("klipper")
        return kf

    def bambu_only(serial: str) -> None:
        p = registry.load().get(serial)
        if p is not None and p.family == "klipper":
            raise family.NotSupported("slot mapping is for Bambu printers; a Klipper printer's slots are its "
                                      "tools or lanes")

    def label_of(request: Request) -> str:
        label = request.path_params["label"]
        if not LABEL.fullmatch(label):
            raise loading.NotFound(f"no slot {label!r}")
        return label

    def not_cert_changed(serial: str) -> None:
        if printers.state(serial) == "cert_changed":
            raise mapping.CertChanged(serial)

    def not_mapping(serial: str) -> None:
        """Slot mapping changes the slots (test colours, the calibration job): no load or unload meanwhile, also
        when `spoolwarden printer map` runs it in another process (dry runs too: the slots are in flux)."""
        if mapper.in_progress(serial):
            raise mapping.AlreadyRunning(serial)

    def sealed() -> str:
        return storage.current().sealed_location().name

    def audit(what: str, device, result: str, **fields) -> None:
        extra = " ".join(f"{k}={_safe(v)}" for k, v in fields.items())
        log.info("%s device_id=%s %s result=%s", what, device, extra, result)

    def audited(what: str, device, fn, **fields):
        result = "ok"
        try:
            return fn()
        except Exception as e:
            result = type(e).__name__
            raise
        finally:
            audit(what, device, result, **fields)

    def manual_call(what: str, fn, write: bool = True):
        """A manual-map step for the app: the same answers as on the setup page. A Klipper printer's session is the
        Klipper family's (bridge/klipper/manual.py), a Bambu printer's the mapper's (bridge/manual.py)."""
        from .bridge import manual

        def session_of(serial: str):
            kf = klipper_of(serial)
            return kf.manual if kf is not None else mapper.manual

        @_authed
        async def route(request: Request):
            serial = request.path_params["id"]
            body = await _json(request) if request.method == "POST" else {}
            try:
                out = await run_in_threadpool(lambda: fn(session_of(serial), serial, body))
            except manual.ManualError as e:
                return _err(e.status, e.code, str(e))
            if write:
                await run_in_threadpool(audit, f"manual map {what}", request.state.device, "ok", serial=serial)
            return JSONResponse(out)
        return route

    manual_routes = [
        ("start", "POST", lambda mm, s, b: mm.start(s)),
        ("", "GET", lambda mm, s, b: mm.view(s)),
        ("confirm", "POST", lambda mm, s, b: mm.confirm(s, b.get("session"), b.get("position"), b.get("reported"),
                                                         b.get("nozzle"), b.get("by_hand") is True)),
        ("skip", "POST", lambda mm, s, b: mm.skip(s, b.get("session"), b.get("position"))),
        ("stop", "POST", lambda mm, s, b: mm.stop(s, b.get("session"))),
        ("offer", "POST", lambda mm, s, b: mm.answer_offer(s, b.get("share") is True)),
    ]

    @_authed
    async def map_start(request: Request):
        demo.stop()
        serial = request.path_params["id"]
        await run_in_threadpool(bambu_only, serial)
        job = await run_in_threadpool(audited, "slot mapping", request.state.device,
                                      lambda: mapper.start(serial), serial=serial)
        return JSONResponse({"job_id": job}, status_code=202)

    @_authed
    async def map_stop(request: Request):
        """Stop a running slot mapping (the slots are put back first); `spoolwarden printer map` on Ctrl-C."""
        serial = request.path_params["id"]
        await run_in_threadpool(bambu_only, serial)
        await run_in_threadpool(audited, "slot mapping stopped", request.state.device, lambda: mapper.abort(serial, pending=False),
                                serial=serial)
        return JSONResponse({"ok": True})

    @_authed
    async def map_get(request: Request):
        serial = request.path_params["id"]

        def build() -> dict:
            known(serial)
            bambu_only(serial)
            return mapper.view(serial)
        return JSONResponse(await run_in_threadpool(build))

    @_authed
    async def map_report(request: Request):
        """The printer report (bridge/printer_report.py): an XML file with no owner data, for its owner to share."""
        serial = request.path_params["id"]

        def build() -> dict:
            known(serial)
            bambu_only(serial)
            r = mapper.report(serial)        # built from saved records when the printer can't be reached
            return {"name": r["name"], "xml": r["xml"], "live": r.get("live", True),
                    "saved_until": r.get("saved_until")}
        return JSONResponse(await run_in_threadpool(build))

    @_authed
    async def auto_load(request: Request):
        serial = request.path_params["id"]
        body = await _json(request)
        spool_id = body.get("spool_id")
        if not isinstance(spool_id, int) or isinstance(spool_id, bool):
            raise BadRequest("spool_id must be a whole number", "spool_id")
        dry = _opt(body, "dry_run", bool, True)

        def run() -> dict:
            known(serial)
            kf = klipper_of(serial)
            if kf is None:
                if isinstance(printers, family.Printers) and printers.bambu is None:
                    raise family.ModuleOff("printers")
                not_cert_changed(serial)
                not_mapping(serial)
            return auto_slot.default_store().start(printers, sm, serial, spool_id, dry=dry)
        return JSONResponse(await run_in_threadpool(audited, "auto load", request.state.device, run,
                                                    serial=serial, spool=spool_id, dry_run=dry))

    @_authed
    async def auto_choose(request: Request):
        serial = request.path_params["id"]
        body = await _json(request)
        slot = body.get("slot")
        if not isinstance(slot, str) or not slot.strip():
            raise BadRequest("slot is required", "slot")
        if auto_slot.is_reserved_label(slot):
            return _err(400, "invalid_slot", "'auto' is a reserved slot label")

        def run() -> dict:
            known(serial)
            kf = klipper_of(serial)
            if kf is None:
                if isinstance(printers, family.Printers) and printers.bambu is None:
                    raise family.ModuleOff("printers")
                not_cert_changed(serial)
                not_mapping(serial)
            try:
                return auto_slot.default_store().choose(printers, sm, serial, slot.strip())
            except loading.SlotWriteFailed as e:
                if printers.state(serial) == "needs_developer_mode" or DEVELOPER_MODE_REASON.search(str(e)):
                    raise _Refused("needs_developer_mode",
                                   f"{e}; turn on Developer Mode (LAN-only mode) on the printer and retry") from e
                raise _Refused("write_refused", str(e)) from e
        try:
            r = await run_in_threadpool(audited, "auto choose", request.state.device, run,
                                        serial=serial, slot=slot.strip())
        except _Refused as e:
            return _err(409, e.code, e.message)
        return JSONResponse(r)

    @_authed
    async def auto_cancel(request: Request):
        serial = request.path_params["id"]

        def run() -> dict:
            known(serial)
            return auto_slot.default_store().cancel(serial)
        return JSONResponse(await run_in_threadpool(audited, "auto cancel", request.state.device, run,
                                                    serial=serial))

    @_authed
    async def slot_load(request: Request):
        if auto_slot.is_reserved_label(request.path_params["label"]):
            return _err(400, "invalid_slot", "'auto' is a reserved slot label")
        serial, label = request.path_params["id"], label_of(request)
        body = await _json(request)
        spool_id = body.get("spool_id")
        if not isinstance(spool_id, int) or isinstance(spool_id, bool):
            raise BadRequest("spool_id must be a whole number")
        dry = _opt(body, "dry_run", bool, True)

        def run() -> dict:
            known(serial)
            kf = klipper_of(serial)
            if kf is not None:
                r = auto_slot.load_named_klipper(kf, sm, serial, label, spool_id, dry, sealed_location=sealed())
                return {**r, "confirmed": r.get("confirmed") if dry else bool(r.get("confirmed"))}
            not_cert_changed(serial)
            not_mapping(serial)
            try:
                r = pending.load_or_wait(printers, sm, serial, label, spool_id, dry, sealed_location=sealed())
            except loading.SlotWriteFailed as e:
                if printers.state(serial) == "needs_developer_mode" or DEVELOPER_MODE_REASON.search(str(e)):
                    raise _Refused("needs_developer_mode",
                                   f"{e}; turn on Developer Mode (LAN-only mode) on the printer and retry") from e
                raise _Refused("write_refused", str(e)) from e
            return {**r, "confirmed": bool(r.get("confirmed"))}
        try:
            r = await run_in_threadpool(audited, "slot load", request.state.device, run, serial=serial,
                                        slot=label, spool=spool_id, dry_run=dry)
        except _Refused as e:
            return _err(409, e.code, e.message)
        return JSONResponse(r)

    @_authed
    async def slot_unload(request: Request):
        if auto_slot.is_reserved_label(request.path_params["label"]):
            return _err(400, "invalid_slot", "'auto' is a reserved slot label")
        serial, label = request.path_params["id"], label_of(request)
        dry = _opt(await _json(request), "dry_run", bool, True)

        def run() -> dict:
            known(serial)
            kf = klipper_of(serial)
            if kf is not None:
                r = {**kf.unload(sm, serial, label, dry, sealed_location=sealed()), "waiting_ended": False}
            else:
                not_mapping(serial)
                r = pending.unload(printers, sm, serial, label, dry, sealed_location=sealed())
            first = next((m for m in r["moved_back"] if m["id"] == r["spool"].get("id")), None)
            return {**r, "location": first["location"] if first else r["spool"].get("location")}
        return JSONResponse(await run_in_threadpool(audited, "slot unload", request.state.device, run,
                                                    serial=serial, slot=label, dry_run=dry))

    @_authed
    async def slot_clear(request: Request):
        if auto_slot.is_reserved_label(request.path_params["label"]):
            return _err(400, "invalid_slot", "'auto' is a reserved slot label")
        serial, label = request.path_params["id"], label_of(request)
        dry = _opt(await _json(request), "dry_run", bool, True)

        def run() -> dict:
            known(serial)
            kf = klipper_of(serial)
            if kf is not None:
                return kf.clear(sm, serial, label, dry, sealed_location=sealed())
            not_mapping(serial)
            return pending.clear_slot(printers, sm, serial, label, dry, sealed_location=sealed())
        return JSONResponse(await run_in_threadpool(audited, "slot clear", request.state.device, run,
                                                    serial=serial, slot=label, dry_run=dry))

    @_authed
    async def usage_list(request: Request):
        serial = request.path_params["id"]
        by = request.query_params.get("by") or None
        if by is not None and by != "month":
            raise BadRequest("by must be 'month'", "by")
        default_days = MAX_DAYS if by == "month" else usage.UNDO_DAYS
        raw = request.query_params.get("days", str(default_days))
        try:
            days = float(raw)
        except ValueError:
            raise BadRequest("days must be a number") from None
        if not math.isfinite(days) or not 0 <= days <= MAX_DAYS:
            raise BadRequest(f"days must be between 0 and {MAX_DAYS:g}")

        def build() -> dict:
            known(serial)
            kf = klipper_of(serial)
            got = kf.usage(serial, days) if kf is not None else usage.entries(serial, days)
            if by == "month":
                return {"months": usage.by_month(got)}
            return {"entries": inventory.with_spools(got, sm)}
        return JSONResponse(await run_in_threadpool(build))

    @_authed
    async def usage_undo(request: Request):
        entry_id = request.path_params["entry_id"]
        if family.is_klipper_entry(entry_id):
            raise family.RecordedByMoonraker(entry_id)
        e = await run_in_threadpool(audited, "usage undo", request.state.device,
                                    lambda: usage.undo(entry_id, sm), entry=entry_id)
        return JSONResponse({"entry": e})

    return [
        Route("/api/printers/{id}/map", map_start, methods=["POST"]),
        Route("/api/printers/{id}/map", map_get),
        Route("/api/printers/{id}/map/stop", map_stop, methods=["POST"]),
        *[Route("/api/printers/{id}/manual-map" + (f"/{name}" if name else ""),
                manual_call(name or "view", fn, write=method == "POST"), methods=[method])
          for name, method, fn in manual_routes],
        Route("/api/printers/{id}/report", map_report, methods=["POST"]),
        Route("/api/printers/{id}/slots/auto/load", auto_load, methods=["POST"]),
        Route("/api/printers/{id}/auto/choose", auto_choose, methods=["POST"]),
        Route("/api/printers/{id}/auto/cancel", auto_cancel, methods=["POST"]),
        Route("/api/printers/{id}/slots/{label}/load", slot_load, methods=["POST"]),
        Route("/api/printers/{id}/slots/{label}/unload", slot_unload, methods=["POST"]),
        Route("/api/printers/{id}/slots/{label}/clear", slot_clear, methods=["POST"]),
        Route("/api/printers/{id}/usage", usage_list),
        Route("/api/usage/{entry_id}/undo", usage_undo, methods=["POST"]),
    ]


def _safe(v) -> str:
    """A path value for a log line: as is when it is plain (serials, labels, entry ids), else a placeholder."""
    s = str(v)
    return s if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", s) else "<invalid>"


class _Refused(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message
# ---- end slot mapping, loading and usage routes ----------------------------------------------------------


from .web.slots import CLI_DEVICE, INTAKE_DEVICE  # noqa: E402  (the kit's own pairings; no mcp import)
INTAKE_TOKEN = "intake_kit_token"       # in the state folder: the kit server's own phone web app's pairing


def _intake_web_app(cfg: dict, sm: Spoolman, claude, hosts=None) -> list:
    """The phone web app at /intake, served by the kit server itself on the home network (the user's decision,
    2026-11-09: part of the main software, no module needed). The same app as the web unit's, on the same rules:
    home network only, the setup page's Host rule, the page's own Origin on every POST. It calls this server's API
    over the loopback with its own pairing (device INTAKE_DEVICE, the web role), as the web unit does with its own.
    No push alerts here: the web unit sends those when it is on."""
    from . import web_access
    from .web import mcp_tools, slots
    from .web.intake_app import IntakeApp
    from .web.reader import Reader
    kit =slots.KitApi(token_file=config.paths().state / INTAKE_TOKEN, device=INTAKE_DEVICE, role=web_access.WEB_ROLE)
    tools = mcp_tools.Tools(sm, kit_api=kit, kit_writes=True)
    lan_cidrs = (cfg.get("web") or {}).get("lan") or []
    # the Tag Reader's API (web/reader.py) on this listener too: a reader talks to the Hub on the home network, whether
    # or not Remote access (the web unit) is installed. Same handlers, same reader token and home-network checks, and
    # the same kit calls through the loopback pairing above.
    return [*IntakeApp(tools, public_url=None, lan_cidrs=lan_cidrs, claude=claude, kit_api=kit, push=False,
                       host_ok=lambda request: setup_page.host_ok(request, hosts),
                       host_refusal=setup_page.bad_host_text).routes(),
            *Reader(tools, sm, lan_cidrs=lan_cidrs).routes()]


def create_app(sm: Spoolman | None = None, claude=None, printers=None, camera_hub=None,
               mapper: mapping.Mapper | None = None, enabled_modules: frozenset[str] | None = None,
               setup: dict | None = None) -> Starlette:
    """The phone app's API, plus the setup page (/setup, setup_page.py; `setup` = its test hooks)."""
    cfg = config.load()
    storage.migrate_at_start()                  # the first start of this version writes storage.xml (the Hub unit)
    on = enabled_modules if enabled_modules is not None else modules.enabled(cfg)
    sm = sm if sm is not None else Spoolman(cfg["spoolman_url"])
    if "printers" in on:
        printers = printers if printers is not None else Manager(spoolman=sm)  # started by __main__, not here
        # stopped by __main__'s lifespan; the Manager's `cert_changed` also refuses the camera
        camera_hub = camera_hub if camera_hub is not None else camera.Hub(printer_state=printers.state)
        mapper = mapper if mapper is not None else mapping.Mapper(printers)
    # else: the gated routes never reach these; nothing printer-related is built
    station: list = [None, None]  # [url, client]: the client (and its back-off) lives as long as the url does
    cap = ReadCap(config.paths().state / "reads.json", cfg["reads_per_day"])

    def station_client() -> StationClient | None:  # sync: one client (and back-off) per station url
        url = (config.load().get("station") or {}).get("url")
        if not url:
            return None
        if station[0] != url:
            station[:] = [url, StationClient(url)]
        return station[1]

    def rules() -> dict:  # sync: call it from inside the threadpool
        return storage.current()

    def info_body() -> dict:
        return {"name": config.load()["server_name"], "version": __version__, "api": API_VERSION,
                "apk_version": _apk_version(), "modules": sorted(on),
                "look": config.look(config.load()),
                "hub_id": pairing.hub_id()}

    @_handle
    async def info(request: Request):
        return JSONResponse(await run_in_threadpool(info_body))

    @_handle
    async def pair(request: Request):
        code = (await _json(request)).get("code")
        try:
            token = await run_in_threadpool(pairing.redeem_code, code)
        except pairing.CodeSpent as e:
            return _err(401, "code_expired", str(e))
        except pairing.PairingError as e:
            return _err(401, "unauthorized", str(e))
        dev_id = await run_in_threadpool(pairing.check, token)
        role = await run_in_threadpool(pairing.role_of, dev_id) if dev_id else None
        return JSONResponse({"token": token, "role": role})

    @_authed
    async def unpair(request: Request):
        """A client leaving: only the calling token is revoked. Clients call it best effort when they forget the server."""
        device = request.state.device
        revoked = await run_in_threadpool(pairing.revoke, device)
        log.info("unpair device_id=%s result=%s", device, "ok" if revoked else "already-gone")
        return JSONResponse({"revoked": True})

    def _charge(device):
        """The daily cap, taken only when a read goes to the cloud (a local read is free); the refund gives it back."""
        def take():
            ticket = cap.take(device)
            return lambda: intake.refund_quietly(cap, ticket, device)
        return take

    @_authed
    async def intake_read(request: Request):
        async with request.form() as form:
            photos = []
            for i, f in enumerate(form.getlist("photo")):
                if not isinstance(f, UploadFile):
                    raise BadRequest("photo must be a file upload")
                photos.append((f.filename or f"photo{i + 1}", await f.read(intake.MAX_PHOTO_BYTES + 1)))
            hint = form.get("hint") or ""
            tag = form.get("tag") or ""
        if not isinstance(hint, str) or not isinstance(tag, str):
            raise BadRequest("hint and tag must be text fields")
        images = intake.check_photos(photos)
        uid = tags.normalize_uid(tag) if tag.strip() else None
        device = request.state.device

        def run() -> dict:
            ctx = intake.read_context(config.load(), sm, station_client(), app_tag=uid is not None)
            return intake.read_labels(sm, images, hint[:MAX_HINT], uid, claude, **ctx, charge=_charge(device))
        return JSONResponse(await run_in_threadpool(run))

    @_authed
    async def intake_retry(request: Request):
        read_id = request.path_params["read_id"]
        device = request.state.device

        def run() -> dict:
            if not intake.is_kept(read_id):  # an unknown or expired read fails with not_found, using no read
                return intake.retry(read_id, sm, claude)
            return intake.retry(read_id, sm, claude, charge=_charge(device), orders=intake.read_context(
                config.load(), sm, None, app_tag=True)["orders"])
        return JSONResponse(await run_in_threadpool(run))

    # ---------- the web unit's label reads: the kit's cap and order history, never its files ----------
    @_authed
    async def cap_take(request: Request):
        """One read off the shared cap; the ticket gives it back (cap_refund) within intake.TICKET_TTL."""
        ticket = await run_in_threadpool(cap.take, request.state.device)
        return JSONResponse({"ok": True, "ticket": ticket})

    @_authed
    async def cap_refund(request: Request):
        """Give back the read of one live ticket this device's take returned (409 refund_refused otherwise)."""
        ticket = (await _json(request)).get("ticket")
        await run_in_threadpool(cap.refund, ticket, request.state.device)
        return JSONResponse({"ok": True})

    @_authed
    async def intake_orders(request: Request):
        """The order history for a label read's prompt (None: the orders module is off or no file is set). Only for
        the web unit's own device (role "web", set as root by `modules apply`): the kit's own reads use it in
        process, and no phone or PC needs the whole history."""
        if await run_in_threadpool(pairing.role_of, request.state.device) != pairing.WEB_ROLE:
            return _err(403, "forbidden", "the order history is for the web unit only")

        def build():
            cfg = config.load()
            path = (cfg.get("orders") or {}).get("file")
            return intake.load_orders(cfg) if path and Path(path).is_file() else None  # as read_context's
        return JSONResponse({"orders": await run_in_threadpool(build)})

    @_authed
    async def intake_share_corrections(request: Request):
        """What the phone and web apps show of Share corrections (design 04/05: the state only; it is switched on
        the Hub page or at the terminal): {saved, actual, differs, count, reason}, share_corrections.state."""
        from . import share_corrections
        return JSONResponse(await run_in_threadpool(lambda: share_corrections.state(config.load())))

    @_authed
    async def intake_add(request: Request):
        body = await _json(request)
        dry = _opt(body, "dry_run", bool, True)
        uid = _opt(body, "tag", str) or None
        read_id = _opt(body, "read_id", str) or None
        device = getattr(request.state, "device", None)

        def run():
            r = inventory.add_and_link(sm, body.get("items"), rules(), dry, uid)
            hid = None if r.dry else intake_history.record_save(r.saved, source="phone", device=_device_name(device),
                                                                read_id=read_id, tag=uid)
            return r, hid
        r, hid = await run_in_threadpool(run)
        return JSONResponse({"spool_ids": r.spool_ids, "lines": r.lines, "dry": r.dry, "items": r.items,
                             **({"history_id": hid} if hid else {})})

    # ---------- the intake history (intake_history.py) ----------
    def _hist(fn):
        @_authed
        async def route(request: Request):
            try:
                out = await fn(request)
            except intake_history.HistoryError as e:
                return _err(e.status, e.code, str(e))
            return out if isinstance(out, Response) else JSONResponse(out)
        return route

    def _ai_on() -> bool:
        return intake.reading_on(config.load())

    async def history_list(request: Request):
        return await run_in_threadpool(lambda: intake_history.list_records(_ai_on()))

    async def history_get(request: Request):
        return await run_in_threadpool(intake_history.get, request.path_params["hid"])

    async def history_photo(request: Request):
        data = await run_in_threadpool(intake_history.photo, request.path_params["hid"], request.path_params["n"])
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=300"})

    async def history_revise(request: Request):
        body = await _json(request)
        device = getattr(request.state, "device", None)

        def run() -> dict:          # a revision by Claude is a read: it counts against the daily cap
            return intake_history.revise(sm, request.path_params["hid"], body.get("note"), body.get("item"),
                                         client=claude, ai_on=_ai_on(), charge=_charge(device))
        if not _ai_on():
            raise intake_history.HistoryError(409, "ai_off", "label reading (ai_intake or local_intake) is off on "
                                                             "this server: edit the values by hand")
        return await run_in_threadpool(run)

    async def history_apply(request: Request):
        body = await _json(request)
        device = _device_name(getattr(request.state, "device", None))
        return await run_in_threadpool(lambda: intake_history.apply(
            sm, request.path_params["hid"], proposal=_opt(body, "proposal", str), item=body.get("item"),
            values=body.get("values") if "proposal" not in body else None, spool_ids=body.get("spool_ids"),
            device=device))

    async def history_undo(request: Request):
        body = await _json(request)
        return await run_in_threadpool(intake_history.undo, sm, request.path_params["hid"], body.get("revision"))

    # ---------- the corrections waiting to be shared (reader_corrections.py): the list and Remove only; Send now,
    # the package and Remove all are on the Hub page ----------
    def _corr(fn):
        @_authed
        async def route(request: Request):
            try:
                return JSONResponse(await run_in_threadpool(fn, request))
            except reader_corrections.CorrectionsError as e:
                return _err(e.status, e.code, str(e), **e.extra)
        return route

    def corrections_list(request: Request):
        return reader_corrections.waiting(config.load())

    def corrections_remove(request: Request):
        return reader_corrections.remove(config.load(), request.path_params["cid"])

    # ---------- the intake station's button flow (station_flow.py) ----------
    def _flow(fn):
        async def route(request: Request):
            try:
                return JSONResponse(await fn(request))
            except station_flow.FlowError as e:
                return _err(e.status, e.code, str(e), display=e.display, **e.extra)
            except CapReached as e:
                return _err(429, "daily_cap", str(e), display=station_flow.lines("Read limit reached", "use phone"))
            except SpoolmanError as e:
                r = _to_response(e)
                return JSONResponse({**json.loads(r.body), "display": station_flow.lines("Spoolman down", "try again")},
                                    status_code=r.status_code)
        return _authed(route)

    async def station_tag(request: Request):
        return await run_in_threadpool(station_flow.tag_info, sm, request.path_params["uid"])

    async def station_recognize(request: Request):
        async with request.form() as form:
            f = form.get("image")
            if not isinstance(f, UploadFile):
                raise station_flow.FlowError(400, "bad_request", "image must be a file upload",
                                             station_flow.lines("No photo", "try again"))
            data = await f.read(intake.MAX_PHOTO_BYTES + 1)
        try:
            (image,) = intake.check_photos([(f.filename or "station.jpg", data)])
        except PhotoError as e:
            raise station_flow.FlowError(400, "bad_photo", str(e), station_flow.lines("Bad photo", "try again")) from e
        return await run_in_threadpool(
            lambda: station_flow.recognize(sm, config.load(), image, claude, cap,
                                           getattr(request.state, "device", None)))

    @_authed
    async def station_hello(request: Request):
        """`spoolwarden station pair` calls this once: the first authenticated call claims the station's token. The
        station also tells the Hub about itself in the query: `fp` (the SHA-256 of its certificate),
        `port` and `version`, or `port`, `version` and `plain` (why it has no certificate). The Hub keeps them with the
        station's pairing; a call with none of them (an older station) stores nothing and is still answered."""
        q = request.query_params
        told = {k: q.get(k) for k in ("fp", "port", "version", "plain")}
        if told["plain"]:       # a reason is one clean line of bounded length before it is stored or shown
            told["plain"] = address_texts.clean_line(told["plain"]) or None
        if any(told.values()):
            kept = await run_in_threadpool(pairing.record_station_hello, request.state.device, told["fp"],
                                           told["port"], told["version"], told["plain"])
            if not kept:
                return _err(400, "bad_hello", "the certificate fingerprint, port or version is not valid, or this "
                                              "token is not a Weigh Station's")
        return JSONResponse({"ok": True, "voice": await run_in_threadpool(lambda: voice_tab.hello_voice(config.load()))})

    @_handle
    async def station_pair_request(request: Request):
        """An unpaired station asks to be paired (station_requests.py): no token yet, so no _authed."""
        body = await _json(request)
        try:
            out = await run_in_threadpool(station_requests.ask, body.get("request_id"), body.get("commit"),
                                          body.get("name"), request.client.host if request.client else "")
        except station_requests.RequestError as e:
            return _err(e.status, e.code, str(e))
        log.info("station pairing requested from %s", _safe(request.client.host if request.client else "?"))
        return JSONResponse(out, status_code=202)

    @_handle
    async def station_pair_reveal(request: Request):
        body = await _json(request)
        try:
            out = await run_in_threadpool(station_requests.reveal, request.path_params["request_id"], body.get("secret"))
        except station_requests.RequestError as e:
            return _err(e.status, e.code, str(e))
        return JSONResponse(out)

    @_handle
    async def station_pair_poll(request: Request):
        status, body = await run_in_threadpool(station_requests.poll, request.path_params["request_id"])
        return JSONResponse(body, status_code=status)

    async def station_save(request: Request):
        body = await _json(request)
        return await run_in_threadpool(lambda: station_flow.save(sm, rules(), body, returner=returner))

    async def station_return(request: Request):
        body = await _json(request)
        return await run_in_threadpool(lambda: station_flow.return_spool(sm, rules(), body, returner=returner))

    @_authed
    async def tag_lookup(request: Request):
        return JSONResponse({"spool": await run_in_threadpool(inventory.by_tag, sm, request.path_params["uid"])})

    @_authed
    async def spool_link(request: Request):
        body = await _json(request)
        tag = body.get("tag")
        if not isinstance(tag, str) or not tag.strip():
            raise BadRequest("tag is required")
        kw = dict(gross=_opt(body, "gross", float), location=_opt(body, "location", str),
                  force=_opt(body, "force", bool, False), dry=_opt(body, "dry_run", bool, True))
        spool = await run_in_threadpool(
            lambda: inventory.open_spool(sm, request.path_params["id"], tag, rules(), **kw))
        out = {"spool": spool}
        if not kw["dry"] and isinstance(spool, dict) and spool.get("event_id") is not None:
            out["event_id"] = spool["event_id"]
        return JSONResponse(out)

    @_authed
    async def spool_update(request: Request):
        body = await _json(request)
        out = await run_in_threadpool(functools.partial(
            inventory.edit_spool, sm, request.path_params["id"], gross=_opt(body, "gross", float),
            remaining=_opt(body, "remaining", float), location=_opt(body, "location", str),
            empty=_opt(body, "empty", bool, False), dry=_opt(body, "dry_run", bool, True), rules=rules(),
            home=_opt(body, "home", str), auto_location=_opt(body, "auto_location", bool, False)))
        return JSONResponse(out)       # `spool` as before; `view`, `before` and `dry` are added (S3.9); `home`: 409 not_checked_out

    def returner(spool_id: int, *, grams: float | None = None, dry: bool = False, gross: float | None = None) -> dict:
        """`inventory.return_spool`, or the Klipper family's when the spool sits in one of its printers' slots (the
        printer is then told to clear the slot when it answers). Sync: call it from inside the threadpool."""
        spool = sm.get(f"spool/{spool_id}")
        where = inventory.view_context().slot_of(spool.get("location")) or {}
        p = registry.load().get(where.get("printer_id") or "")
        kf = getattr(printers, "klipper", None) if p is not None and p.family == "klipper" else None
        if kf is not None:
            return kf.return_spool(sm, p.serial, spool_id, grams=grams, rules=rules(), dry=dry, gross=gross)
        out = inventory.return_spool(sm, spool_id, grams=grams, rules=rules(), dry=dry, gross=gross)
        if p is not None and p.family == "klipper":     # its family is not running: the Hub's record is all that changed
            log.warning("return: spool %s was in a slot of Klipper printer %s, which cannot be told (the Klipper family is "
                        "not running); only the Hub's record changed", spool_id, p.serial)
            out["printer_notified"] = False
        return out

    @_authed
    async def spool_return(request: Request):
        """`POST api/spools/{id}/return`: a checked-out spool goes back to its home (the place the rules chose when
        none was recorded) with no question and no body. `location` is where it went (None when the rules place it nowhere);
        `event_id` is its Undo. 409 not_checked_out otherwise."""
        sid = request.path_params["id"]
        if not sid.isdecimal():
            return _err(404, "not_found", f"no spool {sid}")
        out = await run_in_threadpool(returner, int(sid))
        return JSONResponse({**out, "location": out["view"]["location"] or None})

    @_authed
    async def spool_get(request: Request):
        spool = await run_in_threadpool(sm.get, f"spool/{request.path_params['id']}")
        return JSONResponse({"spool": spool, "view": inventory.spool_view(spool)})   # raw and phone shape, as PATCH gives

    def _caller_host(request: Request) -> str | None:
        raw = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
        host = raw.rsplit(":", 1)[0] if ":" in raw and not raw.endswith("]") else raw
        return host or None

    @_authed
    async def spool_search(request: Request):
        q = request.query_params.get("q", "")
        sealed = request.query_params.get("sealed", "").lower() in ("1", "true", "yes")
        flt = request.query_params.get("filter", "")
        offset, limit = inventory.page_args(request.query_params)
        host = _caller_host(request)

        def build() -> tuple[list[dict], str | None]:
            try:                    # `filter`: sealed, opened, loaded, low or checked_out; the rows carry their `view`
                rows = inventory.listing(sm, q, sealed, flt)
            except inventory.FilterError as e:
                raise BadRequest(str(e), "filter") from None
            return rows, hub_view.spoolman_url(config.load(), host)
        rows, sm_url = await run_in_threadpool(build)
        rows, paging = inventory.page(rows, offset, limit)
        return JSONResponse({"spools": rows, **paging, "filters": inventory.filter_names(),
                             **({"spoolman_url": sm_url} if sm_url else {})})

    @_authed
    async def filament_list(request: Request):
        q = request.query_params.get("q", "")
        offset, limit = inventory.page_args(request.query_params)
        rows = await run_in_threadpool(inventory.filaments, sm, q)
        rows, paging = inventory.page(rows, offset, limit)
        return JSONResponse({"filaments": rows, **paging})

    @_authed
    async def vendor_list(request: Request):
        return JSONResponse({"vendors": await run_in_threadpool(inventory.vendors, sm)})

    @_authed
    async def locations(request: Request):
        """Today's answer, a list of names (released apps read it): every storage location's name in fill order, then
        any other text a spool is stored at. A containers location is one name here; its containers are the spools'."""
        def names() -> list[str]:
            return inventory.known_locations(sm, [x.name for x in storage.current().locations])
        return JSONResponse({"locations": await run_in_threadpool(names)})

    @_authed
    async def storage_list(request: Request):
        """The storage locations with how full each is, the order a new spool goes by material, and notices
        (storage_api.view; the web app's twin and `spoolwarden locations --json` give the same data)."""
        return JSONResponse(await run_in_threadpool(storage_api.current_view, sm, storage.current()))

    def _number(q, name: str, kind=int):
        raw = q.get(name)
        if raw in (None, ""):
            return None
        try:
            return kind(raw)
        except ValueError:
            raise BadRequest(f"{name} must be a number", name) from None

    @_authed
    async def events_get(request: Request):
        """The Hub's events (alerts_api.events_answer): `since` (an id) or `since_at` (a time, UTC),
        `wait` up to 55 s, `kinds` (prefixes, comma separated), `limit`, `alert=1` (only what an alert type that is
        on covers) and `client` (the Hub page's browser id, for its test alert)."""
        q = request.query_params
        since, since_at = _number(q, "since"), q.get("since_at") or None
        if since is not None and since_at:
            raise BadRequest("give since or since_at, not both")
        if since is not None and since < 0:
            raise BadRequest("since must be 0 or more", "since")
        newest = _number(q, "newest")
        if newest is not None and (newest < 1 or newest > 1000):
            raise BadRequest("newest must be between 1 and 1000", "newest")
        wait_val = _number(q, "wait", float) or 0
        if newest is not None and (since is not None or since_at or wait_val > 0):
            raise BadRequest("give newest without since, since_at or wait")
        if since_at:
            try:
                since_at = alerts_api.parse_time(since_at)
            except ValueError as e:
                raise BadRequest(str(e), "since_at") from None
        client = (q.get("client") or "").strip()[:80]
        callers = [f"device:{request.state.device}"] + ([f"client:{client}"] if client else [])
        activity = q.get("activity", "").lower() in ("1", "true", "yes")
        subject = (q.get("subject") or "").strip() or None
        actor = (q.get("actor") or "").strip() or None
        out = await run_in_threadpool(functools.partial(
            alerts_api.events_answer, since=since, since_at=since_at, wait=_number(q, "wait", float) or 0,
            kinds=q.get("kinds") or None, limit=_number(q, "limit") or events.LIMIT_DEFAULT,
            alert=q.get("alert", "").lower() in ("1", "true", "yes"), callers=callers,
            activity=activity, subject=subject, actor=actor, **({"newest": newest} if newest is not None else {})))
        return JSONResponse(out)

    @_authed
    async def events_web(request: Request):
        """The web unit's own spool events, handed over: only `spool.*` kinds; the Hub gives the id, keeps the
        `at` and sets the actor (`device` for the phone web app, `connector` for the connector's tools)."""
        body = await _json(request)
        items = body.get("events")
        if not isinstance(items, list) or len(items) > 500:
            raise BadRequest("events must be a list of at most 500 events", "events")
        device = request.state.device
        fallback = "device" if await run_in_threadpool(_is_intake_device, device) else "connector"
        batch = []
        for it in items:
            if not isinstance(it, dict) or not str(it.get("kind", "")).startswith("spool."):
                raise BadRequest("the web unit may hand over spool.* events only", "events")
            at = it.get("at")
            when = datetime.fromisoformat(alerts_api.parse_time(at).replace("Z", "+00:00")) if at else None
            actor = it.get("actor") if isinstance(it.get("actor"), dict) else {}
            kind = actor.get("type") if actor.get("type") in ("device", "connector") else fallback
            batch.append((it["kind"], it.get("subject") or {"type": "spool"},
                          {"type": kind, "id": str(actor.get("id") or device), "name": str(actor.get("name") or "")},
                          it.get("data") or {}, when))
        # all or none, and a resent hand-over writes nothing twice (events.write_many)
        written = await run_in_threadpool(events.write_many, [
            {"kind": k, "subject": sub, "actor": act, "data": data, "now": at} for k, sub, act, data, at in batch])
        return JSONResponse({"ok": True, "ids": written})

    @_authed
    async def alerts_get(request: Request):
        return JSONResponse(await run_in_threadpool(alerts_api.alerts_view))

    @_authed
    async def alerts_test(request: Request):
        """A test alert for the caller: nothing is stored; the caller's next `GET api/events` carries it as `test`
        (once, within 60 s). The caller is the Hub page's browser id (`client`) or the device of the token."""
        try:
            body = await request.json()
        except ValueError:
            body = {}
        client = str(body.get("client") or "").strip()[:80] if isinstance(body, dict) else ""
        caller = f"client:{client}" if client else f"device:{request.state.device}"
        return JSONResponse({"ok": True, "test": await run_in_threadpool(alerts_api.queue_test, caller)})

    @_authed
    async def spools_low(request: Request):
        return JSONResponse(await run_in_threadpool(alerts_api.low_view, sm))

    @_authed
    async def storage_use(request: Request):
        return JSONResponse(await run_in_threadpool(alerts_api.storage_view))

    @_authed
    async def event_undo(request: Request):
        try:
            event_id = int(request.path_params["id"])
        except (TypeError, ValueError):
            return _err(404, "not_found", "no such event")
        try:
            res = await run_in_threadpool(undo.undo_event, event_id, sm)
        except undo.EventNotFound as e:
            return _err(404, "not_found", str(e) or "no such event")
        except undo.UndoError as e:
            return _err(409, e.code, e.message)
        return JSONResponse(res)

    @_authed
    async def hub_get(request: Request):
        host = _caller_host(request)
        return JSONResponse(await run_in_threadpool(
            lambda: hub_view.build(host=host, this_device=request.state.device,
                                   getting_started=hub_view.getting_started_counts())))

    @_authed
    async def readers_web_seen(request: Request):
        body = await _json(request)
        return JSONResponse(await run_in_threadpool(reader_tokens.record_seen, body.get("readers")))

    @_authed
    async def health_get(request: Request):
        days = _number(request.query_params, "days")
        if days is None:
            days = 7
        if days not in (7, 30):
            raise BadRequest("days must be 7 or 30", "days")
        return JSONResponse(await run_in_threadpool(
            functools.partial(health.health_view, days=days, current_checks=_current_checks())))

    @_authed
    async def analytics_get(request: Request):
        months = _number(request.query_params, "months")
        if months is None:
            months = 12
        if not 1 <= months <= 13:
            raise BadRequest("months must be 1 to 13", "months")
        printer = (request.query_params.get("printer") or "").strip() or None
        return JSONResponse(await run_in_threadpool(
            functools.partial(analytics.view, months=months, printer=printer, sm=sm)))

    @_authed
    async def export_csv(request: Request):
        set_name = request.path_params["set"]
        q = request.query_params
        days = _number(q, "days", float)
        if days is not None and (not math.isfinite(days) or days <= 0):
            raise BadRequest("days must be positive", "days")
        printer = (q.get("printer") or "").strip() or None
        kw: dict = {"printer": printer, "sm": sm, "printers_mgr": printers}
        if days is not None:
            kw["days"] = days
        try:
            data = await run_in_threadpool(functools.partial(csv_io.export_csv, set_name, **kw))
        except csv_io.CsvError as e:
            return _err(e.status, e.code, str(e))
        return Response(
            data,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{set_name}.csv"'},
        )

    async def _import_csv(request: Request, kind: str):
        raw_bytes = bytearray()
        async for chunk in request.stream():          # the Hub page's twin takes 5 MiB at most
            if len(raw_bytes) + len(chunk) > MAX_IMPORT_BYTES:
                return _err(413, "too_large", "the CSV file is larger than the Hub takes (5 MB at most)")
            raw_bytes.extend(chunk)
        try:
            raw_text = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise BadRequest("the CSV file must be UTF-8 text") from None
        dry_run = request.query_params.get("dry_run", "1").lower() not in ("0", "false", "no")
        try:
            if kind == "spools":
                res = await run_in_threadpool(
                    functools.partial(csv_io.import_spools, sm, raw_text, dry_run=dry_run, rules=rules()))
            else:
                res = await run_in_threadpool(
                    functools.partial(csv_io.import_locations, raw_text, dry_run=dry_run))
        except csv_io.CsvError as e:
            return _err(e.status, e.code, str(e))
        return JSONResponse(res)

    @_authed
    async def import_spools_route(request: Request):
        return await _import_csv(request, "spools")

    @_authed
    async def import_locations_route(request: Request):
        return await _import_csv(request, "locations")

    @_authed
    async def station_state(request: Request):
        client = await run_in_threadpool(station_client)
        if client is None:
            return _module_off("station")
        try:
            return JSONResponse(await run_in_threadpool(client.state))
        except StationUnreachable as e:
            log.warning("station: %s", e)
            return _err(503, "station_unreachable", "the intake station is not reachable")

    @_handle
    async def apk(request: Request):
        f = config.paths().opt / "app" / "filament-kit.apk"
        if not await run_in_threadpool(f.is_file):
            return _err(404, "not_found", "no app package is installed on this server")
        return FileResponse(f, media_type=APK_TYPE, filename="filament-kit.apk")

    async def http_error(request: Request, exc: HTTPException):
        return _http_error(exc)

    return Starlette(
        routes=[
            Route("/api/info", info),
            Route("/api/hub", hub_get),
            Route("/api/pair", pair, methods=["POST"]),
            Route("/api/pair", unpair, methods=["DELETE"]),
            *_gated([
                Route("/api/intake/read", intake_read, methods=["POST"]),
                Route("/api/intake/retry/{read_id}", intake_retry, methods=["POST"]),
                Route("/api/intake/cap/take", cap_take, methods=["POST"]),
                Route("/api/intake/cap/refund", cap_refund, methods=["POST"]),
                Route("/api/intake/orders", intake_orders),
            ], "ai_intake" if "ai_intake" in on or "local_intake" not in on else "local_intake", on),
            Route("/api/intake/add", intake_add, methods=["POST"]),  # adding by hand needs no AI (spec R 2.4)
            Route("/api/intake/share-corrections", intake_share_corrections),
            Route("/api/intake/history", _hist(history_list)),
            Route("/api/intake/history/{hid}", _hist(history_get)),
            Route("/api/intake/history/{hid}/photos/{n:int}", _hist(history_photo)),
            Route("/api/intake/history/{hid}/revise", _hist(history_revise), methods=["POST"]),
            Route("/api/intake/history/{hid}/apply", _hist(history_apply), methods=["POST"]),
            Route("/api/intake/history/{hid}/undo", _hist(history_undo), methods=["POST"]),
            Route("/api/label-reader/corrections", _corr(corrections_list)),
            Route("/api/label-reader/corrections/{cid}/remove", _corr(corrections_remove), methods=["POST"]),
            Route("/api/tags/{uid}", tag_lookup),
            Route("/api/spools", spool_search),
            Route("/api/spools/low", spools_low),
            Route("/api/filaments", filament_list),
            Route("/api/vendors", vendor_list),
            Route("/api/spools/{id:int}/link", spool_link, methods=["POST"]),
            Route("/api/spools/{id}/return", spool_return, methods=["POST"]),   # `{id}` as WEB_ROUTES spells it
            Route("/api/spools/{id:int}", spool_get),
            Route("/api/spools/{id:int}", spool_update, methods=["PATCH"]),
            Route("/api/locations", locations),
            Route("/api/storage", storage_list),
            Route("/api/storage-use", storage_use),
            Route("/api/events", events_get),
            Route("/api/events/web", events_web, methods=["POST"]),
            Route("/api/readers/web/seen", readers_web_seen, methods=["POST"]),
            Route("/api/events/{id}/undo", event_undo, methods=["POST"]),
            Route("/api/health", health_get),
            Route("/api/analytics", analytics_get),
            Route("/api/export/{set}.csv", export_csv),
            Route("/api/import/spools", import_spools_route, methods=["POST"]),
            Route("/api/import/locations", import_locations_route, methods=["POST"]),
            Route("/api/alerts", alerts_get),
            Route("/api/alerts/test", alerts_test, methods=["POST"]),
            Route("/app.apk", apk),
            Route("/api/station", station_state),
            Route("/api/station/hello", station_hello),
            Route("/api/station/pair-request", station_pair_request, methods=["POST"]),
            Route("/api/station/pair-request/{request_id}", station_pair_poll),
            Route("/api/station/pair-request/{request_id}/reveal", station_pair_reveal, methods=["POST"]),
            Route("/api/station/tag/{uid}", _flow(station_tag)),
            Route("/api/station/recognize", _flow(station_recognize), methods=["POST"]),
            Route("/api/station/save", _flow(station_save), methods=["POST"]),
            Route("/api/station/return", _flow(station_return), methods=["POST"]),
            *voice_tab.kit_routes(_authed),
            *[Route(r.path, _authed(r.handler) if r.auth == "token" else r.handler, methods=[r.method])
              for r in labels.kit_routes(lambda: sm, config.load)],
            *_gated([*_printer_routes(printers), *_bridge_routes(printers, mapper, sm),
                     *camera.routes(camera_hub, _authed)],
                    "klipper" if "klipper" in on and "printers" not in on else "printers", on),
            *_gated(profiles_api.routes(_authed, sm), "presets", on),
            *_intake_web_app(cfg, sm, claude, (setup or {}).get("hosts")),
            *setup_page.routes(printers if on & {"printers", "klipper"} else None,
                               families={f for m, f in (("printers", "bambu"), ("klipper", "klipper")) if m in on},
                               mapper=mapper if "printers" in on else None,
                               spoolman=sm, camera_hub=camera_hub,
                               **(setup or {})),
        ],
        exception_handlers={HTTPException: http_error},
    )
