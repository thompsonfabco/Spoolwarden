"""Hub history route regressions using the real root helper and data modules."""
import csv
import io
from types import SimpleNamespace

from filament_kit import events, report, setup_helper, trusted
from filament_kit.bridge import registry
from filament_kit.setup_console.printers_tab import printers_csv
from filament_kit.setup_page import SetupPage
from filament_kit.spoolman import Spoolman
from starlette.applications import Starlette

from test_setup_page import CODE, H, HOSTNAME, app, client, env, helper, manager, post  # noqa: F401


def test_report_download_opens_the_root_owned_file_via_helper(app, helper, monkeypatch, tmp_path):
    monkeypatch.setenv("FK_BACKUPS", str(tmp_path / "backups"))
    c = client(app)
    assert c.get("/setup/api/report/download", headers={"X-Setup-Code": CODE}).status_code == 404
    path = report.collect(cfg={"modules": {}}, journal_fn=lambda unit, lines: [])
    for reply in (c.get("/setup/api/report/download", headers={"X-Setup-Code": CODE}),
                  post(c, "report/download", {})):
        assert reply.status_code == 200
        assert reply.content == path.read_bytes()
        assert path.name in reply.headers["content-disposition"]
    assert helper.calls[-2:] == ["report_open", "report_open"]


def test_history_undo_uses_real_event_module_and_records_page_actor(env, helper, spoolman_url):
    sm = Spoolman(spoolman_url)
    filament = sm.post("filament", {"name": "Red PLA", "material": "PLA"})
    spool = sm.post("spool", {"filament_id": filament["id"], "location": "Shelf Two"})
    page = SetupPage(helper=helper, hosts=lambda: {HOSTNAME}, spoolman=sm)
    c = client(Starlette(routes=page.routes()))
    trusted.set_enabled(True)
    _, token = trusted.add("Workshop computer", "192.0.2.10")
    c.cookies.set(trusted.COOKIE, token)
    event = events.write("spool.moved", {"type": "spool", "id": str(spool["id"])}, "cli",
                         {"spool": str(spool["id"]), "before_location": "Shelf One", "after_location": "Shelf Two"})
    result = post(c, f"events/{event['id']}/undo", {})
    assert result.status_code == 200
    assert result.json()["event"]["actor"]["type"] == "page"
    assert result.json()["event"]["actor"]["name"] == "Workshop computer"
    assert sm.get(f"spool/{spool['id']}")["location"] == "Shelf One"
    assert "trust_status" in helper.calls
    assert post(c, "events/99999/undo", {}).status_code == 404
    assert c.get("/setup/api/health?days=30").json()["days"] == 30


def test_csv_routes_use_real_export_and_import_contract(app):
    c = client(app)
    get = c.get("/setup/api/export/locations.csv", headers={"X-Setup-Code": CODE})
    assert get.status_code == 200 and get.headers["content-type"].startswith("text/csv")
    result = c.post("/setup/api/import/locations", content=b"name\nTest shelf\n",
                    headers={**H, "X-Setup-Code": CODE})
    assert result.status_code == 200 and result.json()["dry"] is True


def test_activity_takes_newest_and_refuses_it_beside_since(app):
    """D1 on the Hub page's route (asked by the page's spool events): `newest=N` is the newest N, oldest first;
    with `since` it is refused in a catalogue sentence."""
    from filament_kit.station.page import say
    c = client(app)
    written = [events.write("spool.moved", {"type": "spool", "id": "7"}, "cli",
                            {"spool": "7", "before_location": f"A{n}", "after_location": f"B{n}"})["id"] for n in range(3)]
    got = c.get("/setup/api/activity?subject=spool:7&newest=2")
    assert got.status_code == 200, got.text
    assert [e["id"] for e in got.json()["events"]] == written[1:]
    bad = c.get("/setup/api/activity?newest=2&since=0")
    assert bad.status_code == 400 and bad.json()["error"] == say("hub_newest_with_since")


def test_the_hub_pages_new_refusals_are_catalogue_texts(app):
    """D13: a sentence the wave added to the Hub page's routes is a catalogue text."""
    from filament_kit.station.page import say
    c = client(app)
    assert c.get("/setup/api/health?days=9").json()["error"] == say("hub_health_days")
    assert c.get("/setup/api/activity?limit=x").json()["error"] == say("hub_number_needed", name="limit")
    assert post(c, "events/x/undo", {}).json()["error"] == say("hub_no_such_event")
    assert post(c, "start", {}).json()["error"] == say("hub_start_hide_or_keep")
    assert post(c, "start", {"hide": "yes"}).json()["error"] == say("hub_start_hide_bool")
    assert post(c, "start", {"keep": "x"}).json()["error"] == say("hub_start_keep_which")
    unknown = c.get("/setup/api/export/nothing.csv", headers={"X-Setup-Code": CODE})
    assert unknown.status_code == 404 and unknown.json()["error"] == say("hub_unknown_export", name="nothing")
    for key in ("hub_backup_password_short", "hub_port_give", "hub_number_minimum", "hub_undo_spoolman_down",
                "hub_csv_too_large", "hub_csv_not_utf8", "hub_library_check_fields", "hub_library_missing_id",
                "hub_library_no_such_entry", "hub_slot_auto_reserved", "hub_slot_label_needed", "hub_slot_label_invalid",
                "hub_moonraker_bad_key", "hub_start_not_saved", "auto_not_possible", "auto_nothing_to_choose",
                "auto_not_waiting"):
        assert say(key, count=10, name="days", minimum=1)
    import re
    from pathlib import Path
    from filament_kit import setup_console
    here = Path(setup_console.__file__).parent
    for name in ("backup_tab.py", "general_tab.py", "history_tab.py", "library_tab.py", "printer_live_tab.py",
                 "printers_tab.py", "start_tab.py"):
        source = (here / name).read_text(encoding="utf-8")
        for sentence in ("give the port number", "no such event", "Spoolman cannot be reached for this undo",
                         "days must be 7 or 30", "the CSV file", "check the highlighted fields", "missing entry id",
                         "no such custom catalog entry", "is reserved for Automatic", "No spool is waiting",
                         "slot must be a", "Moonraker wants a valid API key", "give either hide or keep",
                         "hide must be true or false", "could not save the checklist state", "keep must be name",
                         "Backup password must be at least", "no slots the Hub can watch"):
            assert not re.search(r'["\']' + re.escape(sentence), source), (name, sentence)


def test_generic_job_does_not_start_port_change_without_pending_port(env):
    reply = setup_helper.handle({"op": "job", "name": "port_change", "code": CODE})
    assert reply["status"] == 400


def test_printers_csv_preserves_owner_chosen_klipper_name_but_omits_host(env, manager):
    registry.add_klipper("Voron", "station.local", 7125)
    registry.add_klipper("second", "second", 7125)
    page = SetupPage(manager, families={"klipper"})
    rows = list(csv.DictReader(io.StringIO(printers_csv(page))))
    assert sorted(row["name"] for row in rows) == ["", "Voron"]


def test_printers_csv_keeps_owner_chosen_names_with_a_dash_as_the_kit_export_does(env, manager):
    """R559 3: "Ender-3", "Prusa-MK4" (Klipper) and "X1C-Garage" (Bambu Lab) are the owner's names, not addresses; the
    Hub page's export and the kit's give the same names."""
    from filament_kit import csv_io
    registry.add_klipper("Ender-3", "192.0.2.50", 7125)
    registry.add_klipper("Prusa-MK4", "192.0.2.52", 7125)
    registry.add_klipper("Voron", "station.local", 7125)
    registry.upsert(registry.Printer(serial="01P00A000000099", name="X1C-Garage", ip="192.0.2.51", configured=True))
    page = SetupPage(manager, families={"klipper", "bambu"})
    names = sorted(row["name"] for row in csv.DictReader(io.StringIO(printers_csv(page))))
    assert names == ["Ender-3", "Prusa-MK4", "Voron", "X1C-Garage"]
    no_status = SimpleNamespace(status=lambda serial: None)          # PrinterManager.status: no report held yet
    kit = sorted(row["name"] for row in csv.DictReader(io.StringIO(csv_io.export_csv("printers", printers_mgr=no_status))))
    assert kit == names
