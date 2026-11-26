"""Unit tests for Cost & Waste analytics, CSV/JSON order importer, and Hub analytics_tab routes."""
from __future__ import annotations

import datetime as dt
import json

import pytest

from filament_kit import analytics, config, orders_import
from filament_kit.bridge import registry, usage as busage
from filament_kit.spoolman import SpoolmanError


class FakeSpoolman:
    def __init__(self, spools=None):
        self.spools = {s["id"]: dict(s) for s in (spools or [])}

    def get(self, path):
        if path == "spool":
            return list(self.spools.values())
        if path.startswith("spool/"):
            sid = int(path.split("/", 1)[1])
            if sid not in self.spools:
                raise SpoolmanError(f"spool {sid} not found", status=404)
            return self.spools[sid]
        raise SpoolmanError(f"unknown path {path}", status=404)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for name in ("etc", "state"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    cfg = config.load()
    cfg["spoolman_url"] = ""
    config.save(cfg)
    yield


def test_analytics_cost_and_waste_totals(monkeypatch):
    serial = "01P00A000000123"
    registry.upsert(registry.Printer(serial=serial, ip="10.0.0.10", access_code="12345678", name="X1C"))

    now = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc).timestamp()
    prev = dt.datetime(2026, 10, 15, 12, 0, tzinfo=dt.timezone.utc).timestamp()

    monkeypatch.setattr(busage, "_now", lambda: prev)
    # Spool 1: $25 / 1000g = $0.025/g -> 400g = $10.00
    busage.append(serial, {"job": "oct_part", "grams": 400.0, "spool_id": 1, "slot": "A1",
                           "observe": False, "material": "PLA", "outcome": "finished"})

    monkeypatch.setattr(busage, "_now", lambda: now)
    # Spool 1: 200g finished = $5.00
    busage.append(serial, {"job": "nov_ok", "grams": 200.0, "spool_id": 1, "slot": "A1",
                           "observe": False, "material": "PLA", "outcome": "finished"})
    # Spool 2 (filament price fallback): $40 / 1000g = $0.04/g -> 100g failed = $4.00 waste
    busage.append(serial, {"job": "nov_fail", "grams": 100.0, "spool_id": 2, "slot": "A2",
                           "observe": False, "material": "PETG", "outcome": "failed"})
    # Spool 3 (no price): $0.00
    busage.append(serial, {"job": "nov_free", "grams": 50.0, "spool_id": 3, "slot": "A3",
                           "observe": False, "material": "ABS", "outcome": "cancelled"})

    sm = FakeSpoolman([
        {"id": 1, "price": 25.0, "initial_weight": 1000.0, "filament": {"material": "PLA"}},
        {"id": 2, "filament": {"material": "PETG", "price": 40.0, "weight": 1000.0}},
        {"id": 3, "filament": {"material": "ABS"}},
    ])
    res = analytics.view(months=12, sm=sm, now=now)

    oct_row = res["months"][-2]
    nov_row = res["months"][-1]
    assert oct_row["cost"] == 10.0
    assert oct_row["unfinished_cost"] == 0.0
    assert nov_row["cost"] == 9.0
    assert nov_row["unfinished_cost"] == 4.0

    totals = res["totals"]
    assert totals["grams"] == 750.0
    assert totals["prints"] == 4
    assert totals["unfinished_prints"] == 2
    assert totals["unfinished_grams"] == 150.0
    assert totals["cost"] == 19.0
    assert totals["unfinished_cost"] == 4.0


def test_orders_import_csv_dedup_and_load():
    assert orders_import.load_orders() == []

    csv_text = (
        "order_date,vendor,material,color,weight_g,price,currency,qty,spool_id,notes\n"
        "2026-11-01,Bambu Lab,PLA,Matte Black,1000,$19.99,,2,10,AMS refill\n"
        "2026-11-05,Polymaker,PETG,Clear,1kg,24.50,EUR,1,,\n"
    )
    res = orders_import.import_orders(csv_text)
    assert res["imported"] == 2
    assert res["skipped"] == 0
    assert res["total"] == 2

    loaded = orders_import.load_orders()
    assert len(loaded) == 2
    assert loaded[0]["seller"] == "Bambu Lab"
    assert loaded[0]["price"] == 19.99
    assert loaded[0]["currency"] == "USD"
    assert loaded[0]["qty"] == 2
    assert loaded[0]["spool_id"] == 10
    assert loaded[1]["weight_g"] == 1000.0
    assert loaded[1]["currency"] == "EUR"

    # Re-importing same CSV skips duplicates
    res2 = orders_import.import_orders(csv_text, fmt="csv")
    assert res2["imported"] == 0
    assert res2["skipped"] == 2
    assert res2["total"] == 2

    summary = orders_import.summarize_orders()
    assert summary["count"] == 2
    assert summary["total_spools"] == 3
    assert summary["by_currency"] == {"USD": 39.98, "EUR": 24.5}


def test_orders_import_json_and_errors():
    payload = json.dumps({
        "orders": [
            {"date": "2026-11-10", "seller": "Prusa", "material": "ASA", "weight_kg": 0.85, "price": 29.9, "qty": 1},
        ]
    })
    res = orders_import.import_orders(payload, fmt="json", replace=True)
    assert res["imported"] == 1
    assert res["orders"][0]["weight_g"] == 850.0
    assert res["orders"][0]["currency"] == "USD"

    for bad in ("", "   ", "{bad json", "seller,price\nAcme,notanumber\n"):
        with pytest.raises(orders_import.OrderImportError):
            orders_import.parse_orders(bad)


def test_analytics_tab_routes_and_import(env):
    from filament_kit import modules, setup_code
    from test_setup_page import CODE, Helper, client, make, post

    cfg = config.load()
    cfg["modules"] = {n: n in modules.MODES["server"] for n in modules.MODULES}
    cfg["web"] = {**cfg["web"], "lan": ["192.0.2.0/24"]}
    config.save(cfg)
    setup_code.write(CODE)

    app = make(env, Helper())
    c = client(app)

    r_get = c.get("/setup/api/analytics")
    assert r_get.status_code == 200
    body = r_get.json()
    assert "totals" in body
    assert body["orders"]["count"] == 0

    # POST without code is refused
    r_no_code = post(c, "orders/import", {"data": "seller,material,price\nA,PLA,20\n"}, code=None)
    assert r_no_code.status_code == 403

    # POST with code imports orders
    r_imp = post(c, "orders/import", {
        "data": "date,seller,material,weight_g,price,qty\n2026-11-12,Overture,PLA,1000,18.50,2\n",
        "format": "csv",
    })
    assert r_imp.status_code == 200
    imp_data = r_imp.json()
    assert imp_data["imported"] == 1
    assert imp_data["summary"]["count"] == 1
    assert imp_data["summary"]["total_spools"] == 2

    # GET /setup/api/orders and /setup/api/analytics reflect saved orders
    r_orders = c.get("/setup/api/orders")
    assert r_orders.status_code == 200
    assert r_orders.json()["summary"]["count"] == 1
    assert c.get("/setup/api/analytics").json()["orders"]["count"] == 1

    # Invalid input returns 400
    r_bad = post(c, "orders/import", {"data": "", "format": "auto"})
    assert r_bad.status_code == 400
