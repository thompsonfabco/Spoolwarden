"""Low-spool reorder list: combines running-low spools, 3-month burn rate, and order history."""
from __future__ import annotations

import datetime as dt
import time
from typing import Any

from . import alerts, analytics
from .spoolman import Spoolman

WINDOW_DAYS = 90


def _load_orders() -> list[dict]:
    """Load order history from `orders_import.load_orders()`, guarding cleanly if absent or unreadable."""
    try:
        from . import orders_import
    except ImportError:
        return []
    loader = getattr(orders_import, "load_orders", None)
    if not callable(loader):
        return []
    try:
        items = loader()
    except Exception:  # noqa: BLE001 - missing or unreadable orders.json must not break reorder
        return []
    return [o for o in items if isinstance(o, dict)] if isinstance(items, list) else []


def _collect_usage(days: float) -> list[tuple[str, str, dict]]:
    """Collect usage entries over the last `days` days, returning `[]` if logs are unavailable."""
    try:
        return analytics._collect_entries(days, None)
    except Exception:  # noqa: BLE001 - missing state dir or logs must not break reorder
        return []


def _entry_ts(entry: dict) -> float | None:
    """Return the UTC timestamp of a usage entry, or `None` if unparseable."""
    ended = entry.get("ended")
    if isinstance(ended, (int, float)) and not isinstance(ended, bool):
        return float(ended)
    at = entry.get("at")
    if isinstance(at, str) and at.strip():
        raw = at.strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = dt.datetime.fromisoformat(raw)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.timestamp()
        except ValueError:
            return None
    return None


def _burn_totals(
    entries: list[tuple[str, str, dict]],
    spool_by_id: dict[Any, dict],
    now: float,
    window_days: int,
) -> tuple[dict[Any, float], dict[str, float]]:
    """Aggregate 3-month grams used per filament id and per lowercased material."""
    cutoff = now - window_days * 86400.0
    by_filament: dict[Any, float] = {}
    by_material: dict[str, float] = {}

    for _gid, _gname, entry in entries:
        if not isinstance(entry, dict) or entry.get("undone"):
            continue
        raw_g = entry.get("grams")
        if isinstance(raw_g, bool) or not isinstance(raw_g, (int, float)) or raw_g <= 0:
            continue
        ts = _entry_ts(entry)
        if ts is not None and ts < cutoff:
            continue

        grams = float(raw_g)
        sid = entry.get("spool_id")
        sp = spool_by_id.get(sid) if sid is not None else None
        fil = (sp or {}).get("filament") or {}
        fid = fil.get("id")
        if fid is not None:
            by_filament[fid] = by_filament.get(fid, 0.0) + grams

        mat = (entry.get("material") or fil.get("material") or "").strip().lower()
        if mat:
            by_material[mat] = by_material.get(mat, 0.0) + grams

    return by_filament, by_material


def _match_order(spool: dict | None, row: dict, orders: list[dict]) -> dict | None:
    """Return the newest order matching the spool's filament (by ASIN first, then brand + name)."""
    if not orders:
        return None
    fil = (spool or {}).get("filament") or {}
    comment = str(fil.get("comment") or "")
    extra_asin = str(((fil.get("extra") or {}).get("asin")) or "").strip()
    vendor = str(((fil.get("vendor") or {}).get("name")) or "").strip().lower()
    name = str(fil.get("name") or "").strip().lower()
    display = str(row.get("filament") or "").strip().lower()

    matched: list[dict] = []
    for order in orders:
        asin = str(order.get("asin") or "").strip()
        if asin and ((extra_asin and asin == extra_asin) or (comment and asin in comment)):
            matched.append(order)
            continue
        title = str(order.get("title") or "").lower()
        if not title:
            continue
        if vendor and name and vendor in title and name in title:
            matched.append(order)
        elif display and display in title:
            matched.append(order)

    if not matched:
        return None
    matched.sort(key=lambda o: str(o.get("date") or ""), reverse=True)
    return matched[0]


def _order_price(order: dict | None, spool: dict | None) -> float | None:
    if order is not None:
        raw = order.get("unit_price")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            return round(float(raw), 2)
    fil = (spool or {}).get("filament") or {}
    raw_fil = fil.get("price")
    if isinstance(raw_fil, (int, float)) and not isinstance(raw_fil, bool):
        return round(float(raw_fil), 2)
    return None


def build_reorder_list(
    spools: list[dict],
    settings: dict | None = None,
    *,
    orders: list[dict] | None = None,
    usage_entries: list[tuple[str, str, dict]] | None = None,
    now: float | None = None,
    window_days: int = WINDOW_DAYS,
) -> dict:
    """Combine `running_low` spools, 3-month burn rate (`days_left`), and last order seller/price."""
    ts = time.time() if now is None else float(now)
    low = alerts.running_low(spools, settings)
    spool_by_id = {sp.get("id"): sp for sp in spools if isinstance(sp, dict) and sp.get("id") is not None}
    entries = _collect_usage(float(window_days) + 1.0) if usage_entries is None else usage_entries
    by_filament, by_material = _burn_totals(entries, spool_by_id, ts, window_days)
    orders_list = _load_orders() if orders is None else orders

    items: list[dict] = []
    for row in low["spools"]:
        sp = spool_by_id.get(row["id"])
        fil = (sp or {}).get("filament") or {}
        fid = fil.get("id")
        mat = str(row.get("material") or "").strip().lower()

        used_g = by_filament.get(fid, 0.0) if fid is not None else 0.0
        if used_g <= 0.0 and mat:
            used_g = by_material.get(mat, 0.0)

        burn_rate = round(used_g / window_days, 2) if used_g > 0.0 else None
        days_left = round(row["remaining_g"] / (used_g / window_days), 1) if used_g > 0.0 else None

        order = _match_order(sp, row, orders_list)
        seller = (str(order.get("seller")).strip() or None) if order and order.get("seller") else None
        price = _order_price(order, sp)

        items.append({
            **row,
            "filament_id": fid,
            "burn_rate_g_day": burn_rate,
            "days_left": days_left,
            "last_seller": seller,
            "last_price": price,
            "last_order_date": (str(order.get("date")).strip() or None) if order and order.get("date") else None,
        })

    items.sort(key=lambda r: (
        0 if r["last_one"] else 1,
        r["days_left"] if r["days_left"] is not None else float("inf"),
        r["remaining_g"],
        r["id"] if isinstance(r["id"], int) else 0,
    ))

    return {
        "threshold_g": low["threshold_g"],
        "window_days": window_days,
        "orders_available": bool(orders_list),
        "items": items,
    }


def for_spoolman(sm: Spoolman, settings: dict | None = None) -> dict:
    """Fetch spools from `sm` and build the reorder list."""
    return build_reorder_list(sm.get("spool"), settings=settings)
