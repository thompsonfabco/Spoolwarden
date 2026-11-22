"""Tests for filament_kit.csv_io (HD5 CSV export & import)."""
from __future__ import annotations

import csv
import io
import urllib.error
from types import SimpleNamespace

import pytest
from fakes import spoolman_fake

from filament_kit import config, csv_io, events, intake_history, storage
from filament_kit.bridge import registry, usage as bambu_usage
from filament_kit.profiles import lines as lines_mod
from filament_kit.spoolman import Spoolman


@pytest.fixture()
def isolated_state(tmp_path, monkeypatch):
    monkeypatch.setenv("FK_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("FK_ETC", str(tmp_path / "etc"))
    monkeypatch.setattr(
        config,
        "load",
        lambda: {
            "spoolman_url": "http://127.0.0.1:7912",
            "sealed_location": "Sealed",
            "overflow_location": "Overflow",
            "locations": [],
        },
    )
    return tmp_path


def _parse_csv(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


def test_formula_injection_guard():
    assert csv_io.safe_cell("=CMD()") == "'=CMD()"
    assert csv_io.safe_cell("+1+2") == "'+1+2"
    assert csv_io.safe_cell("-1+2") == "'-1+2"
    assert csv_io.safe_cell("@SUM(A1)") == "'@SUM(A1)"
    assert csv_io.safe_cell("normal") == "normal"
    assert csv_io.safe_cell(None) == ""
    assert csv_io.safe_cell(42) == "42"


def test_export_usage_and_formula_guard(isolated_state):
    p = registry.Printer(serial="01P00A000000001", name="=Workshop X1", model="X1C", configured=True)
    registry.upsert(p)
    bambu_usage.append(
        "01P00A000000001",
        {
            "job": "+bracket.3mf",
            "grams": 42.5,
            "spool_id": 7,
            "slot": "A1",
            "observe": False,
        },
    )
    out = csv_io.export_csv("usage", days=30)
    rows = _parse_csv(out)
    assert rows[0] == [
        "time", "printer", "job", "slot", "spool", "vendor", "name",
        "material", "grams", "outcome", "taken_off", "undone",
    ]
    assert len(rows) == 2
    assert rows[1][1] == "'=Workshop X1"
    assert rows[1][2] == "'+bracket.3mf"
    assert rows[1][4] == "7"
    assert rows[1][8] == "42.5"
    assert rows[1][10] == "yes"
    assert rows[1][11] == "no"


def test_export_locations(isolated_state):
    st = storage.Storage(locations=[
        storage.Location(name="Sealed", type="bulk", fill="none", sealed=True, id="loc-1"),
        storage.Location(name="Overflow", type="bulk", fill="none", overflow=True, default_overflow=True, id="loc-2"),
        storage.Location(name="@Shelf A", type="bulk", limit=10, fill="materials", materials=["PLA", "PETG"], id="loc-3"),
    ])
    storage.save(st)
    rows = _parse_csv(csv_io.export_csv("locations"))
    assert rows[0] == [
        "name", "type", "limit", "count", "prefix", "fill", "materials",
        "overflow", "default_overflow", "sealed", "position",
    ]
    assert len(rows) == 4
    assert rows[3][0] == "'@Shelf A"
    assert rows[3][6] == "PLA;PETG"
    assert rows[3][10] == "3"


def test_export_recent(isolated_state):
    intake_history.record_save(
        [{"values": {"vendor": "Polymaker", "name": "-Charcoal PLA", "material": "PLA", "net": 1000, "sealed": True},
          "spool_ids": [11, 12], "filament_id": 5}],
        source="manual",
    )
    rows = _parse_csv(csv_io.export_csv("recent"))
    assert rows[0][0] == "record"
    assert rows[0][-2:] == ["spools", "revisions"]
    assert len(rows) == 2
    assert "'-Charcoal PLA" in rows[1]
    assert "11;12" in rows[1]


def test_export_activity(isolated_state):
    events.write("spool.added", {"type": "spool", "id": "15"}, {"type": "page", "name": "alice"}, {"spool": "15"})
    rows = _parse_csv(csv_io.export_csv("activity", days=7))
    assert rows[0] == ["time", "who", "kind", "subject", "text"]
    assert len(rows) == 2
    assert rows[1][1] == "alice"
    assert rows[1][2] == "spool.added"
    assert rows[1][3] == "spool:15"


def test_export_printers_omits_secrets(isolated_state):
    p1 = registry.Printer(
        serial="01P00A000000099",
        name="Workshop X1",
        model="X1C",
        ip="192.168.1.55",
        access_code="12345678",
        configured=True,
        family="bambu",
    )
    p2 = registry.Printer(
        serial="klipper-1",
        name="192.168.1.88",
        model="Voron 2.4",
        host="192.168.1.88",
        api_key="secret-moonraker-key",
        configured=True,
        family="klipper",
    )
    registry.upsert(p1)
    registry.upsert(p2)

    mgr = SimpleNamespace(
        status=lambda serial: {
            "ams": [{"slots": [{"label": "A1", "spool": {"id": 42}}]}]
        } if serial == "01P00A000000099" else None
    )
    out = csv_io.export_csv("printers", printers_mgr=mgr)
    assert "192.168.1.55" not in out
    assert "192.168.1.88" not in out
    assert "01P00A000000099" not in out
    assert "12345678" not in out
    assert "secret-moonraker-key" not in out

    rows = _parse_csv(out)
    assert rows[0] == ["name", "kind", "model", "slot", "spool"]
    assert ["Workshop X1", "bambu", "X1C", "A1", "42"] in rows
    assert ["", "klipper", "Voron 2.4", "", ""] in rows


def test_export_printers_keeps_owner_chosen_names_as_typed(isolated_state):
    """A name with a dash, or one word, is the owner's: only an address or the serial is left out."""
    registry.add_klipper("Ender-3", "192.0.2.50", 7125)
    registry.add_klipper("Prusa-MK4", "192.0.2.52", 7125)
    registry.add_klipper("Voron", "station.local", 7125)
    registry.add_klipper("", "workshop-voron", 7125)                   # the name defaults to the host
    registry.upsert(registry.Printer(serial="01P00A000000099", name="X1C-Garage", ip="192.0.2.51", configured=True))
    mgr = SimpleNamespace(status=lambda serial: None)                  # PrinterManager.status: no report held yet
    names = sorted(row[0] for row in _parse_csv(csv_io.export_csv("printers", printers_mgr=mgr))[1:])
    assert names == ["", "Ender-3", "Prusa-MK4", "Voron", "X1C-Garage"]


def test_export_printers_without_the_hubs_printers_reads_the_slots_from_spoolman(isolated_state, spoolman_url):
    """The command line's export (as root, no live printers) keeps the slot and spool columns: a slot's spool
    is the one Spoolman holds at its location, as in a printer's status."""
    registry.upsert(registry.Printer(serial="01P00A000000099", name="Workshop X1", ip="192.0.2.55", configured=True))
    registry.add_klipper("Voron", "voron.local", 7125)
    sm = Spoolman(spoolman_url)
    fil = sm.post("filament", {"name": "Red PLA", "material": "PLA"})
    a = sm.post("spool", {"filament_id": fil["id"], "location": "Workshop X1 – AMS A2"})
    b = sm.post("spool", {"filament_id": fil["id"], "location": "Voron – T0"})
    sm.post("spool", {"filament_id": fil["id"], "location": "Shelf"})
    rows = _parse_csv(csv_io.export_csv("printers", sm=sm))
    assert rows[1:] == [["Voron", "klipper", "", "T0", str(b["id"])],
                        ["Workshop X1", "bambu", "", "A2", str(a["id"])]]


def test_export_lines(isolated_state):
    lns = {
        "polymaker__polyterra_pla": lines_mod.Line(
            line_id="polymaker__polyterra_pla",
            vendor="Polymaker",
            product="PolyTerra PLA",
            material="PLA",
            bambu_parent="Generic PLA",
            parent_source="library",
            filament_id="P1234abc",
            created="2026-11-01T00:00:00Z",
        )
    }
    lines_mod.save(lns)
    rows = _parse_csv(csv_io.export_csv("lines"))
    assert rows[0] == ["vendor", "line", "material", "preset"]
    assert rows[1] == ["Polymaker", "PolyTerra PLA", "PLA", "Generic PLA"]


def test_export_spoolman_passthrough_and_unavailable(monkeypatch):
    class FakeResp:
        def __init__(self, body: bytes):
            self._body = body

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(
        csv_io.urllib.request,
        "urlopen",
        lambda req, timeout=15: FakeResp(b"id,name\n1,=BAD()\n"),
    )
    sm = Spoolman("http://127.0.0.1:7912")
    spools_csv = csv_io.export_csv("spoolman-spools", sm=sm)
    assert _parse_csv(spools_csv) == [["id", "name"], ["1", "'=BAD()"]]

    filaments_csv = csv_io.export_csv("spoolman-filaments", sm=sm)
    assert _parse_csv(filaments_csv) == [["id", "name"], ["1", "'=BAD()"]]

    def raise_404(req, timeout=15):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, io.BytesIO(b""))

    monkeypatch.setattr(csv_io.urllib.request, "urlopen", raise_404)
    with pytest.raises(csv_io.SpoolmanExportUnavailable):
        csv_io.export_csv("spoolman-spools", sm=sm)


def test_import_spools_dry_run_and_apply(isolated_state, spoolman_url):
    sm = Spoolman(spoolman_url)
    # Seed an existing spool to verify additive-only (no deletes)
    v = sm.post("vendor", {"name": "ExistingVendor"})
    f = sm.post("filament", {"name": "ExistingFil", "vendor_id": v["id"], "material": "PLA", "weight": 1000, "spool_weight": 200, "density": 1.24})
    existing = sm.post("spool", {"filament_id": f["id"], "remaining_weight": 1000, "location": "Shelf A"})

    csv_data = (
        "vendor,name,material,color_hex,count,net,tare,sealed,location,lot\n"
        "Polymaker,PolyTerra PLA Charcoal Black,PLA,#2B2B2E,2,1000,140,yes,,F57\n"
        "Bambu Lab,PETG HF Orange,PETG,#F2762E,1,0,,yes,Shelf B,\n"
    )
    dry = csv_io.import_spools(sm, csv_data)
    assert dry["dry"] is True
    assert dry["add"] == 2
    assert "spool_ids" not in dry
    assert dry["rows"][0]["ok"] is True
    assert dry["rows"][1]["ok"] is False
    assert dry["rows"][1]["problems"][0]["column"] == "net"
    assert len(sm.get("spool")) == 1

    valid_only = (
        "vendor,name,material,color_hex,count,net,tare,sealed,location,lot\n"
        "Polymaker,PolyTerra PLA Charcoal Black,PLA,#2B2B2E,2,1000,140,yes,,F57\n"
    )
    applied = csv_io.import_spools(sm, valid_only, dry_run=False)
    assert applied["dry"] is False
    assert applied["add"] == 2
    assert len(applied["spool_ids"]) == 2
    all_spools = sm.get("spool")
    assert len(all_spools) == 3
    assert any(s["id"] == existing["id"] for s in all_spools)


def test_import_locations_dry_run_and_apply_no_deletes(isolated_state):
    before_st = storage.Storage(locations=[
        storage.Location(name="Sealed", type="bulk", fill="none", sealed=True, id="loc-1"),
        storage.Location(name="Overflow", type="bulk", fill="none", overflow=True, default_overflow=True, id="loc-2"),
        storage.Location(name="KeepMe", type="bulk", limit=5, fill="any", id="loc-3"),
    ])
    storage.save(before_st)

    csv_data = (
        "name,type,limit,count,prefix,fill,materials,overflow,default_overflow,sealed\n"
        "KeepMe,bulk,12,,,materials,PLA;PETG,no,no,no\n"
        "NewShelf,bulk,8,,,any,,no,no,no\n"
    )
    dry = csv_io.import_locations(csv_data)
    assert dry["dry"] is True
    assert dry["add"] == 1
    assert dry["change"] == 1
    assert all(r["ok"] for r in dry["rows"])
    assert len(storage.current().locations) == 3

    applied = csv_io.import_locations(csv_data, dry_run=False)
    assert applied["dry"] is False
    assert applied["add"] == 1
    assert applied["change"] == 1

    after_locs = storage.current().locations
    names = [loc.name for loc in after_locs]
    assert names == ["Sealed", "Overflow", "KeepMe", "NewShelf"]
    assert storage.current().by_name("KeepMe").limit == 12
    assert storage.current().by_name("KeepMe").materials == ["PLA", "PETG"]
