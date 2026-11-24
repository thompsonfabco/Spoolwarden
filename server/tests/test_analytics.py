"""Tests for analytics, enriched usage entries, by=month, usage.undone, and Klipper 13-month retention."""
from __future__ import annotations

import datetime as dt
import io
import json
import zipfile

import pytest

from filament_kit import analytics, event_sink, events
from filament_kit.bridge import ams, registry, usage as busage
from filament_kit.bridge.klipper import usage as kusage
from filament_kit.spoolman import SpoolmanError


class FakeSpoolman:
    def __init__(self, spools=None):
        self.spools = {s["id"]: dict(s) for s in (spools or [])}
        self.uses = []

    def get(self, path):
        if path == "spool":
            return list(self.spools.values())
        if path.startswith("spool/"):
            sid = int(path.split("/", 1)[1])
            if sid not in self.spools:
                raise SpoolmanError(f"spool {sid} not found", status=404)
            return self.spools[sid]
        raise SpoolmanError(f"unknown path {path}", status=404)

    def use(self, spool_id, grams):
        self.uses.append((spool_id, grams))


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for name in ("etc", "state"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    event_sink.set_sink(None)
    yield
    event_sink.set_sink(None)


def _make_3mf(used_g: float = 42.5, ftype: str = "PLA", color: str = "#FF0000") -> bytes:
    xml = (
        '<config>'
        '<plate>'
        '<metadata key="index" value="1"/>'
        f'<filament id="1" type="{ftype}" color="{color}" used_g="{used_g}"/>'
        '</plate>'
        '</config>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("Metadata/slice_info.config", xml)
    return buf.getvalue()


def test_bambu_enriched_fields_and_usage_undone(monkeypatch):
    serial = "01P00A000000001"
    registry.upsert(registry.Printer(serial=serial, ip="10.0.0.1", access_code="12345678",
                                     name="Workshop X1", model="X1C"))
    smap = ams.SlotMap(calibrated=True, observe=False,
                       slots={"A1": {"ams_id": 0, "tray_id": 0, "global_id": 0}})
    ams.save_map(serial, smap)
    sm = FakeSpoolman([{
        "id": 5,
        "location": "Workshop X1 \u2013 AMS A1",
        "registered": "2025-01-01T00:00:00Z",
        "filament": {"material": "PETG", "name": "Jet Black", "color_hex": "111111",
                     "vendor": {"name": "Prusament"}},
    }])
    monkeypatch.setattr(busage, "fetch_3mf", lambda p, sub, plate: (_make_3mf(55.0, "PETG", "#111111"), "/a.3mf"))

    emitted = []

    class CaptureSink(event_sink.NullSink):
        def write(self, kind, subject, actor=None, data=None, **kw):
            emitted.append((kind, subject, data))

    event_sink.set_sink(CaptureSink())

    out = busage.settle(serial, {"subtask": "bracket_v4", "plate": "1", "mapping": [0], "outcome": "finished"},
                        100.0, sm)
    assert len(out) == 1
    e = out[0]
    assert e["material"] == "PETG"
    assert e["vendor"] == "Prusament"
    assert e["name"] == "Jet Black"
    assert e["color_hex"] == "111111"
    assert e["printer_name"] == "Workshop X1"
    assert e["outcome"] == "finished"

    listed = busage.entries(serial, 7)
    assert len(listed) == 1
    assert listed[0]["outcome"] == "finished"
    assert listed[0]["printer_name"] == "Workshop X1"

    undone = busage.undo(e["id"], sm)
    assert undone["undone"] is True
    undone_evs = [x for x in emitted if x[0] == "usage.undone"]
    assert len(undone_evs) == 1
    _, subj, data = undone_evs[0]
    assert subj["id"] == serial
    assert data == {"printer": serial, "job": "bracket_v4", "spool": 5, "grams": 55.0, "entry": e["id"]}


def test_bambu_by_month_and_unknown_outcome(monkeypatch):
    serial = "01P00A000000002"
    registry.upsert(registry.Printer(serial=serial, ip="10.0.0.2", access_code="12345678", name="Mini"))
    now = dt.datetime(2026, 11, 15, 12, 0, tzinfo=dt.timezone.utc).timestamp()
    monkeypatch.setattr(busage, "_now", lambda: now)
    busage.append(serial, {"job": "j1", "grams": 100.0, "spool_id": 1, "slot": "A1", "observe": False,
                           "material": "PLA", "outcome": "finished"})
    busage.append(serial, {"job": "j2", "grams": 30.0, "spool_id": 1, "slot": "A1", "observe": False,
                           "material": "PLA", "outcome": "failed"})
    busage.append(serial, {"job": "j3", "grams": 50.0, "spool_id": 1, "slot": "A1", "observe": False,
                           "material": "PETG", "outcome": "cancelled"})

    res = busage.entries(serial, 30, by="month")
    assert len(res["months"]) == 1
    m = res["months"][0]
    assert m["month"] == "2026-11"
    assert m["grams"] == 180.0
    assert m["prints"] == 3
    assert m["unfinished_prints"] == 2
    assert m["top_material"] == "PLA"


def test_klipper_enriched_fields_by_month_and_13_month_retention():
    pid = "sv08-max"
    registry.upsert(registry.Printer(serial=pid, host="10.0.0.3", access_code="", name="SV08 Max",
                                     family="klipper"))
    now = dt.datetime(2026, 11, 15, 12, 0, tzinfo=dt.timezone.utc).timestamp()
    old_14m = dt.datetime(2025, 9, 1, 12, 0, tzinfo=dt.timezone.utc).timestamp()
    keep_12m = dt.datetime(2025, 11, 20, 12, 0, tzinfo=dt.timezone.utc).timestamp()

    # Pre-seed an entry older than 13 months and one within 13 months
    kusage._write(pid, [
        {"id": "k-old", "job": "old", "file": "old.gcode", "started": old_14m - 60, "ended": old_14m,
         "status": "completed", "spool_id": 1, "filament_mm": 100.0, "grams": 10.0, "source": "moonraker"},
        {"id": "k-keep", "job": "keep", "file": "keep.gcode", "started": keep_12m - 60, "ended": keep_12m,
         "status": "cancelled", "spool_id": 1, "filament_mm": 50.0, "grams": 5.0, "source": "moonraker"},
    ])

    e = kusage.record(pid, {
        "job_id": "cur1",
        "filename": "part.gcode",
        "status": "completed",
        "start_time": now - 120,
        "end_time": now,
        "filament_used": 1000.0,
        "metadata": {
            "filament_total": 1000.0,
            "filament_weight_total": 25.0,
            "filament_type": "ASA",
            "filament_vendor": "Polymaker",
            "filament_name": "Black ASA",
            "filament_color": "#222222",
        },
    }, 3)

    assert e["material"] == "ASA"
    assert e["vendor"] == "Polymaker"
    assert e["name"] == "Black ASA"
    assert e["color_hex"] == "222222"
    assert e["printer_name"] == "SV08 Max"
    assert e["outcome"] == "finished"

    got = kusage.entries(pid, 500)
    ids = [x["id"] for x in got]
    assert "k-old" not in ids
    assert "k-keep" in ids
    assert "k-cur1" in ids
    kept = next(x for x in got if x["id"] == "k-keep")
    assert kept["outcome"] == "cancelled"

    monthly = kusage.entries(pid, 500, by="month")
    assert [m["month"] for m in monthly["months"]] == ["2026-11", "2025-11"]


def test_klipper_retains_over_2000_entries_within_13_months(monkeypatch):
    pid = "voron"
    now = dt.datetime(2026, 11, 15, 12, 0, tzinfo=dt.timezone.utc).timestamp()
    monkeypatch.setattr(kusage.time, "time", lambda: now)

    seed = [
        {"id": f"k-{i}", "job": str(i), "file": "a.gcode", "started": now - 1000, "ended": now - 500 + i * 0.01,
         "status": "completed", "spool_id": 1, "filament_mm": 10.0, "grams": 1.0, "source": "moonraker"}
        for i in range(2050)
    ]
    kusage._write(pid, seed)
    kusage.record(pid, {"job_id": "2050", "filename": "a.gcode", "status": "completed",
                        "start_time": now - 10, "end_time": now, "filament_used": 10.0,
                        "metadata": {"filament_weight_total": 1.0}}, 1)
    assert len(kusage.entries(pid, 400)) == 2051


def test_analytics_view_empty_and_validation():
    with pytest.raises(ValueError):
        analytics.view(months=0)
    with pytest.raises(ValueError):
        analytics.view(months=14)
    res = analytics.view(months=12)
    assert len(res["months"]) == 12
    assert all(m["grams"] == 0 and m["prints"] == 0 for m in res["months"])
    assert res["insights"] == []
    assert res["notes"] == []


def test_analytics_view_full_aggregation_insights_and_notes(monkeypatch):
    bambu_id = "01P00A000000010"
    klipper_id = "sv08"
    removed_id = "01P00A000000099"
    registry.upsert(registry.Printer(serial=bambu_id, ip="10.0.0.10", access_code="12345678", name="Workshop X1"))
    registry.upsert(registry.Printer(serial=klipper_id, host="10.0.0.11", access_code="", name="SV08", family="klipper"))

    now = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.timezone.utc).timestamp()
    prev_month = dt.datetime(2026, 10, 10, 12, 0, tzinfo=dt.timezone.utc).timestamp()
    monkeypatch.setattr(busage, "_now", lambda: now)

    # Current month Bambu entries
    e_ok = busage.append(bambu_id, {"job": "bracket_v4.3mf", "grams": 212.0, "spool_id": 1, "slot": "A1",
                                    "observe": False, "material": "PA6", "printer_name": "Workshop X1",
                                    "outcome": "finished"})
    # Undone entry: must be excluded
    e_undone = busage.append(bambu_id, {"job": "oops", "grams": 500.0, "spool_id": 1, "slot": "A1",
                                        "observe": False, "material": "PA6", "outcome": "finished"})
    busage._write_line(bambu_id, {"undo": e_undone["id"], "at": "2026-11-20T12:01:00Z"})

    # Observe / logged-only + unfinished
    busage.append(bambu_id, {"job": "failed_part", "grams": 38.0, "spool_id": None, "slot": "A2",
                             "observe": True, "material": "PETG", "printer_name": "Workshop X1",
                             "outcome": "failed"})
    # No-weight print
    busage.append(bambu_id, {"job": "missing_file", "grams": None, "spool_id": None, "slot": None,
                             "observe": False, "printer_name": "Workshop X1", "outcome": "unknown",
                             "error": "file_not_found"})

    # Previous month entry without material (resolved via Spoolman)
    monkeypatch.setattr(busage, "_now", lambda: prev_month)
    busage.append(bambu_id, {"job": "old_job", "grams": 1520.0, "spool_id": 9, "slot": "A1",
                             "observe": False, "outcome": "finished"})

    # Removed printer entry
    busage.append(removed_id, {"job": "legacy", "grams": 100.0, "spool_id": 99, "slot": "A1",
                               "observe": False, "outcome": "finished"})

    # Klipper entry in current month
    kusage._write(klipper_id, [{
        "id": "k-1", "job": "klipper_part.gcode", "file": "klipper_part.gcode", "started": now - 300, "ended": now,
        "status": "completed", "spool_id": 2, "filament_mm": 2000.0, "grams": 1000.0, "source": "moonraker",
        "material": "PA6", "printer_name": "SV08", "outcome": "finished",
    }])

    # Spool emptied event in current month
    store = events.store()
    store.write("spool.emptied", {"type": "spool", "id": "1", "name": "#1"}, {"type": "hub"}, {"spool": 1})

    sm = FakeSpoolman([{"id": 9, "filament": {"material": "ABS"}}])
    res = analytics.view(months=12, sm=sm, now=now)

    assert len(res["months"]) == 12
    oct_row = res["months"][-2]
    nov_row = res["months"][-1]

    assert oct_row["month"] == "2026-10"
    assert oct_row["grams"] == 1620.0
    assert oct_row["top_material"] == "ABS"
    assert any(p["id"] == analytics.REMOVED_PRINTER_ID and p["name"] == analytics.REMOVED_PRINTER_NAME
               for p in oct_row["by_printer"])

    assert nov_row["month"] == "2026-11"
    assert nov_row["grams"] == 1250.0
    assert nov_row["prints"] == 4
    assert nov_row["unfinished_prints"] == 2
    assert nov_row["unfinished_grams"] == 38.0
    assert nov_row["logged_only_grams"] == 38.0
    assert nov_row["no_weight_prints"] == 1
    assert nov_row["top_material"] == "PA6"

    insight_map = {i["key"]: i["text"] for i in res["insights"]}
    assert "1.25 kg (last month 1.62 kg)" in insight_map["total"]
    assert "PA6 (1.21 kg)" in insight_map["favourite_material"]
    assert "SV08 did the most work this month" in insight_map["busiest_printer"]
    assert "2 of 4 prints did not finish this month (38 g)." == insight_map["unfinished"]
    assert "klipper_part.gcode (1.00 kg)" in insight_map["biggest_print"]
    assert "You finished 1 spool this month." == insight_map["spools_finished"]

    assert "Includes 38 g the Hub logged but took off no spool." in res["notes"]
    assert "1 print has no weight." in res["notes"]

    # Filter by removed printer
    removed_only = analytics.view(months=12, printer=analytics.REMOVED_PRINTER_ID, sm=sm, now=now)
    assert removed_only["months"][-2]["grams"] == 100.0
    assert removed_only["months"][-1]["grams"] == 0.0
