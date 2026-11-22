"""History, analytics, export/import, undo, and report CLI commands (docs/design 06 s9.22-9.26, 09 Stage 13 HD2)."""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
import urllib.parse

from . import output
from .spools_cli import confirmed, down, fail

EXPORT_SETS = ("usage", "locations", "recent", "activity", "printers", "lines",
               "spoolman-spools", "spoolman-filaments")

# ---------- texts (catalogue keys) ----------

CLI_ACTIVITY_EMPTY = "Nothing has been done in the last {days} days."
CLI_ACTIVITY_COUNT_ONE = "1 event in the last {days} days."
CLI_ACTIVITY_COUNT = "{n} events in the last {days} days."
CLI_ACTIVITY_MORE = "Showing the newest 200. Narrow it with --days, --who or --spool."
CLI_WHO_UNKNOWN = "unknown actor {who}."
CLI_WHO_UNKNOWN_FIX = "available: {names}"
CLI_UNDO_NONE = "Nothing has been changed from the command line in the last 7 days."
CLI_UNDO_ASK = "Undo it?"
CLI_UNDO_DONE = "Put back."
CLI_UNDO_NOT_FOUND = "there is no event {id}."
CLI_STATUS_HISTORY_EMPTY = "No check changes in the last {days} days."
CLI_USAGE_EMPTY_MONTH = "No filament used this month."
CLI_USAGE_EMPTY = "No filament used yet."
CLI_EXPORT_WROTE = "Wrote {n} rows to {file}."
CLI_IMPORT_SUMMARY = "Checked {total} rows: {ok} can be added, {bad} has problems."
CLI_IMPORT_ASK_SPOOLS = "Add {n} spools?"
CLI_IMPORT_ASK_LOCATIONS = "Apply {n} location changes?"
CLI_IMPORT_NOTHING = "Nothing to add."
CLI_IMPORT_BAD_CSV = "this is not a CSV file that Spoolwarden can read."
CLI_IMPORT_DONE_SPOOLS = "Added {n} spools: {ids}."
CLI_IMPORT_DONE_LOCATIONS = "Added {add}, changed {change} locations."
CLI_REPORT_DONE = "Hub report written: {file}"
CLI_BACKUP_PW_SET = "Backup password is set."
CLI_BACKUP_PW_UNSET = "Backup password is not set."
CLI_BACKUP_PW_REMOVED = "Backup password removed."
CLI_BACKUP_PW_SAVED = "Backup password saved."


def catalogue_texts() -> dict[str, str]:
    """{catalogue key: English text} of every CLI_* constant in this module."""
    return {k.lower(): v for k, v in vars(sys.modules[__name__]).items()
            if k.startswith("CLI_") and isinstance(v, str)}


def _since_at(days: int) -> str:
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=max(1, int(days)))
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


def _who_label(actor: dict) -> str:
    t = actor.get("type", "")
    if actor.get("name"):
        return actor["name"]
    return {"hub": "The Hub", "page": "Hub page", "cli": "command line",
            "connector": "Claude connector"}.get(t, t or "The Hub")


def _resolve_who(api, who: str) -> tuple[str | None, list[str]]:
    """Map `--who <name>` to `actor=<type>[:<id>]`; returns (param, available_names)."""
    fixed = {
        "the hub": "hub",
        "hub": "hub",
        "hub page": "page",
        "page": "page",
        "command line": "cli",
        "cli": "cli",
        "claude connector": "connector",
        "connector": "connector",
    }
    low = who.strip().lower()
    if low in fixed:
        return fixed[low], []
    names = ["The Hub", "Hub page", "command line", "Claude connector"]
    try:
        info = api.get("/api/hub") or {}
        devs = info.get("devices") or []
    except Exception:  # noqa: BLE001
        devs = []
    for d in devs:
        dname = d.get("name") or ""
        if dname:
            names.append(dname)
        if dname.lower() == low or d.get("id") == who:
            return f"device:{d['id']}", names
    return None, names


# ---------- activity ----------

def cmd_activity(api, *, spool: int | None = None, who: str | None = None, days: int = 7) -> int:
    if api is None:
        return down()
    from .web.slots import SlotError
    params: list[tuple[str, str]] = [("activity", "1"), ("limit", "201"), ("since_at", _since_at(days))]
    if spool is not None:
        params.append(("subject", f"spool:{spool}"))
    if who:
        actor_param, names = _resolve_who(api, who)
        if actor_param is None:
            output.error(CLI_WHO_UNKNOWN.format(who=who), CLI_WHO_UNKNOWN_FIX.format(names=", ".join(names)))
            return output.USAGE
        params.append(("actor", actor_param))
    try:
        r = api.get("/api/events?" + urllib.parse.urlencode(params))
    except SlotError as e:
        return fail(e)
    evs = list(reversed(r.get("events") or []))
    more = len(evs) > 200
    evs = evs[:200]
    output.emit({**r, "events": evs})
    if not evs:
        output.out(CLI_ACTIVITY_EMPTY.format(days=days))
        return 0
    rows = []
    for ev in evs:
        mark = "undo" if (ev.get("undo") or {}).get("possible") else ("(undone)" if ev.get("undone") else "")
        what = ev.get("text") or ev.get("kind") or ""
        rows.append([ev.get("id"), ev.get("at", "")[:16].replace("T", " "), _who_label(ev.get("actor") or {}),
                     what, mark])
    output.out_lines(output.table(["ID", "TIME", "WHO", "WHAT", ""], rows, long=3, right=(0,)))
    output.out()
    output.out(CLI_ACTIVITY_COUNT_ONE.format(days=days) if len(evs) == 1
               else CLI_ACTIVITY_COUNT.format(n=len(evs), days=days))
    if more:
        output.out(CLI_ACTIVITY_MORE)
    return 0


# ---------- undo ----------

def cmd_undo(api, *, event_id: int | None = None, yes: bool = False) -> int:
    if api is None:
        return down()
    from .web.slots import SlotError
    target = None
    if event_id is None:
        try:
            r = api.get("/api/events?" + urllib.parse.urlencode([
                ("activity", "1"), ("actor", "cli"), ("since_at", _since_at(7)), ("limit", "50"),
            ]))
        except SlotError as e:
            return fail(e)
        evs = [e for e in reversed(r.get("events") or []) if not e.get("undone")]
        if not evs:
            output.emit({"ok": False, "none": True})
            output.out(CLI_UNDO_NONE)
            return 0
        target = evs[0]
        event_id = target["id"]
    else:
        try:
            r = api.get("/api/events?" + urllib.parse.urlencode([("since", str(max(0, int(event_id) - 1))),
                                                                 ("limit", "1")]))
            evs = r.get("events") or []
            target = next((e for e in evs if e.get("id") == int(event_id)), None)
        except SlotError as e:
            return fail(e)
        if target is None:
            output.error(CLI_UNDO_NOT_FOUND.format(id=event_id))
            return output.USAGE

    what = target.get("text") or target.get("kind") or f"event {event_id}"
    output.out(f"{target.get('at', '')[:16].replace('T', ' ')} · {what}")
    rc = confirmed(CLI_UNDO_ASK, f"undo event {event_id}", f"spoolwarden undo {event_id}", yes)
    if rc is not None:
        return rc
    try:
        res = api.post(f"/api/events/{event_id}/undo", {})
    except SlotError as e:
        output.emit({"ok": False, "error": getattr(e, "code", "error"), "text": str(e)})
        output.error(str(e))
        return output.FAILED
    output.emit({"ok": True, **res})
    output.out(CLI_UNDO_DONE)
    if res.get("note"):
        output.out(res["note"])
    return 0


# ---------- status --history ----------

def run_status_history(api, *, days: int = 7) -> int:
    if api is None:
        return down()
    from .web.slots import SlotError
    try:
        r = api.get(f"/api/health?days={30 if int(days) == 30 else 7}")
    except SlotError as e:
        return fail(e)
    output.emit(r)
    changes = r.get("changes") or []
    if not changes:
        output.out(CLI_STATUS_HISTORY_EMPTY.format(days=days))
        return 0
    rows = [[c.get("at", "")[:16].replace("T", " "), c.get("text") or c.get("key", ""),
             f"{c.get('from', '')} -> {c.get('to', '')}"] for c in changes]
    output.out_lines(output.table(["TIME", "CHECK", "CHANGE"], rows, long=1))
    return 0


# ---------- usage ----------

def cmd_usage(api, *, by: str | None = None, printer: str | None = None) -> int:
    if api is None:
        return down()
    from .web.slots import SlotError
    q = [("months", "12")]
    if printer:
        q.append(("printer", printer))
    try:
        r = api.get("/api/analytics?" + urllib.parse.urlencode(q))
    except SlotError as e:
        return fail(e)
    output.emit(r)
    if by == "month":
        months = r.get("months") or []
        if not months:
            output.out(CLI_USAGE_EMPTY)
            return 0
        rows = [[m.get("month", ""), f"{int(round(m.get('grams') or 0)):,}", m.get("prints", 0),
                 m.get("unfinished_prints", 0), m.get("top_material") or ""] for m in months]
        output.out_lines(output.table(["MONTH", "GRAMS", "PRINTS", "DID NOT FINISH", "MOST USED MATERIAL"],
                                      rows, right=(1, 2, 3)))
        return 0
    insights = r.get("insights") or []
    if not insights:
        output.out(CLI_USAGE_EMPTY_MONTH)
        return 0
    for item in insights:
        if item.get("text"):
            output.out(item["text"])
    for note in r.get("notes") or []:
        output.out(note)
    return 0


# ---------- export / import ----------

def cmd_export(api, *, dataset: str, out_file: str | None = None, days: int | None = None,
               printer: str | None = None) -> int:
    if api is None:
        return down()
    from .web.slots import SlotError
    q = []
    if days is not None:
        q.append(("days", str(days)))
    if printer:
        q.append(("printer", printer))
    route = f"/api/export/{dataset}.csv" + ("?" + urllib.parse.urlencode(q) if q else "")
    try:
        raw = api.get_raw(route) if hasattr(api, "get_raw") else api.get(route)
    except SlotError as e:
        return fail(e)
    text = raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else str(raw)
    if out_file:
        pathlib.Path(out_file).write_text(text, encoding="utf-8")
        nrows = max(0, len([ln for ln in text.splitlines() if ln.strip()]) - 1)
        output.emit({"ok": True, "file": out_file, "rows": nrows})
        output.out(CLI_EXPORT_WROTE.format(n=nrows, file=out_file))
    else:
        sys.stdout.write(text)
    return 0


def cmd_import(api, *, kind: str, file: str, yes: bool = False) -> int:
    if api is None:
        return down()
    from .web.slots import SlotError
    try:
        csv_text = pathlib.Path(file).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        output.error(CLI_IMPORT_BAD_CSV)
        return output.FAILED
    try:
        dry = api.post(f"/api/import/{kind}?dry_run=1", csv_text)
    except SlotError as e:
        return fail(e)
    rows = dry.get("rows") or []
    ok_count = sum(1 for r in rows if r.get("ok"))
    bad_count = len(rows) - ok_count
    output.out(CLI_IMPORT_SUMMARY.format(total=len(rows), ok=ok_count, bad=bad_count))
    if rows:
        trows = [[r.get("n"), r.get("line") or "left out",
                  "; ".join(p.get("text", "") for p in r.get("problems") or [])] for r in rows]
        output.out_lines(output.table(["ROW", "WHAT", "PROBLEMS"], trows, long=1, right=(0,)))
    add_n = int(dry.get("add") or 0) + int(dry.get("change") or 0)
    if add_n <= 0:
        output.emit({**dry, "dry": True})
        output.out(CLI_IMPORT_NOTHING)
        return output.FAILED
    ask = CLI_IMPORT_ASK_SPOOLS.format(n=dry.get("add", 0)) if kind == "spools" else CLI_IMPORT_ASK_LOCATIONS.format(
        n=add_n)
    rc = confirmed(ask, f"import {kind}", f"spoolwarden import {kind} {file}", yes)
    if rc is not None:
        return rc
    try:
        res = api.post(f"/api/import/{kind}?dry_run=0", csv_text)
    except SlotError as e:
        return fail(e)
    output.emit(res)
    if kind == "spools":
        ids = ", ".join(str(i) for i in (res.get("spool_ids") or []))
        output.out(CLI_IMPORT_DONE_SPOOLS.format(n=res.get("add", 0), ids=ids))
    else:
        output.out(CLI_IMPORT_DONE_LOCATIONS.format(add=res.get("add", 0), change=res.get("change", 0)))
    return 0


# ---------- backup password / restore --password-file / report ----------

def read_password_file(path: str) -> str:
    """Read a backup/restore password from `path` (single line, stripped of trailing newline)."""
    return pathlib.Path(path).read_text(encoding="utf-8").rstrip("\r\n")


def run_backup_password(api=None, *, remove: bool = False, show: bool = False, password: str | None = None) -> int:
    if api is None:
        return down()
    from .web.slots import SlotError
    try:
        if show:
            r = api.get("/setup/api/backup/password")
            output.emit(r)
            output.out(CLI_BACKUP_PW_SET if r.get("set") else CLI_BACKUP_PW_UNSET)
            return 0
        if remove:
            r = api.post("/setup/api/backup/password", {"remove": True})
            output.emit({"ok": True, **r})
            output.out(CLI_BACKUP_PW_REMOVED)
            return 0
        r = api.post("/setup/api/backup/password", {"password": password or ""})
        output.emit({"ok": True, **r})
        output.out(CLI_BACKUP_PW_SAVED)
        return 0
    except SlotError as e:
        return fail(e)


def cmd_report(api, *, out_file: str | None = None) -> int:
    if api is None:
        return down()
    from .web.slots import SlotError
    try:
        r = api.post("/setup/api/report", {})
    except SlotError as e:
        return fail(e)
    file_path = r.get("file") or ""
    if out_file and file_path:
        src = pathlib.Path(file_path)
        if src.exists():
            pathlib.Path(out_file).write_bytes(src.read_bytes())
    output.emit({"ok": True, "file": out_file or file_path})
    output.out(CLI_REPORT_DONE.format(file=out_file or file_path))
    return 0


# ---------- parser wiring ----------

def add_parser(sub) -> None:
    p = sub.add_parser("activity", help="show what was done on the Hub")
    p.add_argument("--spool", type=int, default=None, help="only this spool")
    p.add_argument("--who", default=None, help="filter by who did it")
    p.add_argument("--days", type=int, default=7, help="how many days back (default 7)")

    p = sub.add_parser("undo", help="undo a spool or slot change")
    p.add_argument("event_id", nargs="?", type=int, default=None, help="event id to undo (default: last CLI change)")
    p.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")

    p = sub.add_parser("usage", help="show filament used by printers")
    p.add_argument("--by", choices=["month"], default=None, help="show monthly table")
    p.add_argument("--printer", default=None, help="only this printer")

    p = sub.add_parser("export", help="export Hub data as CSV")
    p.add_argument("dataset", choices=EXPORT_SETS, help="which dataset to export")
    p.add_argument("--out", default=None, help="write to file instead of stdout")
    p.add_argument("--days", type=int, default=None, help="days to include (usage/activity)")
    p.add_argument("--printer", default=None, help="filter usage by printer")

    p = sub.add_parser("import", help="import spools or locations from CSV")
    p.add_argument("kind", choices=["spools", "locations"], help="what to import")
    p.add_argument("file", help="CSV file to import")
    p.add_argument("-y", "--yes", action="store_true", help="skip the confirmation prompt")

    p = sub.add_parser("report", help="collect a redacted Hub report")
    p.add_argument("--out", default=None, help="copy the report to this file")


def run(args: argparse.Namespace, api) -> int:
    if args.cmd == "activity":
        return cmd_activity(api, spool=args.spool, who=args.who, days=args.days)
    if args.cmd == "undo":
        return cmd_undo(api, event_id=args.event_id, yes=args.yes)
    if args.cmd == "usage":
        return cmd_usage(api, by=args.by, printer=args.printer)
    if args.cmd == "export":
        return cmd_export(api, dataset=args.dataset, out_file=args.out, days=args.days, printer=args.printer)
    if args.cmd == "import":
        return cmd_import(api, kind=args.kind, file=args.file, yes=args.yes)
    if args.cmd == "report":
        return cmd_report(api, out_file=args.out)
    return output.USAGE
