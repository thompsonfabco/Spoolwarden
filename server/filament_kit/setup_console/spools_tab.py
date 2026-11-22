"""The Spools section of the Hub page: the spools as a list, one spool's facts, and the four
changes the page makes to one (weight, place, tag, empty), each as a dry run first.

    GET  spools {q, filter, offset, limit}   {spools: [spool view], total, offset, has_more, spoolman_url?}: the spools that
                                         are not empty, by number, matching the words of `q` (brand, name, material, place,
                                         number, tag) and one `filter` (sealed | opened | loaded | low | checked_out); 50 rows
                                         by default. `filters` names the filters this Hub applies (`checked_out` only when the
                                         kit can return a spool; an answer without it is an older Hub). No code
    GET  spools/{id}                     {spool: view, last_use: {at, printer, grams}?}. No code
    POST spools/{id} {code, gross | remaining | location | auto_location | empty, dry_run}   a new weight, a place
                                         (`auto_location: true`: the kit places it by its state and the storage rule; `placed_text` says why), or empty; answers {spool, before, dry}. dry_run is
                                         true unless it is sent as false. 400 with `field` for a refusal, 404 for no such spool.
                                         `home: "<location>"` changes the home of a checked-out spool (409 not_checked_out)
    GET  spools/last-tag                 {tag: {uid, read_at, source}?}: the newest tag any reader knows. No code
    POST spools/{id}/link {code, tag, dry_run}   link the tag to the spool; answers {spool, before, dry}. 409 when the
                                         tag or the spool already has another one
    POST spools/{id}/return {code}       send a checked-out spool back: {spool, event, location}; 409 {error, code:
                                         "not_checked_out"} when it is not checked out

The spool view is `inventory.spool_view` with `loaded` ({printer_id, printer, slot} or null) and `low` (an opened spool
at or under the running-low weight of Settings > Alerts) when the kit's own view does not carry them yet.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import anyio

from .. import alerts, config, inventory, storage, tagsources, tags
from ..bridge import registry, status
from ..spoolman import Spoolman, SpoolmanError

PAGE = 50
PAGE_MAX = 200
FILTERS = ("sealed", "opened", "loaded", "low")
NOT_CHECKED_OUT = "This spool is not checked out."


def filters() -> list[str]:
    """The filters this Hub applies: `checked_out` is one when the kit has the return."""
    return [*FILTERS, "checked_out"] if hasattr(inventory, "return_spool") else list(FILTERS)
SPOOLMAN_PORT = 7912
LOOPBACK = {"", "localhost", "127.0.0.1", "::1"}


def _loaded(location: str | None) -> dict | None:
    """Where a spool in a printer slot is: the location reads "<printer name> – <slot>"."""
    from ..bridge import loading
    if not location or not loading._is_slot_location(location):
        return None
    name, _, slot = location.partition(f" {status.DASH} ")
    serial = next((s for s, p in registry.load().items() if p.name == name), None)
    return {"printer_id": serial, "printer": name, "slot": slot}


def view(spool: dict, low_g: float) -> dict:
    v = inventory.spool_view(spool)
    if "loaded" not in v:
        v["loaded"] = _loaded(v["location"])
    if "low" not in v:
        left = v["remaining_g"]
        v["low"] = v["state"] == "opened" and isinstance(left, (int, float)) and not isinstance(left, bool) and left <= low_g
    return v


def _words(spool: dict, v: dict) -> str:
    return " ".join(str(x) for x in (v["vendor"], v["name"], v["material"], v["location"], v["id"], v["tag"]) if x).lower()


def _spoolman_url(request) -> str | None:
    """Spoolman's own page: on this Hub, the name the caller used for the Hub with Spoolman's port; elsewhere, the
    address that is set."""
    url = str(config.load().get("spoolman_url") or "").strip()
    if not url:
        return None
    parsed = urlparse(url if "//" in url else "//" + url)
    if (parsed.hostname or "") in LOOPBACK:
        host = request.url.hostname or ""
        return f"http://{'[' + host + ']' if ':' in host else host}:{parsed.port or SPOOLMAN_PORT}" if host else None
    return url


def _iso(unix: float) -> str:
    return datetime.fromtimestamp(unix, timezone.utc).astimezone().isoformat(timespec="seconds")


def _last_use(spool_id: int) -> dict | None:
    """The newest usage entry that names the spool, over every printer's log (the logs the kit keeps for undo)."""
    from ..bridge import usage
    best = None
    try:
        for serial, printer in registry.load().items():
            for e in usage.entries(serial):
                if e.get("spool_id") == spool_id and not e.get("undone") and (best is None or str(e.get("at")) > str(best[0].get("at"))):
                    best = (e, printer.name)
    except Exception:  # noqa: BLE001 - the log is a convenience: a spool without it still shows
        return None
    return {"at": best[0].get("at"), "printer": best[1], "grams": best[0].get("grams")} if best else None


def routes(page) -> list[tuple]:
    from ..setup_page import Refused

    def sm() -> Spoolman:
        return Spoolman(config.load()["spoolman_url"])

    def run(fn, *a):
        try:
            return fn(*a)
        except inventory.PageError as e:
            raise Refused(400, str(e), field=e.field) from None
        except inventory.SpoolEditError as e:
            raise Refused(400, str(e), field=e.field) from None
        except getattr(inventory, "NotCheckedOut", ()) as e:
            raise Refused(409, str(e) or NOT_CHECKED_OUT, code="not_checked_out") from None
        except inventory.TagConflict as e:
            raise Refused(409, str(e), reason=e.reason) from None
        except SpoolmanError as e:
            if e.status == 404:
                raise Refused(404, "no such spool") from None
            raise Refused(502, "Spoolman is not reachable" if e.status is None else str(e)) from None

    def low_g() -> float:
        return alerts.load()["running_low_g"]

    def listing(q: str, flt: str, offset: int, limit: int | None, url: str | None) -> dict:
        if flt and flt not in filters():
            raise Refused(400, f"filter must be one of {', '.join(filters())}", field="filter")
        low = low_g()
        words = q.lower().split()
        rows = []
        for s in sorted(sm().get("spool"), key=lambda x: x.get("id") or 0):
            v = view(s, low)
            if v["state"] == "empty" or not all(w in _words(s, v) for w in words):
                continue
            if flt in ("sealed", "opened") and v["state"] != flt or flt == "loaded" and not v["loaded"] or flt == "low" and not v["low"]                     or flt == "checked_out" and not v.get("checked_out"):
                continue
            rows.append(v)
        rows, paging = inventory.page(rows, offset, limit)
        return {"spools": rows, **paging, "filters": filters(), **({"spoolman_url": url} if url else {})}

    def one(spool_id: int) -> dict:
        return {"spool": view(sm().get(f"spool/{spool_id}"), low_g()), "last_use": _last_use(spool_id)}

    def edit(spool_id: int, b: dict) -> dict:
        client = sm()
        before = client.get(f"spool/{spool_id}")
        location = b.get("location")
        if location is not None and (not isinstance(location, str) or not location.strip()):
            raise inventory.SpoolEditError("location must be text", "location")
        for key in ("empty", "dry_run"):
            if b.get(key) is not None and not isinstance(b[key], bool):
                raise inventory.SpoolEditError(f"{key} must be true or false", key)
        auto = b.get("auto_location")
        if auto is not None and not isinstance(auto, bool):
            raise inventory.SpoolEditError("auto_location must be true or false", "auto_location")
        if auto and location is not None:
            raise inventory.SpoolEditError("give only one of location or auto_location", "auto_location")
        rules = storage.current()
        num = lambda k: b.get(k) if isinstance(b.get(k), (int, float)) and not isinstance(b.get(k), bool) else None  # noqa: E731
        for key in ("gross", "remaining"):
            if b.get(key) is not None and num(key) is None:
                raise inventory.SpoolEditError(f"{key} must be a number of grams", key)
        home = {"home": b["home"]} if b.get("home") is not None else {}        # the kit's home change
        out = inventory.edit_spool(client, spool_id, gross=num("gross"), remaining=num("remaining"), location=location,
                                   empty=bool(b.get("empty")), dry=b.get("dry_run") is not False, auto_location=bool(auto),
                                   locations=inventory.known_locations(client, rules.choosable()), rules=rules, **home)
        low = low_g()
        return {"spool": view(out["spool"], low), "before": view(before, low), "dry": out["dry"],
                **({"placed_text": out["placed_text"]} if "placed_text" in out else {})}

    def send_back(spool_id: int) -> dict:
        if not hasattr(inventory, "return_spool"):
            raise Refused(404, "this Hub cannot return a spool")
        out = inventory.return_spool(sm(), spool_id, rules=storage.current())
        v = view(out["spool"], low_g())
        return {"spool": v, "event": out.get("event"), "location": v["location"] or None}

    def last_tag() -> dict:
        found = tagsources.latest(config.load())
        if not found:
            return {"tag": None}
        at = found.get("read_at_unix") or time.time() - float(found["age_seconds"])
        return {"tag": {"uid": found["nfc_id"], "read_at": _iso(float(at)), "source": found.get("reader")}}

    def link(spool_id: int, b: dict) -> dict:
        tag = b.get("tag")
        if not isinstance(tag, str) or not tag.strip():
            raise Refused(400, "tag is required", field="tag")
        if b.get("dry_run") is not None and not isinstance(b["dry_run"], bool):
            raise Refused(400, "dry_run must be true or false", field="dry_run")
        dry = b.get("dry_run") is not False
        try:
            uid = tags.normalize_uid(tag)
        except ValueError as e:
            raise Refused(400, str(e), field="tag") from None
        client = sm()
        before = client.get(f"spool/{spool_id}")
        result = inventory.link_tag(client, spool_id, uid, force=False, dry=dry)
        low = low_g()
        if dry:                                           # the spool as the link would leave it
            after = {**before, **result.patch, "extra": {**(before.get("extra") or {}), "nfc_id": json.dumps(uid)}}
        else:
            after = client.get(f"spool/{spool_id}")
        return {"spool": view(after, low), "before": view(before, low), "dry": dry}

    def number(request, key: str, low: int, default: int | None) -> int | None:
        raw = request.query_params.get(key)
        if raw in (None, ""):
            return default
        try:
            n = int(raw)
        except ValueError:
            n = -1
        if n < low:
            raise Refused(400, f"{key} must be a whole number of at least {low}", field=key)
        return n

    async def get_list(request, body):
        q = request.query_params
        offset, limit = number(request, "offset", 0, 0), min(number(request, "limit", 1, PAGE), PAGE_MAX)
        return await anyio.to_thread.run_sync(run, lambda: listing(q.get("q", ""), q.get("filter", ""), offset, limit, _spoolman_url(request)))

    async def get_one(request, body):
        return await anyio.to_thread.run_sync(run, one, request.path_params["id"])

    async def post_one(request, body):
        sid = request.path_params["id"]
        r = await anyio.to_thread.run_sync(run, edit, sid, body)
        if not r["dry"]:
            page.audit(request, f"spool {sid} changed")
        return r

    async def post_return(request, body):
        sid = request.path_params["id"]
        r = await anyio.to_thread.run_sync(run, send_back, sid)
        page.audit(request, f"spool {sid} returned")
        return r

    async def get_last_tag(request, body):
        return await anyio.to_thread.run_sync(run, last_tag)

    async def post_link(request, body):
        sid = request.path_params["id"]
        r = await anyio.to_thread.run_sync(run, link, sid, body)
        if not r["dry"]:
            page.audit(request, f"spool {sid} tag linked")
        return r

    return [("/spools", page.guard(get_list), "GET"),
            ("/spools/last-tag", page.guard(get_last_tag), "GET"),
            ("/spools/{id:int}", page.guard(get_one), "GET"),
            ("/spools/{id:int}", page.guard(post_one, write=True), "POST"),
            ("/spools/{id:int}/link", page.guard(post_link, write=True), "POST"),
            ("/spools/{id:int}/return", page.guard(post_return, write=True), "POST")]
