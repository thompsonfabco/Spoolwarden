"""Devices with their kind, version and out-of-date sentence, and a device's detail the Hub page draws itself (R83 section 9,
10.2 and the rulings of 2026-11-17 21:18 and 21:22). Under /setup/api:

    GET  devices                    {devices: [{id, name, kind, version?, newest?, out_of_date, advice?, paired?, last_seen?,
                                    reachable?, built_in, page}]}: the pairings and the Tag Reader tokens (kind "reader")
    GET  device/{id}                {device, status?, settings?, settings_dropped?, own_page?, photo}: a station's status and
                                    settings come from the station as checked data
    GET  device/{id}/photo          the station's photo, only image/jpeg of at most 4 MiB
    POST device/{id}/settings {code, <setting id>: <value>, ...}   one change; {reset: true} removes the station's own values
    POST device/{id}/open {code}    {address: "<own_page>#ticket=<ticket>"}: a one-time ticket from the station (POST /ticket)

The Hub never serves a device's HTML, script or style. What it takes from a station is data: JSON only, at most 256 KiB, 5
seconds, a redirect is an error, only the address stored for that pairing. Every text from a station reaches the page as text.
The kind and version of a pairing come from the pairing record once it holds them (`kind`, `version`; the client header of
R83 section 9.2); until then a row has no kind, or "station" when it is a Weigh Station pairing.

The station's side (EV3): GET /state (its `photo` object reduced to a bool by `station_client.read_state`),
GET /settings, POST /settings, GET /photo, and POST /ticket -> {ticket, expires_in: 60}; the station's page hands
the ticket to its own POST /session. All station calls go through `station_client` (pinned TLS, single 5 s deadline,
no redirect/proxy, and `X-Station-Proof` on POST /settings and POST /ticket).
"""
from __future__ import annotations

import ipaddress
import re
import urllib.parse

import anyio
from starlette.responses import Response

from .. import __version__, config, pairing, reader_tokens, station_client
from ..station import panel_setup

KINDS = ("phone", "pc", "station", "web", "reader", "builtin")
CLIENT_KINDS = {"android": "phone", "slicer-sync": "pc", "weigh-station": "station", "web": "web"}   # the header's names
SETTING_ID = station_client.SETTING_ID
SETTING_KINDS = station_client.SETTING_KINDS
TICKET = station_client.TICKET
MAX_SETTINGS = station_client.MAX_SETTINGS
MAX_LABEL = station_client.MAX_LABEL
MAX_TEXT = station_client.MAX_TEXT
MAX_JSON = station_client.MAX_JSON
MAX_PHOTO = station_client.MAX_PHOTO
STATION_TIMEOUT = station_client.STATION_TIMEOUT
DROPPED_NOTE = "Some of this device's settings could not be shown."
clean_settings = station_client.clean_settings
PAGE_FROM = ("home", "subnets", "allowed")      # the station's `station.page_from` values


# ---------- what the Hub offers (out of date is a comparison with these) ----------
def _numbers(text) -> tuple[int, ...] | None:
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", str(text or ""))
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _phone_offer() -> str | None:
    try:
        return (config.paths().opt / "app" / "version.txt").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _reader_offer() -> str | None:
    from .. import reader_flash
    got = reader_flash.bundled_image()
    return str(got[1]) if got else None


def _offer(kind: str) -> str | None:
    return {"station": __version__, "pc": __version__, "phone": _phone_offer(), "reader": _reader_offer()}.get(kind)


def _advice(kind: str, version: str, newest: str) -> str:
    """The row's out-of-date sentence; the page words it from the same fields."""
    return {"station": f"Out of date ({version}; the Hub is {newest}). On that machine: sudo spoolwarden update",
            "pc": f"Out of date ({version}). Install the current Slicer Sync on that PC.",
            "phone": "Out of date. The app offers the update when it is opened.",
            "reader": "Out of date. Hold the knob for 4 seconds to update."}[kind]


def _judge(kind: str | None, version) -> dict:
    """{version?, newest?, out_of_date, advice?}: a version that is not known is never out of date."""
    out: dict = {"out_of_date": False}
    have = str(version).strip() if version not in (None, "") else None
    if have:
        out["version"] = have
    newest = _offer(kind) if kind else None
    if newest:
        out["newest"] = newest
    a, b = _numbers(have), _numbers(newest)
    if have and newest and a is not None and b is not None and a < b:
        out["out_of_date"] = True
        out["advice"] = _advice(kind, have, newest)
    return out


# ---------- the sources, one place each so a test can stand in for them ----------
def _pairings() -> list[dict]:
    return pairing.devices()


def _readers() -> list[dict]:
    return reader_tokens.TokenStore().list()


def _station_url(cfg: dict) -> str | None:
    return (cfg.get("station") or {}).get("url")


def _kind_of(d: dict, built_in: bool) -> str | None:
    if built_in:
        return "builtin"
    k = d.get("kind")
    k = CLIENT_KINDS.get(k, k)
    if k in KINDS and k not in ("reader", "builtin"):
        return k
    return "station" if d.get("type") == "station" else None


def rows() -> list[dict]:
    """The Devices list: every pairing, then every Tag Reader token."""
    from ..setup_page import BUILT_IN_DEVICES, _fmt_time
    cfg = config.load()
    devs = _pairings()
    reach = station_client.station_reach(devs, _station_url(cfg))
    out = []
    for d in devs:
        built_in = d["name"] in BUILT_IN_DEVICES
        kind = _kind_of(d, built_in)
        row = {"id": d["id"], "name": d["name"], "kind": kind, **_judge(kind, d.get("version")),
               "paired": _fmt_time(d.get("paired")), "last_seen": _fmt_time(d.get("last_seen")),
               "built_in": built_in, "page": kind == "station"}
        if d["id"] in reach:
            row["reachable"] = reach[d["id"]]
        if kind == "station":       # how its panel is connected
            panel = station_client.station_panel(_station_url(cfg))      # the station's own /state; nothing when it is silent
            if panel:
                row["panel"] = panel_setup.TEXT_DEVICES_WIFI if panel == "wifi" else panel_setup.TEXT_DEVICES_USB
            notice = station_client.station_flash_notice(_station_url(cfg))   # the panel lost its Wi-Fi set-up in a flash
            if notice:
                row["panel_notice"] = notice
        out.append(row)
    for t in _readers():
        made = str(t.get("created") or "").replace("T", " ")[:16]
        out.append({"id": "reader-" + str(t["id"]), "name": t["name"], "kind": "reader", **_judge("reader", t.get("build")),
                    "paired": made or None, "last_seen": _fmt_time(t.get("last_seen")), "built_in": False, "page": False})
    return out


# ---------- the station: data only, checked ----------
def _station_for(device_id: str) -> str:
    """The stored address of the station behind a device row; the Hub knows one station address, so the pairing must be the
    one station pairing (as `station_reach` has it)."""
    from ..setup_page import Refused
    devs = _pairings()
    stations = [d for d in devs if _kind_of(d, False) == "station"]
    if not any(d["id"] == device_id for d in stations):
        raise Refused(404, "no such Weigh Station")
    url = _station_url(config.load())
    if len(stations) != 1 or not _origin(url):
        raise Refused(503, "the Hub does not know this Weigh Station's address")
    return url


def _origin(url) -> str | None:
    """scheme://host[:port] of a stored address, or None when it is not an http(s) address with a host."""
    try:
        u = urllib.parse.urlsplit(str(url or ""))
        return f"{u.scheme}://{u.netloc}" if u.scheme in ("http", "https") and u.hostname and not u.username else None
    except ValueError:
        return None


def own_page(endpoint: tuple[str, int, str | None], hub_host: str) -> str:
    """Use the pinned Station port. A co-hosted Station uses the browser's already validated Hub hostname."""
    host, port, fingerprint = endpoint
    try:
        loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = host.rstrip(".").lower() == "localhost"
    if loopback and hub_host:
        host = hub_host
    authority = f"[{host}]" if ":" in host else host
    scheme = "https" if fingerprint else "http"
    if port != (443 if fingerprint else 80):
        authority += f":{port}"
    return f"{scheme}://{authority}/"


def clean_status(v, depth: int = 0):
    """The station's status as plain data: nested at most three deep, at most 60 entries a level, texts of at most 200
    characters; anything else becomes nothing."""
    if isinstance(v, dict) and depth < 3:
        return {k[:40]: clean_status(x, depth + 1) for k, x in list(v.items())[:60] if isinstance(k, str) and k.strip()}
    if isinstance(v, list) and depth < 3:
        return [clean_status(x, depth + 1) for x in v[:60]]
    if isinstance(v, str):
        return v[:MAX_TEXT]
    return v if isinstance(v, bool) or station_client._number(v) else None


# ---------- routes ----------
def routes(page) -> list[tuple]:
    from ..setup_page import Refused, _host_of

    async def listing(request, body):
        return {"devices": await anyio.to_thread.run_sync(rows)}

    def find(device_id: str) -> dict:
        row = next((r for r in rows() if r["id"] == device_id), None)
        if row is None:
            raise Refused(404, "no such device")
        return row

    def detail(device_id: str, hub_host: str) -> dict:
        row = find(device_id)
        out: dict = {"device": row, "photo": False}
        if row["page"]:
            url = _station_for(device_id)
            try:
                out["own_page"] = own_page(station_client.station_endpoint(url), hub_host)
                status = station_client.read_state(url)
            except station_client.StationCallError:
                out["device"] = {**row, "reachable": False}
                return out
            out["status"] = clean_status(status)
            out["photo"] = status.get("photo") is True
            try:
                got = station_client.call_json(url, "GET", "/settings")
            except station_client.StationCallError:
                out["device"] = {**row, "reachable": False}
                return out
            settings_list, dropped = clean_settings(got.get("settings"))
            out["settings"] = settings_list
            if got.get("page_from") in PAGE_FROM:      # who may open its page (R83 2026-11-18 01:14 point 1); the page
                out["page_from"] = got["page_from"]     # says devices_station_allow_list_only under the link for `allowed`
            if dropped:
                out["settings_dropped"] = True
        return out

    async def one(request, body):
        return await anyio.to_thread.run_sync(detail, request.path_params["id"], _host_of(request.headers.get("host", "")))

    def photo(device_id: str) -> Response:
        if not find(device_id)["page"]:
            raise Refused(404, "no photo")
        url = _station_for(device_id)
        try:
            data = station_client.read_photo(url)
        except station_client.StationCallError as e:
            raise Refused(e.status or 502, e.message) from None
        if not isinstance(data, (bytes, bytearray)) or len(data) > MAX_PHOTO or not data.startswith(b"\xff\xd8"):
            raise Refused(502, "the Weigh Station's photo is not a JPEG picture")
        return Response(bytes(data), 200, media_type="image/jpeg",
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'"})

    async def photo_route(request, body):
        return await anyio.to_thread.run_sync(photo, request.path_params["id"])

    def change(device_id: str, body: dict) -> dict:
        row = find(device_id)
        if not row["page"]:
            raise Refused(404, "this device has no settings")
        values = {k: v for k, v in body.items() if k != "code"}
        if values.get("reset") is True and len(values) == 1:
            sent = {"reset": True}
        else:
            if not values or len(values) > MAX_SETTINGS:
                raise Refused(400, "no settings to change")
            for k, v in values.items():
                if not SETTING_ID.fullmatch(k) or isinstance(v, (dict, list)) or v is None:
                    raise Refused(400, f"{k[:40]} is not a setting this page can change")
            sent = values
        try:
            settings, dropped = station_client.change_settings(_station_for(device_id), sent)
        except station_client.StationSetOnHub as e:
            raise Refused(409, e.message, code="set_on_hub") from None        # {error: the sentence, code}: the page's shape
        except station_client.StationCallError as e:
            raise Refused(e.status or 502, e.message) from None
        return {"settings": settings, **({"settings_dropped": True} if dropped else {})}

    async def settings(request, body):
        device_id = request.path_params["id"]
        out = await anyio.to_thread.run_sync(change, device_id, body)
        page.audit(request, f"Weigh Station {device_id} settings changed")
        return out

    def open_page(device_id: str, hub_host: str) -> dict:
        """The address that opens the station's own page for this browser: its one-time ticket after the `#`."""
        if not find(device_id)["page"]:
            raise Refused(404, "this device has no page of its own")
        url = _station_for(device_id)
        try:
            endpoint = station_client.station_endpoint(url)       # plain HTTP only for a station that told no certificate
            base = own_page(endpoint, hub_host)
            got = station_client.ticket(url, endpoint=endpoint)
        except station_client.StationCallError as e:
            raise Refused(e.status or 502, e.message) from None
        ticket = got.get("ticket") if isinstance(got, dict) else None
        if not (isinstance(ticket, str) and TICKET.fullmatch(ticket)) or got.get("expires_in") != 60 or isinstance(got.get("expires_in"), bool):
            raise Refused(502, "the Weigh Station's ticket is not valid")
        address = f"{base}#ticket={ticket}"
        if _origin(address) != _origin(base):
            raise Refused(502, "the Weigh Station's ticket is not valid")
        return {"address": address}

    async def open_route(request, body):
        device_id = request.path_params["id"]
        out = await anyio.to_thread.run_sync(open_page, device_id, _host_of(request.headers.get("host", "")))
        page.audit(request, f"Weigh Station {device_id} page opened")        # no ticket in the log
        return out

    return [("/devices", page.guard(listing), "GET"), ("/device/{id}", page.guard(one), "GET"),
            ("/device/{id}/photo", page.guard(photo_route), "GET"),
            ("/device/{id}/settings", page.guard(settings, write=True), "POST"),
            ("/device/{id}/open", page.guard(open_route, write=True), "POST")]
