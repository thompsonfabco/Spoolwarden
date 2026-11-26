"""Tests for Feature 4: low-spool reorder list, GET /setup/api/spools/reorder, and recent-addition label printing."""
from __future__ import annotations

import datetime as dt
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from filament_kit import alerts, reorder
from filament_kit.setup_console import spools_tab


ROOT = Path(__file__).resolve().parents[2]


def _spool(sid: int, *, fid: int = 10, vendor: str = "Bambu Lab", name: str = "Basic Black",
           material: str = "PLA", remaining_g: float = 120.0, archived: bool = False,
           used_weight: float = 880.0, comment: str = "", price: float | None = None,
           location: str = "Shelf A") -> dict:
    fil: dict = {
        "id": fid,
        "name": name,
        "material": material,
        "color_hex": "111111",
        "vendor": {"name": vendor},
        "comment": comment,
    }
    if price is not None:
        fil["price"] = price
    return {
        "id": sid,
        "archived": archived,
        "remaining_weight": remaining_g,
        "used_weight": used_weight,
        "location": location,
        "filament": fil,
    }


def test_reorder_combines_running_low_burn_rate_and_orders():
    now = dt.datetime(2026, 11, 25, 12, 0, tzinfo=dt.timezone.utc).timestamp()
    recent_at = dt.datetime(2026, 11, 1, 12, 0, tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")
    old_at = dt.datetime(2026, 5, 1, 12, 0, tzinfo=dt.timezone.utc).isoformat().replace("+00:00", "Z")

    spools = [
        _spool(1, fid=10, vendor="Bambu Lab", name="Basic Black", material="PLA", remaining_g=180.0,
               comment="ASIN: B0TEST0001"),
        _spool(2, fid=20, vendor="Polymaker", name="PolyLite Grey", material="PETG", remaining_g=100.0),
        _spool(3, fid=20, vendor="Polymaker", name="PolyLite Grey", material="PETG", remaining_g=1000.0,
               used_weight=0.0),  # sealed backup for fid 20
        _spool(4, fid=30, vendor="eSUN", name="Fire Red", material="PLA", remaining_g=500.0),  # not low
    ]
    usage = [
        ("p1", "X1C", {"at": recent_at, "spool_id": 1, "grams": 900.0, "material": "PLA"}),  # 10 g/day -> 18 days left
        ("p1", "X1C", {"at": recent_at, "spool_id": 1, "grams": 500.0, "undone": True}),    # ignored
        ("p1", "X1C", {"at": old_at, "spool_id": 1, "grams": 9000.0}),                      # outside 90d
        ("p1", "X1C", {"at": recent_at, "spool_id": 2, "grams": 450.0, "material": "PETG"}),  # 5 g/day -> 20 days left
    ]
    orders = [
        {"order": "111-1", "date": "2026-08-01", "asin": "B0TEST0001", "unit_price": 24.99,
         "seller": "Old Seller", "title": "Bambu Lab Basic Black PLA"},
        {"order": "111-2", "date": "2026-10-15", "asin": "B0TEST0001", "unit_price": 19.99,
         "seller": "Bambu Direct", "title": "Bambu Lab Basic Black PLA"},
        {"order": "111-3", "date": "2026-09-10", "asin": "B0OTHER999", "unit_price": 22.50,
         "seller": "Polymaker Store", "title": "Polymaker PolyLite Grey PETG 1kg"},
    ]
    settings = {"running_low_g": 200}

    res = reorder.build_reorder_list(spools, settings=settings, orders=orders, usage_entries=usage, now=now)

    assert res["threshold_g"] == 200
    assert res["window_days"] == 90
    assert res["orders_available"] is True
    items = res["items"]
    assert [i["id"] for i in items] == [1, 2]

    first = items[0]
    assert first["last_one"] is True
    assert first["burn_rate_g_day"] == 10.0
    assert first["days_left"] == 18.0
    assert first["last_seller"] == "Bambu Direct"
    assert first["last_price"] == 19.99
    assert first["last_order_date"] == "2026-10-15"

    second = items[1]
    assert second["last_one"] is False
    assert second["in_stock"] == 1
    assert second["burn_rate_g_day"] == 5.0
    assert second["days_left"] == 20.0
    assert second["last_seller"] == "Polymaker Store"
    assert second["last_price"] == 22.50


def test_reorder_guards_when_orders_import_missing_or_fails(monkeypatch):
    monkeypatch.delitem(sys.modules, "filament_kit.orders_import", raising=False)
    # Force ImportError even if orders_import exists in workspace
    monkeypatch.setitem(sys.modules, "filament_kit.orders_import", None)
    assert reorder._load_orders() == []

    # Now provide a fake orders_import whose load_orders raises FileNotFoundError
    fake_mod = types.ModuleType("filament_kit.orders_import")

    def _boom():
        raise FileNotFoundError("orders.json")

    fake_mod.load_orders = _boom
    monkeypatch.setitem(sys.modules, "filament_kit.orders_import", fake_mod)
    assert reorder._load_orders() == []

    # Fallback to filament.price when no orders are available
    spools = [_spool(7, fid=70, remaining_g=90.0, price=27.5)]
    res = reorder.build_reorder_list(spools, settings={"running_low_g": 200}, usage_entries=[])
    assert res["orders_available"] is False
    assert len(res["items"]) == 1
    assert res["items"][0]["last_seller"] is None
    assert res["items"][0]["last_price"] == 27.5
    assert res["items"][0]["burn_rate_g_day"] is None
    assert res["items"][0]["days_left"] is None


def test_spools_tab_reorder_route(tmp_path, monkeypatch):
    (tmp_path / "etc").mkdir()
    (tmp_path / "state").mkdir()
    monkeypatch.setenv("FK_ETC", str(tmp_path / "etc"))
    monkeypatch.setenv("FK_STATE", str(tmp_path / "state"))

    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    monkeypatch.setattr(spools_tab.config, "load", lambda: {"spoolman_url": "http://spoolman.test:7912"})
    monkeypatch.setattr(alerts, "load", lambda: {"running_low_g": 200})

    class FakeSm:
        def __init__(self, url):
            self.url = url

        def get(self, path):
            assert path == "spool"
            return [_spool(5, fid=50, remaining_g=120.0)]

    monkeypatch.setattr(spools_tab, "Spoolman", FakeSm)
    monkeypatch.setattr(reorder, "_collect_usage", lambda days: [])
    monkeypatch.setattr(reorder, "_load_orders", lambda: [])

    def _guard(fn, write=False):
        async def _handler(req):
            return JSONResponse(await fn(req, {}))
        return _handler

    page = SimpleNamespace(
        guard=_guard,
        audit=lambda req, msg: None,
    )

    app = Starlette(routes=[Route(p, h, methods=[m]) for p, h, m in spools_tab.routes(page)])
    client = TestClient(app)
    r = client.get("/spools/reorder")
    assert r.status_code == 200
    body = r.json()
    assert body["threshold_g"] == 200
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == 5


def test_strings_catalogue_and_ui_hooks():
    catalogue = json.loads((ROOT / "strings" / "en.json").read_text(encoding="utf-8"))
    expected_keys = [
        "spools_chip_reorder", "spools_reorder_title", "spools_reorder_note",
        "spools_reorder_empty_title", "spools_reorder_empty_text",
        "spools_reorder_col_burn", "spools_reorder_col_days_left",
        "spools_reorder_col_stock", "spools_reorder_col_order",
        "spools_reorder_burn_rate", "spools_reorder_burn_none",
        "spools_reorder_days_left", "spools_reorder_days_unknown",
        "spools_reorder_price", "spools_reorder_order_none",
        "recent_select_all", "recent_select_name", "recent_selection",
        "recent_selected", "recent_selected_spools", "recent_print_labels",
        "recent_print_no_spools",
    ]
    for key in expected_keys:
        assert key in catalogue, f"missing catalogue key: {key}"

    spools_js = (ROOT / "server" / "filament_kit" / "setup_static" / "setup_spools.js").read_text(encoding="utf-8")
    assert "spools/reorder" in spools_js
    assert "paintReorder" in spools_js

    intake_js = (ROOT / "server" / "filament_kit" / "setup_static" / "setup_intake.js").read_text(encoding="utf-8")
    assert "REC_LABEL_SIZES" in intake_js
    assert "recSizePicker" in intake_js
    assert "recSelectedSpoolIds" in intake_js
