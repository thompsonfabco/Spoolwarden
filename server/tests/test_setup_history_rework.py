"""Hub history route regressions using the real root helper and data modules."""
import csv
import io

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


def test_generic_job_does_not_start_port_change_without_pending_port(env):
    reply = setup_helper.handle({"op": "job", "name": "port_change", "code": CODE})
    assert reply["status"] == 400


def test_printers_csv_preserves_owner_chosen_klipper_name_but_omits_host(env, manager):
    registry.add_klipper("Voron", "station.local", 7125)
    registry.add_klipper("second", "second", 7125)
    page = SetupPage(manager, families={"klipper"})
    rows = list(csv.DictReader(io.StringIO(printers_csv(page))))
    assert sorted(row["name"] for row in rows) == ["", "Voron"]
