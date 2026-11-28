"""History, Undo, Health, CSV export/import and Hub report routes for the Setup Console.

    GET  /setup/api/activity                  (open)     activity events with actor/subject/kind filters
    POST /setup/api/events/{id}/undo          (code)     undo a reversible event
    GET  /setup/api/health                    (open)     check health history (days=7|30)
    GET  /setup/api/export/{set}.csv          (code)     CSV export download
    POST /setup/api/import/spools             (code)     CSV spool import (raw body, X-Setup-Code)
    POST /setup/api/import/locations          (code)     CSV location import (raw body, X-Setup-Code)
    POST /setup/api/report                    (code)     start the Hub report job
    GET  /setup/api/report/download           (code)     download newest Hub report (text/plain)
    POST /setup/api/report/download           (code)     download newest Hub report (text/plain)
"""
from __future__ import annotations

import csv
import datetime
import io

import anyio
from starlette.responses import Response, StreamingResponse

from .. import alerts_api, csv_io, event_sink, events, health, redact, trusted, undo
from ..spoolman import SpoolmanError
from .texts import say

EXPORT_SETS = (
    "usage",
    "locations",
    "recent",
    "activity",
    "enterprise_activity",
    "printers",
    "lines",
    "spoolman-spools",
    "spoolman-filaments",
)
CATEGORY_KINDS = {
    "auth": "auth.",
    "enterprise": "enterprise.",
    "cert": "cert.",
    "inventory": "spool.,slot.,location.,catalog.,record.",
    "printers": "printer.,usage.",
    "system": "hub.,backup.,device.,settings.",
}
MAX_IMPORT_BYTES = 5 * 1024 * 1024


async def page_actor(page, request) -> dict:
    """Actor identity for Hub page actions: the trusted computer's name when available, else 'Hub page'."""
    token = request.cookies.get(trusted.COOKIE)
    if token and trusted.TOKEN.fullmatch(token):
        try:
            st = await page._helper({"op": "trust_status", "token": token})
            if st.get("trusted") and st.get("name"):
                return {"type": "page", "id": st.get("id"), "name": st["name"]}
        except Exception:  # noqa: BLE001
            pass
    return {"type": "page", "name": "Hub page"}


async def _require_get_code(page, request) -> str | None:
    """Enforce the setup code (via X-Setup-Code header or trusted cookie) on a GET download route."""
    body = {"code": request.headers.get("x-setup-code", "")}
    token = page._trusted_token(request, body)
    await page._code_ok(request, body)
    return token


def _parse_int(raw: str | None, name: str, *, minimum: int | None = None) -> int | None:
    from ..setup_page import Refused
    if raw in (None, ""):
        return None
    try:
        val = int(raw)
    except ValueError:
        raise Refused(400, say("hub_number_needed", name=name), field=name) from None
    if minimum is not None and val < minimum:
        raise Refused(400, say("hub_number_minimum", name=name, minimum=minimum), field=name)
    return val


def _call_events_answer(*, since: int | None, kinds: str | None, limit: int,
                        subject: str | None, actor: str | None) -> dict:
    window = {"since": since, "limit": limit} if since is not None else {"newest": limit}
    return alerts_api.events_answer(**window, kinds=kinds, activity=True, subject=subject, actor=actor)


def _spoolman(page):
    if getattr(page, "spoolman", None) is not None:
        return page.spoolman
    from .. import config, spoolman
    return spoolman.Spoolman(config.load()["spoolman_url"])


def _call_undo(page, event_id: int, actor: dict) -> dict:
    return undo.undo_event(event_id, _spoolman(page), actor=actor)


def _safe_csv_cell(value) -> str:
    text = "" if value is None else str(value)
    if text.startswith(("=", "+", "-", "@", "\t", "\r", "\n")):
        return "'" + text
    return text


def _export_enterprise_activity(*, days: float = 30) -> str:
    try:
        days_val = float(days)
    except (TypeError, ValueError):
        raise csv_io.CsvError(alerts_api.say("csv_days_number"), status=400, code="bad_request") from None
    if days_val <= 0:
        raise csv_io.CsvError(alerts_api.say("csv_days_above_zero"), status=400, code="bad_request")

    since_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_val)
    all_events: list[dict] = []
    since_id = 0
    while True:
        page = events.read(
            since=since_id,
            limit=1000,
            kinds=("auth.", "enterprise.", "cert."),
            since_at=since_dt,
            activity=True,
        )
        batch = page.get("events") or []
        if not batch:
            break
        all_events.extend(batch)
        last_id = batch[-1]["id"]
        if len(batch) < 1000 or last_id <= since_id:
            break
        since_id = last_id

    all_events.reverse()
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(("id", "at", "kind", "actor_name", "subject_name", "details"))
    for ev in all_events:
        actor = ev.get("actor") or {}
        subject = ev.get("subject") or {}
        title, text = alerts_api.words(ev)
        details = text or title
        writer.writerow((
            _safe_csv_cell(ev.get("id", "")),
            _safe_csv_cell(ev.get("at", "")),
            _safe_csv_cell(ev.get("kind", "")),
            _safe_csv_cell(actor.get("name") or actor.get("id") or actor.get("type") or ""),
            _safe_csv_cell(subject.get("name") or subject.get("id") or ""),
            _safe_csv_cell(details),
        ))
    return buf.getvalue()


def _call_export(page, set_name: str, params: dict) -> str | bytes:
    if set_name == "enterprise_activity":
        return _export_enterprise_activity(**params)
    if set_name.startswith("spoolman-") and "sm" not in params:
        params = {**params, "sm": _spoolman(page)}
    return csv_io.export_csv(set_name, **params)


def _call_import(page, kind: str, raw_text: str, dry_run: bool, actor: dict) -> dict:
    with event_sink.acting(actor):
        if kind == "spools":
            return csv_io.import_spools(_spoolman(page), raw_text, dry_run=dry_run)
        return csv_io.import_locations(raw_text, dry_run=dry_run)


def routes(page) -> list[tuple]:
    from ..setup_page import NEED_CODE, Refused

    g = page.guard
    w = lambda fn: page.guard(fn, write=True)  # noqa: E731
    hw = lambda fn: page.guard(fn, write="helper")  # noqa: E731
    rw = lambda fn: page.guard(fn, write=True, raw=True)  # noqa: E731

    async def activity(request, body):
        q = request.query_params
        since = _parse_int(q.get("since"), "since", minimum=0)
        newest = _parse_int(q.get("newest"), "newest", minimum=1)        # D1: the newest N, oldest first
        if newest is not None and since is not None:
            raise Refused(400, say("hub_newest_with_since"), field="newest")
        limit = newest or _parse_int(q.get("limit"), "limit", minimum=1) or events.LIMIT_DEFAULT
        subject = (q.get("subject") or "").strip() or None
        actor = (q.get("actor") or "").strip() or None
        kinds = (q.get("kinds") or "").strip() or None
        category = (q.get("category") or "").strip().lower()
        if category and not kinds:
            kinds = CATEGORY_KINDS.get(category, "__none__")
        return await anyio.to_thread.run_sync(
            lambda: _call_events_answer(since=since, kinds=kinds, limit=limit, subject=subject, actor=actor)
        )

    async def undo_route(request, body):
        raw_id = request.path_params.get("id", "")
        try:
            event_id = int(raw_id)
        except (TypeError, ValueError):
            raise Refused(404, say("hub_no_such_event")) from None
        actor = await page_actor(page, request)
        try:
            res = await anyio.to_thread.run_sync(_call_undo, page, event_id, actor)
        except LookupError as e:
            raise Refused(404, str(e) or say("hub_no_such_event")) from None
        except SpoolmanError:
            raise Refused(502, say("hub_undo_spoolman_down")) from None
        except Exception as e:
            status = getattr(e, "status", None)
            code = getattr(e, "code", None) or getattr(e, "error", None)
            if status in (404, 409) or code in ("changed_since", "too_old", "already_undone", "not_undoable"):
                extra = {"code": code} if code else {}
                raise Refused(int(status or 409), str(e), **extra) from None
            raise
        page.audit(request, f"event {event_id} undone")
        return res

    async def health_route(request, body):
        days = _parse_int(request.query_params.get("days"), "days")
        if days is None:
            days = 7
        if days not in (7, 30):
            raise Refused(400, say("hub_health_days"), field="days")

        def run() -> dict:
            res = health.health_view(days)
            known = redact.known_values()
            for bucket in ("checks", "changes"):
                for row in res.get(bucket) or ():
                    if isinstance(row, dict) and isinstance(row.get("text"), str):
                        row["text"] = redact.text(row["text"], known)
            return res

        res = await anyio.to_thread.run_sync(run)
        return {"days": days, **res}

    async def export_csv(request, body):
        token = await _require_get_code(page, request)
        set_name = request.path_params.get("set", "")
        if set_name not in EXPORT_SETS:
            raise Refused(404, say("hub_unknown_export", name=set_name))
        q = request.query_params
        params: dict = {}
        if "days" in q and q["days"] != "":
            params["days"] = _parse_int(q["days"], "days", minimum=1)
        if "printer" in q and q["printer"].strip():
            params["printer"] = q["printer"].strip()
        try:
            data = await anyio.to_thread.run_sync(_call_export, page, set_name, params)
        except csv_io.CsvError as e:
            raise Refused(e.status, e.message, code=e.code) from None
        page.audit(request, f"{set_name} CSV downloaded")
        if token:
            request.state.set_trust = token
        return Response(
            data,
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{set_name}.csv"'},
        )

    async def _import_csv(request, kind: str):
        raw_bytes = bytearray()
        async for chunk in request.stream():
            if len(raw_bytes) + len(chunk) > MAX_IMPORT_BYTES:
                raise Refused(413, say("hub_csv_too_large"))
            raw_bytes.extend(chunk)
        try:
            raw_text = raw_bytes.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise Refused(400, say("hub_csv_not_utf8")) from None
        dry_run = request.query_params.get("dry_run", "1").lower() not in ("0", "false", "no")
        actor = await page_actor(page, request)
        try:
            res = await anyio.to_thread.run_sync(_call_import, page, kind, raw_text, dry_run, actor)
        except ValueError as e:
            raise Refused(400, str(e)) from None
        if not dry_run:
            page.audit(request, f"{kind} CSV imported")
        return res

    async def import_spools(request, body):
        return await _import_csv(request, "spools")

    async def import_locations(request, body):
        return await _import_csv(request, "locations")

    async def report_start(request, body):
        await page._helper({"op": "job", "name": "report", "code": body["code"]})
        page.audit(request, "Hub report started")
        return {"ok": True, "name": "report"}

    async def _serve_report(request, code):
        reply, fh = await page._helper_file({"op": "report_open", "code": code})
        name = reply["download"]
        page.audit(request, f"Hub report {name} downloaded")

        def chunks():
            with fh:
                while data := fh.read(64 * 1024):
                    yield data

        return StreamingResponse(chunks(), media_type="text/plain; charset=utf-8",
                                 headers={"Content-Disposition": f'attachment; filename="{name}"',
                                          "Content-Length": str(reply["size"])})

    async def report_download_get(request, body):
        credentials = {"code": request.headers.get("x-setup-code", "")}
        token = page._trusted_token(request, credentials)
        if not credentials["code"].strip():
            raise Refused(403, NEED_CODE, field="code")
        resp = await _serve_report(request, credentials["code"])
        if token:
            request.state.set_trust = token
        return resp

    async def report_download_post(request, body):
        return await _serve_report(request, body["code"])

    return [
        ("/activity", g(activity), "GET"),
        ("/events/{id}/undo", w(undo_route), "POST"),
        ("/health", g(health_route), "GET"),
        ("/export/{set}.csv", g(export_csv), "GET"),
        ("/import/spools", rw(import_spools), "POST"),
        ("/import/locations", rw(import_locations), "POST"),
        ("/report", hw(report_start), "POST"),
        ("/report/download", g(report_download_get), "GET"),
        ("/report/download", hw(report_download_post), "POST"),
    ]
