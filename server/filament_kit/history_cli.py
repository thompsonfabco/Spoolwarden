"""History, analytics, export/import, undo, and report CLI commands (the design, command line s9.22-9.26, 09 Stage 13 HD2)."""
from __future__ import annotations

import argparse
import datetime as dt
import pathlib
import sys
import urllib.parse

from . import output
from .spools_cli import confirmed, down, fail

EVENTS_NEWEST = 50          # `activity` without --days shows this many of the newest
EVENTS_DAYS_MAX = 1000      # `activity --days <n>` shows up to this many, oldest first
UNDO_LOOK = 50              # plain `undo` looks at this many of the newest command-line changes
WHO_LOOK = 1000             # `--who <device name>` finds the name among this many of the newest events

EXPORT_SETS = ("usage", "locations", "recent", "activity", "printers", "lines",
               "spoolman-spools", "spoolman-filaments")

# ---------- texts (catalogue keys) ----------

CLI_ACTIVITY_EMPTY = "Nothing has been done in the last {days} days."
CLI_ACTIVITY_EMPTY_EVER = "Nothing has been done yet."
CLI_ACTIVITY_COUNT_ONE = "1 event in the last {days} days."
CLI_ACTIVITY_COUNT = "{n} events in the last {days} days."
CLI_ACTIVITY_MORE = "Showing the first 1000. Narrow it with --days, --who or --spool."
CLI_ACTOR_HUB = "The Hub"
CLI_ACTOR_PAGE = "Hub page"
CLI_ACTOR_CLI = "command line"
CLI_ACTOR_CONNECTOR = "Claude connector"
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
CLI_BACKUP_PW_UNSET = "Backup password is not set."
CLI_BACKUP_PW_NEW = "New backup password, at least 10 characters: "
CLI_BACKUP_PW_AGAIN = "New backup password again: "
CLI_BACKUP_PW_MISMATCH = "the two passwords do not match."
CLI_BACKUP_PW_SHORT = "use at least 10 characters."
CLI_BACKUP_PW_WRITE_DOWN = ("Write this password down. Without it these backups cannot be restored, and Spoolwarden "
                            "cannot recover it.")
CLI_BACKUP_PW_SAVED = "Backup password set. Every backup is now encrypted."
CLI_BACKUP_PW_REMOVE_ASK = ("Remove the backup password? New backups are plain again. Backups already made stay "
                            "encrypted and need the password they were made with.")
CLI_BACKUP_PW_REMOVED = "Removed the backup password."


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
    return {"hub": CLI_ACTOR_HUB, "page": CLI_ACTOR_PAGE, "cli": CLI_ACTOR_CLI,
            "connector": CLI_ACTOR_CONNECTOR}.get(t, t or CLI_ACTOR_HUB)


def _resolve_who(api, who: str) -> tuple[str | None, list[str]]:
    """Map `--who <name>` to `actor=<type>[:<id>]`; returns (param, available_names). A device is found by the name
    or id it has in the newest events (the paired devices are not an API route)."""
    fixed = {
        "the hub": "hub",
        "hub": "hub",
        CLI_ACTOR_PAGE.lower(): "page",
        "page": "page",
        CLI_ACTOR_CLI: "cli",
        "cli": "cli",
        CLI_ACTOR_CONNECTOR.lower(): "connector",
        "connector": "connector",
    }
    low = who.strip().lower()
    if low in fixed:
        return fixed[low], []
    names = [CLI_ACTOR_HUB, CLI_ACTOR_PAGE, CLI_ACTOR_CLI, CLI_ACTOR_CONNECTOR]
    try:
        seen = (api.get("/api/events?" + urllib.parse.urlencode(
            [("activity", "1"), ("newest", str(WHO_LOOK))])) or {}).get("events") or []
    except Exception:  # noqa: BLE001 - the names are a courtesy; a name not found ends as "unknown actor"
        seen = []
    devs: dict[str, str] = {}
    for ev in seen:
        a = ev.get("actor") or {}
        if a.get("type") == "device" and a.get("id"):
            devs[str(a["id"])] = a.get("name") or ""
    for did, dname in devs.items():
        if dname and dname not in names:
            names.append(dname)
        if dname.lower() == low or did == who:
            return f"device:{did}", names
    return None, names


# ---------- activity ----------

def cmd_activity(api, *, spool: int | None = None, who: str | None = None, days: int | None = None) -> int:
    """the design, command line: without `days` the newest 50 (`newest=50`), newest first; with `days` the events
    from that time on (`since_at`), up to 1000, oldest first. `--json` prints the Hub's answer as it came."""
    if api is None:
        return down()
    from .web.slots import SlotError
    params: list[tuple[str, str]] = [("activity", "1")]
    if spool is not None:
        params.append(("subject", f"spool:{spool}"))
    if who:
        actor_param, names = _resolve_who(api, who)
        if actor_param is None:
            output.error(CLI_WHO_UNKNOWN.format(who=who), CLI_WHO_UNKNOWN_FIX.format(names=", ".join(names)))
            return output.USAGE
        params.append(("actor", actor_param))
    window = [("since_at", _since_at(days)), ("limit", str(EVENTS_DAYS_MAX))] if days is not None else [
        ("newest", str(EVENTS_NEWEST))]
    more = False
    try:
        r = api.get("/api/events?" + urllib.parse.urlencode(params + window))
        evs = list(r.get("events") or [])
        if days is not None and len(evs) >= EVENTS_DAYS_MAX:
            # a full page: more exist when the newest matching event is past the last one shown
            top = (api.get("/api/events?" + urllib.parse.urlencode(params + [("newest", "1")])) or {}).get("events")
            more = bool(top) and int(top[-1].get("id") or 0) > int(evs[-1].get("id") or 0)
    except SlotError as e:
        return fail(e)
    if days is None:
        evs.reverse()                                   # the newest first
    output.emit(r)
    if not evs:
        output.out(CLI_ACTIVITY_EMPTY.format(days=days) if days is not None else CLI_ACTIVITY_EMPTY_EVER)
        return 0
    rows = []
    for ev in evs:
        mark = "undo" if (ev.get("undo") or {}).get("possible") else ("(undone)" if ev.get("undone") else "")
        what = ev.get("text") or ev.get("kind") or ""
        rows.append([ev.get("id"), ev.get("at", "")[:16].replace("T", " "), _who_label(ev.get("actor") or {}),
                     what, mark])
    output.out_lines(output.table(["ID", "TIME", "WHO", "WHAT", ""], rows, long=3, right=(0,)))
    if days is None:
        return 0
    output.out()
    output.out(CLI_ACTIVITY_COUNT_ONE.format(days=days) if len(evs) == 1
               else CLI_ACTIVITY_COUNT.format(n=len(evs), days=days))
    if more:
        output.out(CLI_ACTIVITY_MORE)
    return 0


# ---------- undo ----------

def _need_root(command: str) -> int | None:
    """None when this runs as root; else the exit code after the `sudo` error. These commands read or change what the
    Hub's service user cannot reach, so they run as root and call the modules, not the Hub page's routes (D5)."""
    from . import cli
    if cli._is_root():
        return None
    return output.need_sudo(cli._sudo_command(command))


def cmd_undo(api, sm_factory, *, event_id: int | None = None, yes: bool = False) -> int:
    rc = _need_root("sudo spoolwarden undo" + (f" {event_id}" if event_id is not None else ""))
    if rc is not None:
        return rc
    if api is None:
        return down()
    from .web.slots import SlotError
    target = None
    if event_id is None:
        try:        # `newest` alone: the Hub refuses it together with since_at; the 7 days are cut here
            r = api.get("/api/events?" + urllib.parse.urlencode([
                ("activity", "1"), ("actor", "cli"), ("newest", str(UNDO_LOOK)),
            ]))
        except SlotError as e:
            return fail(e)
        cutoff = _since_at(7)
        # D15: plain `undo` is for the NEWEST change of this command line and never skips to an older one: when that one is already
        # undone (a compensating event of an earlier undo is the newest, too) or cannot be undone, it says so and names `activity`
        evs = [e for e in reversed(r.get("events") or []) if str(e.get("at") or "") >= cutoff]
        if not evs:
            output.emit({"ok": False, "none": True})
            output.out(CLI_UNDO_NONE)
            return 0
        target = evs[0]
        if not (target.get("undo") or {}).get("possible"):
            from .station.page import say as catalogue_say
            text = catalogue_say("cli_undo_newest_cannot")
            output.emit({"ok": False, "newest_cannot_be_undone": True, "id": target.get("id"), "text": text})
            output.out(text)
            return 0
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
    from . import event_sink, undo
    from .spoolman import SpoolmanError
    try:
        with event_sink.acting({"type": "cli"}):
            res = undo.undo_event(event_id, sm_factory(), actor={"type": "cli"})
    except (undo.EventNotFound, undo.UndoError, SpoolmanError) as e:
        output.emit({"ok": False, "error": type(e).__name__, "text": str(e)})
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

def cmd_export(sm_factory, *, dataset: str, out_file: str | None = None, days: int | None = None,
               printer: str | None = None) -> int:
    rc = _need_root(f"sudo spoolwarden export {dataset}")
    if rc is not None:
        return rc
    from . import csv_io
    params: dict = {}
    if days is not None:
        params["days"] = days
    if printer:
        params["printer"] = printer
    try:
        if dataset.startswith("spoolman-"):
            params["sm"] = sm_factory()
        data = csv_io.export_csv(dataset, **params)
    except csv_io.CsvError as e:
        output.error(e.message)
        return output.FAILED
    text = data.decode("utf-8") if isinstance(data, (bytes, bytearray)) else str(data)
    if out_file:
        with open(out_file, "w", encoding="utf-8", newline="") as fh:     # the CSV's own line ends, on every system
            fh.write(text)
        nrows = max(0, len([ln for ln in text.splitlines() if ln.strip()]) - 1)
        output.emit({"ok": True, "file": out_file, "rows": nrows})
        output.out(CLI_EXPORT_WROTE.format(n=nrows, file=out_file))
    else:
        sys.stdout.write(text)
    return 0


def _import_call(sm_factory, kind: str, csv_text: str, dry_run: bool) -> dict:
    """The Hub page's import (setup_console/history_tab._call_import), made as the command line."""
    from . import csv_io, event_sink
    with event_sink.acting({"type": "cli"}):
        if kind == "spools":
            return csv_io.import_spools(sm_factory(), csv_text, dry_run=dry_run)
        return csv_io.import_locations(csv_text, dry_run=dry_run)


def cmd_import(sm_factory, *, kind: str, file: str, yes: bool = False) -> int:
    rc = _need_root(f"sudo spoolwarden import {kind} {file}")
    if rc is not None:
        return rc
    from . import csv_io
    from .spoolman import SpoolmanError
    try:
        csv_text = pathlib.Path(file).read_bytes().decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        output.error(CLI_IMPORT_BAD_CSV)
        return output.FAILED
    try:
        dry = _import_call(sm_factory, kind, csv_text, True)
    except (csv_io.CsvError, ValueError, SpoolmanError) as e:
        output.error(getattr(e, "message", None) or str(e))
        return output.FAILED
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
        res = _import_call(sm_factory, kind, csv_text, False)
    except (csv_io.CsvError, ValueError, SpoolmanError) as e:
        output.error(getattr(e, "message", None) or str(e))
        return output.FAILED
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


def _ask_new_password() -> tuple[str | None, int]:
    """the design, command line: the new password, typed twice (both secret). (password, 0) or (None, exit code after the error)."""
    from . import backup, secret_input
    first = secret_input.ask(CLI_BACKUP_PW_NEW)
    if len(first) < backup.MIN_PASSWORD_LEN:
        output.error(CLI_BACKUP_PW_SHORT)
        return None, output.USAGE
    if secret_input.ask(CLI_BACKUP_PW_AGAIN) != first:
        output.error(CLI_BACKUP_PW_MISMATCH)
        return None, output.USAGE
    return first, 0


def run_backup_password(*, remove: bool = False, show: bool = False, password: str | None = None,
                        yes: bool = False) -> int:
    """`backup password`: the helper's own functions, as `backup keep` calls them (the password file
    is root-only). Setting asks twice unless `password` is given; `--remove` asks first; `--show` prints the password
    (its JSON form is only `{"set": ...}`, a stored secret is never in JSON)."""
    from . import backup, setup_helper
    try:
        if show:
            pw = backup.read_password() if backup.password_set() else None
            output.emit({"set": pw is not None})
            output.out(pw if pw is not None else CLI_BACKUP_PW_UNSET)
            return 0
        if remove:
            rc = confirmed(CLI_BACKUP_PW_REMOVE_ASK, "remove the backup password", "sudo spoolwarden backup password "
                           "--remove", yes)
            if rc is not None:
                return rc
            setup_helper._backup_password_remove({})
            output.emit({"ok": True, "set": False})
            output.out(CLI_BACKUP_PW_REMOVED)
            return 0
        if password is None:
            password, rc = _ask_new_password()
            if password is None:
                return rc
        elif len(password) < backup.MIN_PASSWORD_LEN:
            output.error(CLI_BACKUP_PW_SHORT)
            return output.USAGE
        setup_helper._backup_password_set({"password": password})
    except setup_helper.Refused as e:
        output.error(str(e).rstrip(".") + ".")
        return output.USAGE
    output.emit({"ok": True, "set": True})
    output.out(CLI_BACKUP_PW_WRITE_DOWN)
    output.out(CLI_BACKUP_PW_SAVED)
    return 0


def cmd_report(*, out_file: str | None = None) -> int:
    rc = _need_root("sudo spoolwarden report")
    if rc is not None:
        return rc
    from . import report
    try:
        path = report.collect(out=out_file)
    except OSError as e:
        output.error(str(e))
        return output.FAILED
    shown = out_file or str(path)
    output.emit({"ok": True, "file": shown})
    output.out(CLI_REPORT_DONE.format(file=shown))
    return 0


# ---------- parser wiring ----------

def add_parser(sub) -> None:
    p = sub.add_parser("activity", help="show what was done on the Hub")
    p.add_argument("--spool", type=int, default=None, help="only this spool")
    p.add_argument("--who", default=None, help="filter by who did it")
    p.add_argument("--days", type=int, default=None, help="how many days back (default: the newest 50 events)")

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


def run(args: argparse.Namespace, api, sm_factory=None) -> int:
    if sm_factory is None:
        from . import cli
        sm_factory = cli._default_spoolman
    if args.cmd == "activity":
        return cmd_activity(api, spool=args.spool, who=args.who, days=args.days)
    if args.cmd == "undo":
        return cmd_undo(api, sm_factory, event_id=args.event_id, yes=args.yes)
    if args.cmd == "usage":
        return cmd_usage(api, by=args.by, printer=args.printer)
    if args.cmd == "export":
        return cmd_export(sm_factory, dataset=args.dataset, out_file=args.out, days=args.days, printer=args.printer)
    if args.cmd == "import":
        return cmd_import(sm_factory, kind=args.kind, file=args.file, yes=args.yes)
    if args.cmd == "report":
        return cmd_report(out_file=args.out)
    return output.USAGE
