"""CSV export and import for Hub data and Spoolman files (HD5, the design, Hub page).

Exports (`export_csv`):
- `usage` (`days`, `printer`)
- `locations`
- `recent`
- `activity` (`days`)
- `printers` (omits address, serial, and access codes/API keys)
- `lines`
- `spoolman-spools` (proxied from Spoolman's own CSV export)
- `spoolman-filaments` (proxied from Spoolman's own CSV export)

Formula-injection guard (`safe_cell`): any cell value whose string form starts with
`=`, `+`, `-`, `@`, `\\t`, `\\r`, or `\\n` is prefixed with `'`.

Imports (`import_spools`, `import_locations`):
- Additive/updating only (never deletes existing spools or locations).
- Support `dry_run=True` by default.
"""
from __future__ import annotations

import csv
import datetime
import io
import logging
import re
import urllib.error
import urllib.request
from typing import Any

from . import alerts_api, config, demo, events, hostaddr, intake_history, inventory, storage
from .alerts_api import say
from .bridge import registry, usage as bambu_usage
from .bridge.klipper import usage as klipper_usage
from .profiles import lines as lines_mod
from .spoolman import Spoolman, SpoolmanError
from .station.page import say as catalogue_say

log = logging.getLogger("filament_kit.csv_io")

EXPORT_SETS = frozenset({
    "usage",
    "locations",
    "recent",
    "activity",
    "printers",
    "lines",
    "spoolman-spools",
    "spoolman-filaments",
})

_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r", "\n")
_BOOL_TRUE = frozenset({"yes", "true", "1", "y"})
_BOOL_FALSE = frozenset({"no", "false", "0", "n", ""})
_HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{6}(?:[0-9A-Fa-f]{2})?)$")

RECENT_FILAMENT_FIELDS = (
    "vendor",
    "name",
    "material",
    "color_hex",
    "line",
    "count",
    "density",
    "net",
    "tare",
    "sealed",
    "gross",
    "nozzle",
    "bed",
    "location",
    "lot",
    "price_per_kg",
    "note",
)


class CsvError(ValueError):
    """Base error for CSV export/import failures."""

    def __init__(self, message: str, *, status: int = 400, code: str = "bad_request"):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code


class SpoolmanExportUnavailable(CsvError):
    """Raised when Spoolman does not support its own CSV export endpoint."""

    def __init__(self, message: str | None = None):
        super().__init__(message or say("csv_export_unavailable"), status=501, code="spoolman_export_unavailable")


def safe_cell(value: Any) -> str:
    """Format a CSV cell value and guard against spreadsheet formula injection."""
    if value is None:
        return ""
    text = str(value)
    if text.startswith(_FORMULA_PREFIXES):
        return "'" + text
    return text


def _write_csv(headers: tuple[str, ...], rows: list[tuple[Any, ...]]) -> str:
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    for row in rows:
        writer.writerow([safe_cell(v) for v in row])
    return output.getvalue()


def _yes_no(value: Any) -> str:
    return "yes" if value else "no"


def _resolve_printers(printers_map: dict[str, registry.Printer], selector: str | None) -> list[registry.Printer]:
    items = sorted(printers_map.values(), key=lambda p: (registry.display_name(p).casefold(), p.serial))
    if not selector:
        return items
    sel = selector.strip()
    if not sel:
        return items
    if sel in printers_map:
        return [printers_map[sel]]
    low = sel.casefold()
    exact = [p for p in items if registry.display_name(p).casefold() == low]
    if exact:
        return exact
    prefix = [p for p in items if registry.display_name(p).casefold().startswith(low)]
    if len(prefix) == 1:
        return prefix
    if len(prefix) > 1:
        raise CsvError(say("csv_ambiguous_printer", selector=repr(selector)), status=400, code="ambiguous_printer")
    raise CsvError(say("csv_no_printer", selector=repr(selector)), status=404, code="printer_not_found")


def _export_usage(*, days: float = 30, printer: str | None = None, printers_mgr: Any = None) -> str:
    try:
        days_val = float(days)
    except (TypeError, ValueError):
        raise CsvError(say("csv_days_number"), status=400, code="bad_request") from None
    if days_val <= 0:
        raise CsvError(say("csv_days_above_zero"), status=400, code="bad_request")

    printers_map = registry.load()
    selected = _resolve_printers(printers_map, printer)
    collected: list[tuple[str, tuple[Any, ...]]] = []

    for p in selected:
        pname = registry.display_name(p)
        fam = getattr(p, "family", "bambu")
        entries: list[dict] = []
        if printers_mgr is not None and fam == "klipper" and getattr(printers_mgr, "klipper", None) is not None:
            try:
                entries = printers_mgr.klipper.usage(p.serial, days_val)
            except Exception:
                entries = []
        elif fam == "klipper":
            try:
                entries = klipper_usage.entries(p.serial, days=days_val)
            except Exception:
                entries = []
        else:
            try:
                entries = bambu_usage.entries(p.serial, days=days_val)
            except Exception:
                entries = []

        for e in entries:
            at = e.get("at") or ""
            taken_off = _yes_no(not e.get("observe") and e.get("spool_id") is not None)
            undone = _yes_no(bool(e.get("undone")))
            row = (
                at,
                e.get("printer_name") or pname,
                e.get("job") or "",
                e.get("slot") or "",
                e.get("spool_id") if e.get("spool_id") is not None else "",
                e.get("vendor") or "",
                e.get("name") or "",
                e.get("material") or "",
                e.get("grams") if e.get("grams") is not None else "",
                e.get("outcome") or "",
                taken_off,
                undone,
            )
            collected.append((str(at), row))

    collected.sort(key=lambda item: item[0], reverse=True)
    headers = (
        "time",
        "printer",
        "job",
        "slot",
        "spool",
        "vendor",
        "name",
        "material",
        "grams",
        "outcome",
        "taken_off",
        "undone",
    )
    return _write_csv(headers, [r for _, r in collected])


def _export_locations(*, st: storage.Storage | None = None) -> str:
    cur = st if st is not None else storage.current()
    rows: list[tuple[Any, ...]] = []
    for pos, loc in enumerate(cur.locations, start=1):
        rows.append((
            loc.name,
            loc.type,
            loc.limit if loc.limit is not None else "",
            loc.count if loc.count is not None else "",
            loc.prefix if loc.prefix is not None else "",
            loc.fill,
            ";".join(loc.materials or []),
            _yes_no(loc.overflow),
            _yes_no(loc.default_overflow),
            _yes_no(loc.sealed),
            pos,
        ))
    headers = (
        "name",
        "type",
        "limit",
        "count",
        "prefix",
        "fill",
        "materials",
        "overflow",
        "default_overflow",
        "sealed",
        "position",
    )
    return _write_csv(headers, rows)


def _export_recent() -> str:
    rows: list[tuple[Any, ...]] = []
    for rid in intake_history._ids_newest_first():
        try:
            rec = intake_history._load(rid)
        except Exception:
            continue
        revs = len(rec.get("revisions") or [])
        saved = rec.get("saved") or []
        if not saved:
            rows.append((
                rec.get("id", rid),
                rec.get("at", ""),
                *["" for _ in RECENT_FILAMENT_FIELDS],
                "",
                revs,
            ))
            continue
        for item in saved:
            vals = item.get("values") or {}
            spools = ";".join(str(sid) for sid in (item.get("spool_ids") or []))
            field_vals = []
            for key in RECENT_FILAMENT_FIELDS:
                val = vals.get(key)
                if key == "sealed" and isinstance(val, bool):
                    val = _yes_no(val)
                elif val is None:
                    val = ""
                field_vals.append(val)
            rows.append((
                rec.get("id", rid),
                rec.get("at", ""),
                *field_vals,
                spools,
                revs,
            ))
    headers = ("record", "added", *RECENT_FILAMENT_FIELDS, "spools", "revisions")
    return _write_csv(headers, rows)


def _actor_label(actor: Any) -> str:
    if isinstance(actor, dict):
        return str(actor.get("name") or actor.get("id") or actor.get("type") or "")
    return str(actor or "")


def _subject_label(subject: Any) -> str:
    if isinstance(subject, dict):
        t = subject.get("type") or ""
        i = subject.get("id") or ""
        return f"{t}:{i}" if (t and i) else str(t or i)
    return str(subject or "")


def _event_text(ev: dict) -> str:
    """The sentence the Hub page and the command line show for the event."""
    title, text = alerts_api.words(ev)
    return text or title


def _export_activity(*, days: float = 30) -> str:
    try:
        days_val = float(days)
    except (TypeError, ValueError):
        raise CsvError(say("csv_days_number"), status=400, code="bad_request") from None
    if days_val <= 0:
        raise CsvError(say("csv_days_above_zero"), status=400, code="bad_request")

    since_dt = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=days_val)
    all_events: list[dict] = []
    since_id = 0
    while True:
        page = events.read(since=since_id, limit=1000, since_at=since_dt, activity=True)
        batch = page.get("events") or []
        if not batch:
            break
        all_events.extend(batch)
        last_id = batch[-1]["id"]
        if len(batch) < 1000 or last_id <= since_id:
            break
        since_id = last_id

    all_events.reverse()
    rows = [
        (
            ev.get("at") or "",
            _actor_label(ev.get("actor")),
            ev.get("kind") or "",
            _subject_label(ev.get("subject")),
            _event_text(ev),
        )
        for ev in all_events
    ]
    return _write_csv(("time", "who", "kind", "subject", "text"), rows)


def export_printer_name(name: str, kind: str, serial: str, addresses: tuple[str, ...]) -> str:
    """The printer's name in printers.csv (the kit's, the command line's and the Hub page's export alike): as the owner
    chose it, or "" when it is an address or the serial (no address, serial or code). An address is an IP
    literal, a dotted host name, the printer's own address, or a Klipper name that reads as the host it was added at
    (a Klipper name defaults to the host, which may have changed since): one lower-case DNS label with a dash, such as
    "workshop-voron". A name with a capital ("Ender-3", "Voron") was typed by the owner; a Bambu Lab name never
    defaults to a host."""
    label = re.sub(r" \d+$", "", name)
    label = hostaddr.clean(re.sub(r"^(\[[^]]+\]|[^:]+):\d+$", r"\1", label))
    host_label = kind == "klipper" and "-" in label and label == label.lower()
    address_name = ("." in label or host_label or hostaddr.is_literal(label)) and hostaddr.form_error(label) is None
    if (address_name or label.casefold() in {(a or "").casefold() for a in addresses}
            or (kind == "bambu" and name == serial)):
        return ""
    return name


def _safe_printer_name(p: registry.Printer) -> str:
    return export_printer_name(registry.display_name(p), getattr(p, "family", "bambu"), p.serial,
                               (p.ip, getattr(p, "host", "") or ""))


def _slots_from_locations(sm: Spoolman | None) -> dict[str, list[dict]]:
    """{printer id: [{label, spool: {id}}]} from the spools Spoolman holds at a printer's slot location ("<name> – AMS
    <label>", "<name> – <label>"): where a slot's spool comes from in a printer's status too. For an export made without
    the Hub's printers (the command line, as root): the slots that hold a spool, in label order."""
    if sm is None:
        sm = Spoolman(config.load()["spoolman_url"])
    ctx = inventory.view_context()
    out: dict[str, list[dict]] = {}
    try:
        spools = sm.get("spool") or []
    except SpoolmanError:
        raise CsvError(catalogue_say("common_spoolman_unreachable"), status=502, code="spoolman_unreachable") from None
    for s in spools:
        where = ctx.slot_of(s.get("location")) if isinstance(s, dict) and not s.get("archived") else None
        if where is not None:
            out.setdefault(where["printer_id"], []).append({"label": where["slot"], "spool": {"id": s.get("id")}})
    for slots in out.values():
        slots.sort(key=lambda x: (x["label"].casefold(), x["spool"]["id"] if type(x["spool"]["id"]) is int else 0))
    return out


def _export_printers(*, printers_mgr: Any = None, sm: Spoolman | None = None) -> str:
    printers_map = registry.load()
    held = _slots_from_locations(sm) if printers_mgr is None and any(p.configured for p in printers_map.values()) else {}
    rows: list[tuple[Any, ...]] = []
    for p in sorted(printers_map.values(), key=lambda x: (registry.display_name(x).casefold(), x.serial)):
        name = _safe_printer_name(p)
        kind = getattr(p, "family", "bambu")
        model = p.model or ""
        st = None
        if p.configured and printers_mgr is not None:
            try:
                st = printers_mgr.status(p.serial)
            except Exception:
                st = None
        elif p.configured:
            st = {"ams": [{"slots": held.get(p.serial, [])}]}
        units = (st.get("ams") if isinstance(st, dict) else None) or []
        slots = [slot for unit in units for slot in (unit.get("slots") or [])]
        if not slots:
            rows.append((name, kind, model, "", ""))
            continue
        for slot in slots:
            spool_id = (slot.get("spool") or {}).get("id")
            rows.append((name, kind, model, slot.get("label") or "", spool_id if type(spool_id) is int else ""))
    return _write_csv(("name", "kind", "model", "slot", "spool"), rows)


def _export_lines() -> str:
    loaded = lines_mod.load()
    rows: list[tuple[Any, ...]] = []
    for ln in sorted(loaded.values(), key=lambda x: (x.vendor.casefold(), x.product.casefold(), x.material.casefold())):
        preset = ln.bambu_parent or ln.orca_parent or ""
        rows.append((ln.vendor, ln.product, ln.material, preset))
    return _write_csv(("vendor", "line", "material", "preset"), rows)


def _sanitize_csv_text(raw_text: str) -> str:
    reader = csv.reader(io.StringIO(raw_text))
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    for row in reader:
        writer.writerow([
            v if v.startswith("'") else safe_cell(v)
            for v in row
        ])
    return output.getvalue()


def _export_spoolman(kind: str, sm: Spoolman | None) -> str:
    demo.stop()                  # this reads Spoolman's own export (not its client): the demo has none
    if sm is None:
        cfg = config.load()
        sm = Spoolman(cfg["spoolman_url"])
    url = f"{sm.base_url}/api/v1/export/{kind}?fmt=csv"
    req = urllib.request.Request(url, method="GET", headers={"Accept": "text/csv, */*"})
    try:
        with urllib.request.urlopen(req, timeout=sm.timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code in (404, 405, 501):
            raise SpoolmanExportUnavailable() from e
        detail = e.read().decode(errors="replace")[:300]
        raise SpoolmanError(f"spoolman GET export/{kind} -> {e.code}: {detail}", status=e.code) from e
    except (urllib.error.URLError, OSError) as e:
        raise SpoolmanError("spoolman_unreachable", status=None) from e

    text = raw.decode("utf-8-sig", errors="replace")
    return _sanitize_csv_text(text)


def export_csv(
    export_set: str,
    *,
    days: float = 30,
    printer: str | None = None,
    sm: Spoolman | None = None,
    printers_mgr: Any = None,
    st: storage.Storage | None = None,
) -> str:
    """Generate a UTF-8 CSV string for one of the 8 supported export sets."""
    if export_set not in EXPORT_SETS:
        raise CsvError(say("csv_unknown_set", name=repr(export_set)), status=404, code="not_found")
    if export_set == "usage":
        return _export_usage(days=days, printer=printer, printers_mgr=printers_mgr)
    if export_set == "locations":
        return _export_locations(st=st)
    if export_set == "recent":
        return _export_recent()
    if export_set == "activity":
        return _export_activity(days=days)
    if export_set == "printers":
        return _export_printers(printers_mgr=printers_mgr, sm=sm)
    if export_set == "lines":
        return _export_lines()
    if export_set == "spoolman-spools":
        return _export_spoolman("spools", sm)
    return _export_spoolman("filaments", sm)


# ---------------------------------------------------------------------------
# CSV Import
# ---------------------------------------------------------------------------

def _decode_csv(data: bytes | str) -> list[dict[str, str]]:
    if isinstance(data, bytes):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise CsvError(say("csv_not_readable")) from None
    elif isinstance(data, str):
        text = data.lstrip("\ufeff")
    else:
        raise CsvError(say("csv_not_readable"))

    if not text.strip():
        raise CsvError(say("csv_not_readable"))
    try:
        reader = csv.DictReader(io.StringIO(text))
        if not reader.fieldnames:
            raise CsvError(say("csv_not_readable"))
        # Normalize header names
        fieldnames = [h.strip() if isinstance(h, str) else "" for h in reader.fieldnames]
        if any(not h for h in fieldnames):
            raise CsvError(say("csv_not_readable"))
        rows: list[dict[str, str]] = []
        for raw_row in reader:
            if None in raw_row:
                raise CsvError(say("csv_not_readable"))
            cleaned = {
                (k.strip() if isinstance(k, str) else k): (v.strip() if isinstance(v, str) else "")
                for k, v in raw_row.items()
            }
            if any(cleaned.values()):
                rows.append(cleaned)
        return rows
    except csv.Error:
        raise CsvError(say("csv_not_readable")) from None


def _parse_bool(raw: str, column: str, problems: list[dict[str, str]], default: bool = False) -> bool:
    low = raw.strip().lower()
    if not low:
        return default
    if low in _BOOL_TRUE:
        return True
    if low in _BOOL_FALSE:
        return False
    problems.append({"column": column, "text": say("csv_enter_yes_no", column=column)})
    return default


def _parse_int(raw: str, column: str, problems: list[dict[str, str]], *, default: int | None = None,
               min_val: int | None = None) -> int | None:
    s = raw.strip()
    if not s:
        return default
    try:
        val = int(s)
    except ValueError:
        problems.append({"column": column, "text": say("csv_enter_whole", column=column)})
        return default
    if min_val is not None and val < min_val:
        problems.append({"column": column, "text": say("csv_enter_whole_min", column=column, min=min_val)})
        return default
    return val


def _parse_float(raw: str, column: str, problems: list[dict[str, str]], *, default: float | None = None,
                 above_zero: bool = False) -> float | None:
    s = raw.strip()
    if not s:
        return default
    try:
        val = float(s)
    except ValueError:
        problems.append({"column": column, "text": say("csv_enter_number", column=column)})
        return default
    if above_zero and val <= 0:
        problems.append({"column": column, "text": say("csv_enter_number_above_zero", column=column)})
        return default
    return val


def _spool_row_to_item(row: dict[str, str]) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    problems: list[dict[str, str]] = []
    vendor = row.get("vendor", "").strip()
    name = row.get("name", "").strip()
    material = row.get("material", "").strip()
    if not vendor:
        problems.append({"column": "vendor", "text": say("csv_need_vendor")})
    if not name:
        problems.append({"column": "name", "text": say("csv_need_name")})
    if not material:
        problems.append({"column": "material", "text": say("csv_need_material")})

    net_raw = row.get("net", "").strip()
    if not net_raw:
        problems.append({"column": "net", "text": say("csv_need_net")})
        net = None
    else:
        net = _parse_float(net_raw, "net", problems, above_zero=True)
        if net is not None and net <= 0:
            problems.append({"column": "net", "text": say("csv_need_net")})

    count = _parse_int(row.get("count", ""), "count", problems, default=1, min_val=1)
    tare = _parse_float(row.get("tare", ""), "tare", problems, default=200.0, above_zero=True)
    density = _parse_float(row.get("density", ""), "density", problems, default=1.24, above_zero=True)
    sealed = _parse_bool(row.get("sealed", ""), "sealed", problems, default=False)
    gross = _parse_float(row.get("gross", ""), "gross", problems, default=None, above_zero=True)
    nozzle = _parse_int(row.get("nozzle", ""), "nozzle", problems, default=None)
    bed = _parse_int(row.get("bed", ""), "bed", problems, default=None)
    price_per_kg = _parse_float(row.get("price_per_kg", ""), "price_per_kg", problems, default=None)

    color_raw = row.get("color_hex", "").strip()
    color_hex = ""
    if color_raw:
        m = _HEX_RE.fullmatch(color_raw)
        if not m:
            problems.append({"column": "color_hex", "text": say("csv_bad_color")})
        else:
            color_hex = m.group(1)

    if gross is not None and count != 1:
        problems.append({"column": "gross", "text": say("csv_gross_count")})
    if gross is not None and tare is not None and gross <= tare:
        problems.append({"column": "gross", "text": say("csv_gross_tare")})

    if problems:
        return None, problems

    item: dict[str, Any] = {
        "vendor": vendor,
        "name": name,
        "material": material,
        "count": count,
        "net": net,
        "tare": tare,
        "density": density,
        "sealed": sealed,
    }
    if color_hex:
        item["color_hex"] = color_hex
    if gross is not None:
        item["gross"] = gross
    if nozzle is not None:
        item["nozzle"] = nozzle
    if bed is not None:
        item["bed"] = bed
    if price_per_kg is not None:
        item["price_per_kg"] = price_per_kg
    for opt in ("line", "location", "lot", "note", "bambu_parent"):
        val = row.get(opt, "").strip()
        if val:
            item[opt] = val

    return item, []


def import_spools(
    sm: Spoolman,
    data: bytes | str,
    *,
    dry_run: bool = True,
    rules: dict | None = None,
) -> dict[str, Any]:
    """Parse and validate a spools CSV, optionally adding new spools to Spoolman (additive only)."""
    rows_raw = _decode_csv(data)
    if not rows_raw:
        return {"rows": [], "add": 0, "dry": bool(dry_run)}

    required = {"vendor", "name", "material", "net"}
    header_keys = set(rows_raw[0].keys())
    if not required.issubset(header_keys):
        raise CsvError(say("csv_not_readable"))

    rules_eff = rules if rules is not None else storage.current()
    results: list[dict[str, Any]] = []
    valid_entries: list[tuple[int, dict[str, Any]]] = []

    for idx, raw_row in enumerate(rows_raw, start=1):
        item, problems = _spool_row_to_item(raw_row)
        if problems:
            results.append({"n": idx, "ok": False, "problems": problems, "line": say("csv_left_out")})
            continue
        assert item is not None
        valid_entries.append((idx - 1, item))
        net_str = f"{int(item['net']) if float(item['net']).is_integer() else item['net']} g"
        desc = say("csv_line_add_spools_at" if item.get("location") else "csv_line_add_spools", count=item["count"],
                   vendor=item["vendor"], name=item["name"], material=item["material"], net=net_str,
                   location=item.get("location") or "")
        results.append({"n": idx, "ok": True, "problems": [], "line": desc})

    total_add = sum(item["count"] for _, item in valid_entries)
    out: dict[str, Any] = {"rows": results, "add": total_add, "dry": bool(dry_run)}

    if valid_entries:
        items_only = [item for _, item in valid_entries]
        try:
            add_res = inventory.add_spools(sm, items_only, rules_eff, dry=bool(dry_run))
            for (res_idx, _), line_text in zip(valid_entries, add_res.lines):
                if line_text:
                    results[res_idx]["line"] = line_text
            if not dry_run:
                out["spool_ids"] = list(add_res.spool_ids)
        except inventory.ItemsInvalid as e:
            for prob in e.problems:
                item_pos = prob.get("item")
                if isinstance(item_pos, int) and 0 <= item_pos < len(valid_entries):
                    res_idx = valid_entries[item_pos][0]
                    results[res_idx]["ok"] = False
                    results[res_idx]["line"] = say("csv_left_out")
                    results[res_idx]["problems"].append({
                        "column": prob.get("field") or "row",
                        "text": prob.get("message") or str(e),
                    })
            out["add"] = sum(
                item["count"]
                for res_idx, item in valid_entries
                if results[res_idx]["ok"]
            )
            if not dry_run:
                raise CsvError(str(e), status=400, code="invalid_items") from e

    return out


def import_locations(
    data: bytes | str,
    *,
    dry_run: bool = True,
    before: storage.Storage | None = None,
) -> dict[str, Any]:
    """Parse and validate a locations CSV; adds new locations and updates same-named ones (never deletes)."""
    rows_raw = _decode_csv(data)
    if not rows_raw:
        return {"rows": [], "add": 0, "change": 0, "dry": bool(dry_run)}

    if "name" not in rows_raw[0]:
        raise CsvError(say("csv_not_readable"))

    before_st = before if before is not None else storage.current()
    merged = storage.copy_of(before_st)

    results: list[dict[str, Any]] = []
    # Map index in merged.locations -> CSV row index (0-based in results)
    loc_to_csv: dict[int, int] = {}
    row_kind: dict[int, str] = {}                 # results index -> "add" or "change" (not read back from the text)
    add_count = 0
    change_count = 0

    for idx, raw_row in enumerate(rows_raw, start=1):
        problems: list[dict[str, str]] = []
        name = raw_row.get("name", "").strip()
        if not name:
            problems.append({"column": "name", "text": say("csv_need_location_name")})

        loc_type = raw_row.get("type", "").strip().lower() or storage.BULK
        if loc_type not in (storage.BULK, storage.CONTAINERS):
            problems.append({"column": "type", "text": say("csv_bad_type")})

        limit = _parse_int(raw_row.get("limit", ""), "limit", problems, default=None, min_val=1)
        count = _parse_int(raw_row.get("count", ""), "count", problems, default=None, min_val=1)
        prefix = raw_row.get("prefix", "").strip() or None
        fill = raw_row.get("fill", "").strip().lower() or storage.NONE
        if fill not in (storage.NONE, storage.ANY, storage.MATERIALS):
            problems.append({"column": "fill", "text": say("csv_bad_fill")})

        mats_raw = raw_row.get("materials", "").strip()
        materials = [m.strip() for m in re.split(r"[;,]", mats_raw) if m.strip()] if mats_raw else []
        overflow = _parse_bool(raw_row.get("overflow", ""), "overflow", problems, default=False)
        default_overflow = _parse_bool(raw_row.get("default_overflow", ""), "default_overflow", problems, default=False)
        sealed = _parse_bool(raw_row.get("sealed", ""), "sealed", problems, default=False)

        if problems:
            results.append({"n": idx, "ok": False, "problems": problems, "line": say("csv_left_out")})
            continue

        existing = merged.by_name(name)
        new_loc = storage.Location(
            name=name,
            type=loc_type,
            id=existing.id if existing is not None else None,
            limit=limit if loc_type == storage.BULK else None,
            count=count if loc_type == storage.CONTAINERS else None,
            prefix=prefix if loc_type == storage.CONTAINERS else None,
            fill=fill,
            materials=materials,
            overflow=overflow,
            default_overflow=default_overflow,
            sealed=sealed,
        )
        if existing is not None:
            pos = merged.locations.index(existing)
            merged.locations[pos] = new_loc
            loc_to_csv[pos] = idx - 1
            change_count += 1
            desc = say("csv_line_change_location", name=name, type=loc_type)
            row_kind[idx - 1] = "change"
        else:
            pos = len(merged.locations)
            merged.locations.append(new_loc)
            loc_to_csv[pos] = idx - 1
            add_count += 1
            desc = say("csv_line_add_location", name=name, type=loc_type)
            row_kind[idx - 1] = "add"
        results.append({"n": idx, "ok": True, "problems": [], "line": desc})

    val_problems = storage.validate(merged.locations, before_st.locations)
    for vp in val_problems:
        if vp.row is not None and vp.row in loc_to_csv:
            r_idx = loc_to_csv[vp.row]
            results[r_idx]["ok"] = False
            results[r_idx]["line"] = say("csv_left_out")
            results[r_idx]["problems"].append({"column": vp.field, "text": say("csv_problem_line", field=vp.field, error=vp.error)})
        elif vp.row is None and results:
            results[0]["ok"] = False
            results[0]["problems"].append({"column": vp.field, "text": say("csv_problem_line", field=vp.field, error=vp.error)})

    has_errors = any(not r["ok"] for r in results)
    if has_errors:
        add_count = sum(1 for i, r in enumerate(results) if r["ok"] and row_kind.get(i) == "add")
        change_count = sum(1 for i, r in enumerate(results) if r["ok"] and row_kind.get(i) == "change")

    if not dry_run:
        if has_errors:
            first_msg = next(
                (p["text"] for r in results for p in r["problems"]),
                say("csv_locations_invalid"),
            )
            raise CsvError(first_msg, status=400, code="invalid_locations")
        storage.save(merged, before=before_st)

    return {
        "rows": results,
        "add": add_count,
        "change": change_count,
        "dry": bool(dry_run),
    }
