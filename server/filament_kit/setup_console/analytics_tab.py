"""The Analytics section: filament used over the last months, from the printers' filament-used logs.

    GET  analytics?months=12&printer=<id>    as the kit route GET /api/analytics: {months: [{month, grams, prints,
                                         unfinished_prints, unfinished_grams, logged_only_grams, no_weight_prints,
                                         by_printer: [{id, name, grams, prints}], by_material: [{material, grams}],
                                         top_material?}], insights: [{key, text}], notes: [text]}. `months` is 1 to 13
                                         (12 when left out), `printer` is one printer's id (all printers when left out).
                                         Looking needs no code: the answer holds no address, serial or secret
"""
from __future__ import annotations

import anyio

try:
    from .. import analytics as _analytics          # 's module; not in every build yet (see read())
except ImportError:
    _analytics = None

from .. import orders_import

EMPTY = {"months": [], "insights": [], "notes": []}


def read(months: int, printer: str | None) -> dict:
    """The kit's analytics for `months` months (one printer when `printer`), in the shape the Hub page reads. Until the kit has
    its analytics module the answer is the shape with nothing in it, which the page shows as "No usage yet"."""
    view = getattr(_analytics, "view", None)
    out = view(months=months, printer=printer) if callable(view) else dict(EMPTY)
    if "orders" not in out:
        out["orders"] = orders_import.summarize_orders()
    return out


def routes(page) -> list[tuple]:
    from ..setup_page import Refused

    async def get(request, body):
        q = request.query_params
        try:
            months = int(q.get("months") or 12)
        except ValueError:
            months = 0
        if not 1 <= months <= 13:
            raise Refused(400, "months must be 1 to 13")
        printer = (q.get("printer") or "").strip() or None
        return await anyio.to_thread.run_sync(read, months, printer)

    async def get_orders(request, body):
        def _load():
            items = orders_import.load_orders()
            return {"orders": items, "summary": orders_import.summarize_orders(items)}
        return await anyio.to_thread.run_sync(_load)

    async def import_orders_route(request, body):
        if not isinstance(body, dict) or not isinstance(body.get("data"), str):
            raise Refused(400, "Order data is required", field="order_data")
        fmt = body.get("format") or "auto"
        if not isinstance(fmt, str):
            raise Refused(400, "Invalid format", field="order_format")
        replace = bool(body.get("replace", False))
        try:
            out = await anyio.to_thread.run_sync(
                lambda: orders_import.import_orders(body["data"], fmt=fmt, replace=replace)
            )
        except orders_import.OrderImportError as e:
            raise Refused(e.status, str(e), reason=e.code, field="order_data") from None
        page.audit(request, f"imported {out['imported']} filament orders")
        return {
            "imported": out["imported"],
            "skipped": out["skipped"],
            "total": out["total"],
            "summary": orders_import.summarize_orders(out["orders"]),
        }

    return [
        ("/analytics", page.guard(get), "GET"),
        ("/orders", page.guard(get_orders), "GET"),
        ("/orders/import", page.guard(import_orders_route, write=True), "POST"),
    ]
