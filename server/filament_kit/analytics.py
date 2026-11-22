"""Monthly filament usage analytics (R84 section 5 / HD3).

Aggregates Bambu (`usage.jsonl`) and Klipper (`usage.xml`) filament-used logs into monthly totals,
printer and material breakdowns, plain-language insights for the current month, and honesty notes.
"""
from __future__ import annotations

import datetime as dt
import time
from typing import Any

from . import config, events
from .alerts_api import say
from .bridge import registry, usage as busage
from .bridge.klipper import usage as kusage
from .spoolman import Spoolman, SpoolmanError

REMOVED_PRINTER_ID = "_removed"
REMOVED_PRINTER_NAME = say("analytics_removed_printer")
UNKNOWN_MATERIAL = say("analytics_unknown_material")
_UNFINISHED = frozenset({"failed", "cancelled", "unknown"})


def _fmt_weight(grams: float) -> str:
    if grams >= 1000:
        return f"{grams / 1000:.2f} kg"
    return f"{round(grams)} g"


def _month_keys(count: int, now: float) -> list[str]:
    cur = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    y, m = cur.year, cur.month
    keys: list[str] = []
    for _ in range(count):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    keys.reverse()
    return keys


def _entry_month(e: dict) -> str | None:
    at = e.get("at")
    if isinstance(at, str) and len(at) >= 7 and at[4] == "-":
        return at[:7]
    ended = e.get("ended")
    if isinstance(ended, (int, float)) and not isinstance(ended, bool):
        return dt.datetime.fromtimestamp(ended, dt.timezone.utc).strftime("%Y-%m")
    return None


def _is_logged_only(e: dict) -> bool:
    if e.get("source") == "moonraker":
        return e.get("spool_id") is None
    return bool(e.get("observe")) or e.get("spool_id") is None or bool(e.get("error"))


def _default_spoolman() -> Spoolman | None:
    try:
        url = (config.load().get("spoolman_url") or "").strip()
        return Spoolman(url) if url else None
    except Exception:  # noqa: BLE001 - unconfigured or unreadable config must not break analytics
        return None


def _spool_material(spool_id: Any, sm: Spoolman | None, cache: dict[int, str | None], state: dict) -> str | None:
    if not isinstance(spool_id, int) or isinstance(spool_id, bool):
        return None
    if spool_id in cache:
        return cache[spool_id]
    if sm is None or state.get("down"):
        return None
    try:
        spool = sm.get(f"spool/{spool_id}")
        mat = ((spool.get("filament") or {}).get("material") or "").strip() or None
    except SpoolmanError as e:
        if e.status != 404:
            state["down"] = True
        mat = None
    except Exception:  # noqa: BLE001
        state["down"] = True
        mat = None
    cache[spool_id] = mat
    return mat


def _collect_entries(days: float, printer_filter: str | None) -> list[tuple[str, str, dict]]:
    """Return `[(group_id, display_name, entry), ...]` across all active and removed printers."""
    known = registry.load()
    printers_dir = config.paths().state / "printers"
    seen: set[str] = set()
    sources: list[tuple[str, str, str, bool]] = []

    for pid, p in known.items():
        seen.add(pid)
        sources.append((pid, pid, registry.display_name(p), p.family == "klipper"))

    if printers_dir.is_dir():
        for d in sorted(printers_dir.iterdir()):
            if not d.is_dir() or d.name in seen:
                continue
            has_jsonl = (d / busage.FILE).is_file() or (d / busage.DIR).is_dir()
            has_xml = (d / kusage.FILE).is_file()
            if not (has_jsonl or has_xml):
                continue
            sources.append((d.name, REMOVED_PRINTER_ID, REMOVED_PRINTER_NAME, has_xml and not has_jsonl))

    out: list[tuple[str, str, dict]] = []
    for serial, gid, gname, is_klipper in sources:
        if printer_filter is not None and serial != printer_filter and gid != printer_filter:
            continue
        try:
            items = kusage.entries(serial, days) if is_klipper else busage.entries(serial, days)
        except busage.UsageNotFound:
            continue
        if isinstance(items, list):
            for e in items:
                out.append((gid, gname, e))
    return out


def _spools_finished_in_month(month: str) -> int:
    try:
        y, m = (int(x) for x in month.split("-"))
        start = dt.datetime(y, m, 1, tzinfo=dt.timezone.utc)
        n, cur = 0, 0
        while True:
            res = events.read(cur, 1000, "spool.emptied", since_at=start, include_undo=True)
            got = res.get("events", [])
            # an undo writes a spool.emptied of its own (undo_of set), and an undone one never counted
            n += sum(1 for ev in got if str(ev.get("at") or "")[:7] == month and ev.get("undo_of") is None
                     and not ev.get("undone"))
            if len(got) < 1000 or res["next"] <= cur:
                return n
            cur = res["next"]
    except Exception:  # noqa: BLE001
        return 0


def view(months: int = 12, printer: str | None = None, *, sm: Spoolman | None = None,
         now: float | None = None) -> dict:
    """Return `{months, insights, notes}` for the last `months` (1..13) months, optionally filtered to one printer."""
    if isinstance(months, bool) or not isinstance(months, int) or not 1 <= months <= 13:
        raise ValueError("months must be 1 to 13")
    printer_filter = (printer or "").strip() or None
    ts = time.time() if now is None else float(now)
    keys = _month_keys(months, ts)
    oldest_y, oldest_m = (int(x) for x in keys[0].split("-"))
    oldest_ts = dt.datetime(oldest_y, oldest_m, 1, tzinfo=dt.timezone.utc).timestamp()
    days = max(1.0, (ts - oldest_ts) / 86400.0 + 1.0)

    raw_entries = _collect_entries(days, printer_filter)
    spool_client = sm if sm is not None else _default_spoolman()
    spool_cache: dict[int, str | None] = {}
    sm_state: dict = {}

    buckets: dict[str, dict] = {
        k: {
            "month": k,
            "grams": 0.0,
            "prints": 0,
            "unfinished_prints": 0,
            "unfinished_grams": 0.0,
            "logged_only_grams": 0.0,
            "no_weight_prints": 0,
            "_printers": {},
            "_materials": {},
            "_biggest": None,
        }
        for k in keys
    }

    for gid, gname, e in raw_entries:
        if e.get("undone"):
            continue
        m = _entry_month(e)
        if m not in buckets:
            continue
        b = buckets[m]
        b["prints"] += 1

        raw_g = e.get("grams")
        has_weight = isinstance(raw_g, (int, float)) and not isinstance(raw_g, bool)
        g = float(raw_g) if (has_weight and raw_g > 0) else 0.0
        if not has_weight:
            b["no_weight_prints"] += 1
        elif g > 0:
            b["grams"] += g
            if _is_logged_only(e):
                b["logged_only_grams"] += g
            job_name = (e.get("job") or e.get("file") or "").strip()
            if job_name and (b["_biggest"] is None or g > b["_biggest"][0]):
                b["_biggest"] = (g, job_name)

        if e.get("outcome") in _UNFINISHED:             # an entry without an outcome (before it was kept) is not counted
            b["unfinished_prints"] += 1
            b["unfinished_grams"] += g

        p_rec = b["_printers"].setdefault(gid, {"id": gid, "name": gname, "grams": 0.0, "prints": 0})
        p_rec["prints"] += 1
        p_rec["grams"] += g

        if g > 0:
            mat = (e.get("material") or "").strip() if isinstance(e.get("material"), str) else ""
            if not mat:
                mat = _spool_material(e.get("spool_id"), spool_client, spool_cache, sm_state) or UNKNOWN_MATERIAL
            b["_materials"][mat] = b["_materials"].get(mat, 0.0) + g

    months_out: list[dict] = []
    cur_biggest: tuple[float, str] | None = None
    for k in keys:
        b = buckets[k]
        by_printer = sorted(
            ({"id": p["id"], "name": p["name"], "grams": round(p["grams"], 2), "prints": p["prints"]}
             for p in b["_printers"].values()),
            key=lambda p: (-p["grams"], -p["prints"], p["name"]),
        )
        by_material = sorted(
            ({"material": mat, "grams": round(mg, 2)} for mat, mg in b["_materials"].items()),
            key=lambda x: (-x["grams"], x["material"]),
        )
        row: dict[str, Any] = {
            "month": k,
            "grams": round(b["grams"], 2),
            "prints": b["prints"],
            "unfinished_prints": b["unfinished_prints"],
            "unfinished_grams": round(b["unfinished_grams"], 2),
            "logged_only_grams": round(b["logged_only_grams"], 2),
            "no_weight_prints": b["no_weight_prints"],
            "by_printer": by_printer,
            "by_material": by_material,
        }
        if by_material:
            row["top_material"] = by_material[0]["material"]
        if k == keys[-1]:
            cur_biggest = b["_biggest"]
        months_out.append(row)

    cur = months_out[-1]
    prev = months_out[-2] if len(months_out) >= 2 else None
    insights: list[dict[str, str]] = []

    if cur["grams"] > 0:
        if prev is not None and prev["grams"] > 0:
            text = say("analytics_insight_total_prev", used=_fmt_weight(cur["grams"]), last=_fmt_weight(prev["grams"]))
        else:
            text = say("analytics_insight_total", used=_fmt_weight(cur["grams"]))
        insights.append({"key": "total", "text": text})

    known_mats = [m for m in cur["by_material"] if m["material"] != UNKNOWN_MATERIAL]
    if known_mats:
        fav = known_mats[0]
        insights.append({"key": "favourite_material",
                         "text": say("analytics_insight_favourite", material=fav["material"],
                                     used=_fmt_weight(fav["grams"]))})

    if cur["by_printer"] and (cur["by_printer"][0]["grams"] > 0 or cur["by_printer"][0]["prints"] > 0):
        bp = cur["by_printer"][0]
        insights.append({"key": "busiest_printer",
                         "text": say("analytics_insight_busiest", printer=bp["name"], used=_fmt_weight(bp["grams"]),
                                     count=bp["prints"])})

    if cur["unfinished_prints"] > 0:
        insights.append({"key": "unfinished",
                         "text": say("analytics_insight_unfinished", count=cur["unfinished_prints"],
                                     total=cur["prints"], used=_fmt_weight(cur["unfinished_grams"]))})

    if cur_biggest is not None:
        bg, bname = cur_biggest
        insights.append({"key": "biggest_print",
                         "text": say("analytics_insight_biggest", job=bname, used=_fmt_weight(bg))})

    finished_spools = _spools_finished_in_month(cur["month"])
    if finished_spools > 0:
        insights.append({"key": "spools_finished", "text": say("analytics_insight_finished", count=finished_spools)})

    notes: list[str] = []
    logged_total = sum(m["logged_only_grams"] for m in months_out)
    if logged_total > 0:
        notes.append(say("analytics_note_logged", grams=round(logged_total)))
    no_weight_total = sum(m["no_weight_prints"] for m in months_out)
    if no_weight_total > 0:
        notes.append(say("analytics_note_no_weight", count=no_weight_total))

    return {"months": months_out, "insights": insights, "notes": notes}
