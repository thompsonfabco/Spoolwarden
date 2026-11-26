"""Filament order history importer (CSV and JSON) stored in `<state>/orders.json`.

Provides `load_orders()` for analytics and reorder suggestions, plus `parse_orders()`,
`import_orders()`, and `summarize_orders()` for the Hub API.
"""
from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
import json
import re
from pathlib import Path
from typing import Any

from . import config, pairing

FILE = "orders.json"
LOCK = "orders.lock"

_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY"}
_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "order_date", "purchased_at", "purchase_date"),
    "seller": ("seller", "vendor", "store", "shop", "supplier", "brand", "merchant"),
    "material": ("material", "filament", "filament_type", "type"),
    "color": ("color", "colour", "color_name", "shade"),
    "weight_g": ("weight_g", "weight", "grams", "net_g", "net_weight", "weight_grams"),
    "weight_kg": ("weight_kg", "kg", "net_kg"),
    "price": ("price", "unit_price", "cost", "amount", "total"),
    "currency": ("currency", "curr"),
    "qty": ("qty", "quantity", "count", "spools"),
    "spool_id": ("spool_id", "spool"),
    "notes": ("notes", "note", "comment", "memo"),
}
_KG_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*kg\s*$", re.I)
_G_RE = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*g(?:rams?)?\s*$", re.I)


class OrderImportError(ValueError):
    """Raised when order import input is empty, malformed, or contains invalid rows."""

    def __init__(self, message: str, *, code: str = "invalid_orders", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def orders_path() -> Path:
    return config.paths().state / FILE


def _lock_path() -> Path:
    return config.paths().state / LOCK


def _norm_key(key: Any) -> str:
    return re.sub(r"[\s\-]+", "_", str(key or "").strip().lower())


def _pick(row: dict[str, Any], field: str) -> Any:
    norm = {_norm_key(k): v for k, v in row.items() if k is not None}
    for alias in _ALIASES[field]:
        if alias in norm:
            val = norm[alias]
            if val is not None and str(val).strip() != "":
                return val
    return None


def _parse_date(raw: Any) -> str:
    if raw is None:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m/%d/%Y", "%d/%m/%Y"):
        try:
            return dt.datetime.strptime(s[:10], fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return s


def _parse_weight_g(row: dict[str, Any], idx: int) -> float | None:
    raw_kg = _pick(row, "weight_kg")
    if raw_kg is not None:
        try:
            val = float(str(raw_kg).strip())
        except ValueError as e:
            raise OrderImportError(f"Row {idx}: invalid weight_kg {raw_kg!r}", code="invalid_row") from e
        if val <= 0:
            raise OrderImportError(f"Row {idx}: weight_kg must be positive", code="invalid_row")
        return round(val * 1000.0, 2)

    raw_g = _pick(row, "weight_g")
    if raw_g is None:
        return None
    if isinstance(raw_g, (int, float)) and not isinstance(raw_g, bool):
        val = float(raw_g)
    else:
        s = str(raw_g).strip()
        m_kg = _KG_RE.match(s)
        m_g = _G_RE.match(s)
        try:
            if m_kg:
                val = float(m_kg.group(1)) * 1000.0
            elif m_g:
                val = float(m_g.group(1))
            else:
                val = float(s)
        except ValueError as e:
            raise OrderImportError(f"Row {idx}: invalid weight_g {raw_g!r}", code="invalid_row") from e
    if val <= 0:
        raise OrderImportError(f"Row {idx}: weight_g must be positive", code="invalid_row")
    return round(val, 2)


def _parse_price_and_currency(row: dict[str, Any], idx: int) -> tuple[float | None, str]:
    raw_curr = _pick(row, "currency")
    currency = str(raw_curr).strip().upper() if raw_curr is not None else ""
    raw_price = _pick(row, "price")
    if raw_price is None:
        return None, currency
    if isinstance(raw_price, (int, float)) and not isinstance(raw_price, bool):
        price = float(raw_price)
    else:
        s = str(raw_price).strip()
        for sym, code in _CURRENCY_SYMBOLS.items():
            if s.startswith(sym):
                if not currency:
                    currency = code
                s = s[len(sym):].strip()
                break
        s = s.replace(",", "")
        try:
            price = float(s)
        except ValueError as e:
            raise OrderImportError(f"Row {idx}: invalid price {raw_price!r}", code="invalid_row") from e
    if price < 0:
        raise OrderImportError(f"Row {idx}: price cannot be negative", code="invalid_row")
    if not currency:
        currency = "USD"
    return round(price, 2), currency


def _parse_qty(raw: Any, idx: int) -> int:
    if raw is None:
        return 1
    if isinstance(raw, int) and not isinstance(raw, bool):
        qty = raw
    else:
        try:
            qty = int(float(str(raw).strip()))
        except ValueError as e:
            raise OrderImportError(f"Row {idx}: invalid qty {raw!r}", code="invalid_row") from e
    if qty < 1:
        raise OrderImportError(f"Row {idx}: qty must be at least 1", code="invalid_row")
    return qty


def _parse_spool_id(raw: Any, idx: int) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, int) and not isinstance(raw, bool):
        return raw
    try:
        return int(str(raw).strip())
    except ValueError as e:
        raise OrderImportError(f"Row {idx}: invalid spool_id {raw!r}", code="invalid_row") from e


def _order_id(rec: dict[str, Any]) -> str:
    key_fields = (
        rec["date"], rec["seller"], rec["material"], rec["color"],
        rec["weight_g"], rec["price"], rec["currency"], rec["qty"],
        rec["spool_id"], rec["notes"],
    )
    payload = json.dumps(key_fields, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]


def _normalize_row(row: dict[str, Any], idx: int, imported_at: str) -> dict[str, Any]:
    seller = str(_pick(row, "seller") or "").strip()
    material = str(_pick(row, "material") or "").strip()
    color = str(_pick(row, "color") or "").strip()
    notes = str(_pick(row, "notes") or "").strip()
    date = _parse_date(_pick(row, "date"))
    weight_g = _parse_weight_g(row, idx)
    price, currency = _parse_price_and_currency(row, idx)
    qty = _parse_qty(_pick(row, "qty"), idx)
    spool_id = _parse_spool_id(_pick(row, "spool_id"), idx)

    if not (seller or material or price is not None or weight_g is not None or spool_id is not None):
        raise OrderImportError(
            f"Row {idx}: order must include at least seller, material, weight_g, price, or spool_id",
            code="invalid_row",
        )

    rec: dict[str, Any] = {
        "date": date,
        "seller": seller,
        "material": material,
        "color": color,
        "weight_g": weight_g,
        "price": price,
        "currency": currency,
        "qty": qty,
        "spool_id": spool_id,
        "notes": notes,
    }
    rec["id"] = str(row.get("id") or "").strip() or _order_id(rec)
    rec["imported_at"] = imported_at
    return rec


def parse_orders(raw: str | bytes, fmt: str = "auto") -> list[dict[str, Any]]:
    """Parse CSV or JSON order data into normalized order dicts."""
    if isinstance(raw, bytes):
        text = raw.decode("utf-8-sig", errors="replace")
    elif isinstance(raw, str):
        text = raw.lstrip("\ufeff")
    else:
        raise OrderImportError("Order data must be text", code="invalid_format")

    stripped = text.strip()
    if not stripped:
        raise OrderImportError("No order data provided", code="empty_input")

    fmt_norm = (fmt or "auto").strip().lower()
    if fmt_norm not in ("auto", "csv", "json"):
        raise OrderImportError(f"Unsupported order format: {fmt!r}", code="invalid_format")

    if fmt_norm == "auto":
        fmt_norm = "json" if stripped[0] in "[{\"" else "csv"

    imported_at = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw_rows: list[dict[str, Any]] = []

    if fmt_norm == "json":
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError as e:
            raise OrderImportError(f"Invalid JSON: {e.msg}", code="invalid_format") from e
        if isinstance(parsed, dict):
            for key in ("orders", "items", "records"):
                if isinstance(parsed.get(key), list):
                    parsed = parsed[key]
                    break
            else:
                parsed = [parsed]
        if not isinstance(parsed, list):
            raise OrderImportError("JSON orders must be a list of objects", code="invalid_format")
        for idx, item in enumerate(parsed, start=1):
            if not isinstance(item, dict):
                raise OrderImportError(f"Row {idx}: expected a JSON object", code="invalid_row")
            raw_rows.append(item)
    else:
        reader = csv.DictReader(io.StringIO(stripped))
        if not reader.fieldnames or not any(f and f.strip() for f in reader.fieldnames):
            raise OrderImportError("CSV header row is missing", code="invalid_format")
        for row in reader:
            if not any(v is not None and str(v).strip() for v in row.values()):
                continue
            raw_rows.append(row)

    if not raw_rows:
        raise OrderImportError("No order rows found", code="empty_input")

    return [_normalize_row(row, idx, imported_at) for idx, row in enumerate(raw_rows, start=1)]


def load_orders() -> list[dict[str, Any]]:
    """Load saved orders from `<state>/orders.json`. Returns `[]` if none are saved yet."""
    path = orders_path()
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if isinstance(data, dict) and isinstance(data.get("orders"), list):
        data = data["orders"]
    if not isinstance(data, list):
        return []
    return [dict(item) for item in data if isinstance(item, dict)]


def _save_orders_locked(orders: list[dict[str, Any]]) -> None:
    path = orders_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = (json.dumps(orders, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
    pairing._write_atomic(path, payload, 0o600, state_owned=True)


def import_orders(raw: str | bytes, fmt: str = "auto", *, replace: bool = False) -> dict[str, Any]:
    """Parse and persist orders to `<state>/orders.json`, deduplicating by order `id`."""
    parsed = parse_orders(raw, fmt=fmt)
    orders_path().parent.mkdir(parents=True, exist_ok=True)
    with pairing.open_lock(_lock_path()):
        existing = [] if replace else load_orders()
        seen = {str(o.get("id")) for o in existing if o.get("id")}
        added = 0
        skipped = 0
        merged = list(existing)
        for rec in parsed:
            if rec["id"] in seen:
                skipped += 1
                continue
            seen.add(rec["id"])
            merged.append(rec)
            added += 1
        _save_orders_locked(merged)
    return {
        "imported": added,
        "skipped": skipped,
        "total": len(merged),
        "orders": merged,
    }


def summarize_orders(orders: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Return compact order summary metrics for analytics and Hub UI."""
    items = load_orders() if orders is None else orders
    total_spools = sum(int(o.get("qty") or 1) for o in items)
    by_currency: dict[str, float] = {}
    for o in items:
        price = o.get("price")
        if isinstance(price, (int, float)) and not isinstance(price, bool) and price > 0:
            curr = str(o.get("currency") or "USD").strip() or "USD"
            qty = int(o.get("qty") or 1)
            by_currency[curr] = round(by_currency.get(curr, 0.0) + float(price) * qty, 2)
    recent = sorted(items, key=lambda o: (str(o.get("date") or ""), str(o.get("imported_at") or "")), reverse=True)[:5]
    return {
        "count": len(items),
        "total_spools": total_spools,
        "by_currency": by_currency,
        "recent": recent,
    }
