"""The kit API, the command line and the string catalogue after the wave-1 review (refuter 03): each test fails on
wave1-int 70ea5acf and passes now. The command line asks a real `create_app` through a TestClient (never a fake
with a method the real KitApi lacks) and runs the root commands on the real modules."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

import re
from filament_kit import (address_texts, alerts_api, api, backup, cli, config, csv_io, event_sink, events, getting_started,
                          history_cli, klipper_cli, pairing, report, secret_input, status, storage, undo)
from filament_kit.api import create_app
from filament_kit.web.slots import CLI_DEVICE, SlotError


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt", "bk"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper() if name != 'bk' else 'BACKUPS'}", str(tmp_path / name))
    monkeypatch.setenv("FK_NO_SYSTEMD", "1")
    monkeypatch.setenv("COLUMNS", "100")
    pairing.make_cert("fkhost", [], tmp_path / "etc")
    config.save({})
    monkeypatch.setattr(cli, "_is_root", lambda: True)
    monkeypatch.setattr(cli, "_hub_audit", lambda what: None)
    event_sink.set_sink(event_sink.StoreSink(events.store()))
    yield tmp_path
    event_sink.set_sink(None)


def token(name: str = "phone", role=None) -> str:
    return pairing.redeem_code((pairing.issue(name, role=role) if role else pairing.issue(name)).code)


def auth(tok: str) -> dict:
    return {"Authorization": f"Bearer {tok}"}


class HubApi:
    """The command line's view of the Hub: the real routes of `create_app`, with the `get`/`post` of the real KitApi
    (a JSON answer, a SlotError for a refusal)."""

    def __init__(self, tok: str):
        self.c, self.tok = TestClient(create_app()), tok

    def _answer(self, r):
        if r.status_code >= 400:
            e = SlotError(r.json().get("message") or "refused")
            e.http, e.code = r.status_code, r.json().get("error")
            raise e
        return r.json()

    def get(self, path):
        return self._answer(self.c.get(path, headers=auth(self.tok)))

    def post(self, path, body=None):
        return self._answer(self.c.post(path, json=body, headers=auth(self.tok)))


def run(capsys, argv):
    try:
        rc = cli.main(argv)
    except SystemExit as e:
        rc = e.code
    got = capsys.readouterr()
    return rc, got.out, got.err


def _spool_moved(n: int, actor, *, at=None):
    return events.write("spool.moved", {"type": "spool", "id": str(n)}, actor,
                        {"spool": str(n), "before_location": "Shelf A", "after_location": "Shelf B"}, now=at)


# ---------- finding 6: the newest events (D1) ----------

def test_the_events_route_gives_the_newest_n_in_ascending_order():
    for n in range(1, 13):
        _spool_moved(n, "cli")
    c, h = TestClient(create_app()), auth(token())
    got = c.get("/api/events?activity=1&newest=5", headers=h).json()
    last = events.store().last_id()
    assert [e["id"] for e in got["events"]] == list(range(last - 4, last + 1))     # the five newest, oldest of them first
    assert [e["id"] for e in c.get("/api/events?activity=1&newest=3", headers=h).json()["events"]] == [last - 2, last - 1, last]
    assert c.get("/api/events?newest=5&since=3", headers=h).status_code == 400
    bad = c.get("/api/events?newest=0", headers=h)
    assert bad.status_code == 400 and bad.json()["error"] == "bad_request"


def test_activity_shows_the_newest_200_and_says_so(capsys):
    for n in range(1, 251):
        _spool_moved(n, "cli")
    assert history_cli.cmd_activity(HubApi(token())) == 0
    out = capsys.readouterr().out
    shown = [int(m.group(1)) for m in re.finditer(r"^\s*(\d+)\s+\d{4}-\d\d-\d\d ", out, re.M)]
    assert len(shown) == 200
    last = events.store().last_id()
    assert shown[0] == last and shown[-1] == last - 199
    assert "Showing the newest 200." in out


# ---------- finding 5: what the command line writes is `cli` (D8) ----------

def test_a_change_by_the_command_lines_own_pairing_is_written_as_cli(monkeypatch):
    seen = []
    monkeypatch.setattr(api.csv_io, "import_locations",
                        lambda raw, dry_run=True, **kw: seen.append(event_sink.current_actor()) or {"rows": [], "add": 0})
    c = TestClient(create_app())
    c.post("/api/import/locations?dry_run=0", content=b"name\nA\n", headers=auth(token(CLI_DEVICE)))
    c.post("/api/import/locations?dry_run=0", content=b"name\nA\n", headers=auth(token("phone")))
    assert seen[0]["type"] == "cli" and seen[1]["type"] == "device" and seen[1]["name"]


def test_plain_undo_undoes_the_last_command_line_change(capsys):
    sm = _Spoolman({1: {"id": 1, "location": "Shelf B", "archived": False}})
    ev = _spool_moved(1, {"type": "cli"})
    for n in range(2, 60):                                  # newer changes by others do not hide it
        _spool_moved(n, {"type": "page", "name": "Hub page"})
    rc = history_cli.cmd_undo(HubApi(token()), lambda: sm, yes=True)
    out = capsys.readouterr().out
    assert rc == 0 and "Put back." in out
    assert sm.spools[1]["location"] == "Shelf A" and events.store().get(ev["id"])["undone"] is True
    assert events.store().read(since=ev["id"], newest=1, include_undo=True)["events"][-1]["actor"]["type"] == "cli"


class _Spoolman:
    """The two calls `undo` makes on the real Spoolman class."""

    def __init__(self, spools):
        self.spools = spools

    def get(self, path):
        return dict(self.spools[int(path.split("/")[1])])

    def patch(self, path, body):
        self.spools[int(path.split("/")[1])].update(body)
        return dict(self.spools[int(path.split("/")[1])])


# ---------- finding 8: --who <device name> ----------

def test_activity_who_finds_a_device_by_the_name_in_its_events(capsys):
    events.write("spool.moved", {"type": "spool", "id": "1"}, {"type": "device", "id": "d1", "name": "Pat's phone"},
                 {"spool": "1", "before_location": "A", "after_location": "B"})
    _spool_moved(2, {"type": "page", "name": "Hub page"})
    assert history_cli.cmd_activity(HubApi(token()), who="pat's phone") == 0
    out = capsys.readouterr().out
    assert "Pat's phone" in out and "Hub page" not in out
    assert history_cli.cmd_activity(HubApi(token()), who="nobody") == 2
    assert "unknown actor nobody." in capsys.readouterr().err


# ---------- findings 1 to 4: export, import, report and the backup password run as root on the modules (D5) ----------

def test_export_writes_the_csv_of_csv_io_and_needs_root(capsys, monkeypatch, tmp_path):
    out = tmp_path / "locations.csv"
    assert run(capsys, ["export", "locations", "--out", str(out)])[0] == 0
    assert out.read_bytes().decode("utf-8") == csv_io.export_csv("locations")
    monkeypatch.setattr(cli, "_is_root", lambda: False)
    rc, _, err = run(capsys, ["export", "locations"])
    assert rc == 3 and "administrator rights" in err


def test_import_sends_the_file_as_it_is_and_adds_the_rows(capsys, tmp_path):
    f = tmp_path / "in.csv"
    f.write_text(csv_io.export_csv("locations") + "Garage,,,,,,,\n", encoding="utf-8")
    rc, out, _ = run(capsys, ["import", "locations", str(f), "--yes"])
    assert rc == 0 and "Checked 5 rows: 5 can be added" in out and "Added 1, changed 4 locations." in out
    assert "Garage" in [loc.name for loc in storage.current().locations]


def test_report_is_collected_by_the_module(capsys, tmp_path):
    rc, out, _ = run(capsys, ["report"])
    assert rc == 0 and report.latest() is not None and str(report.latest()) in out.replace("\n", "")
    copy = tmp_path / "copy.txt"
    assert run(capsys, ["report", "--out", str(copy)])[0] == 0 and copy.read_text(encoding="utf-8").strip()


def test_backup_password_is_set_shown_and_removed_through_the_helper(capsys, monkeypatch):
    monkeypatch.setattr(secret_input, "ask", lambda prompt: "correct horse battery")
    assert run(capsys, ["backup", "password", "--show"])[1] == "Backup password is not set.\n"
    assert run(capsys, ["backup", "password"])[1] == "Backup password saved.\n"
    assert backup.password_set() and backup.read_password() == "correct horse battery"
    assert run(capsys, ["backup", "password", "--show"])[1] == "Backup password is set.\n"
    assert run(capsys, ["backup", "password", "--remove"])[1] == "Backup password removed.\n"
    assert not backup.password_set()


def test_a_password_that_is_too_short_is_refused_with_the_helpers_words(capsys, monkeypatch):
    monkeypatch.setattr(secret_input, "ask", lambda prompt: "short")
    rc, _, err = run(capsys, ["backup", "password"])
    assert rc == 2 and "Backup password must be at least" in err and not backup.password_set()


# ---------- finding 7 and 10 and 11: Auto on the command line ----------

class _AutoApi:
    """The routes `printer load ... auto` uses, answering as bridge/auto_slot.py does (the real answer has no `watching`)."""

    def __init__(self):
        self.calls = []

    def get(self, path):
        self.calls.append(("GET", path))
        return {"auto": {"state": "waiting", "spool_id": 7, "spool": {"id": 7, "name": "Polymaker PolyTerra PLA Black"},
                         "text": "Waiting for Polymaker PolyTerra PLA Black to be loaded into any slot of X1."}}

    def post(self, path, body=None):
        self.calls.append(("POST", path, body))
        if path.endswith("/auto/cancel"):
            return {"cancelled": True}
        if (body or {}).get("dry_run"):
            return {"auto": True, "watching": ["A1", "A2"], "dry_run": True}
        return {"auto": True, "pending": True, "confirmed": None, "expires_in": 900, "message": "waiting"}


def test_printer_load_auto_stop_stops_the_waiting_without_asking(capsys):
    a = _AutoApi()
    assert klipper_cli.cmd_printer_slot(a, {"serial": "P1", "name": "X1"}, "load", "auto", None, False, stop=True) == 0
    assert capsys.readouterr().out == "Stopped waiting for Polymaker PolyTerra PLA Black.\n"
    assert a.calls[-1] == ("POST", "/api/printers/P1/auto/cancel", {})


def test_printer_load_auto_stop_parses_with_no_spool_number():
    parser = argparse.ArgumentParser()
    psub = parser.add_subparsers(dest="printer_cmd")
    klipper_cli.add_printer_parsers(psub)
    args = parser.parse_args(["load", "x1", "auto", "--stop"])
    assert args.stop is True and args.spool is None


def test_a_slot_typed_AUTO_is_the_auto_route(capsys):
    a = _AutoApi()
    assert klipper_cli.cmd_printer_slot(a, {"serial": "P1", "name": "X1"}, "load", "AUTO", "7", True) == 0
    assert all("/slots/auto/load" in c[1] for c in a.calls if c[0] == "POST")


def test_the_auto_done_line_names_the_slots_the_dry_run_listed(capsys):
    a = _AutoApi()
    assert klipper_cli.cmd_printer_slot(a, {"serial": "P1", "name": "X1"}, "load", "auto", "7", True) == 0
    assert "Watching A1, A2 on X1 for spool 7." in capsys.readouterr().out


class _ShowHub:
    def __init__(self, auto):
        self.auto = auto

    def view(self, serial):
        return {"auto": self.auto}


def test_printer_show_prints_the_auto_notice_and_the_choice(capsys, monkeypatch):
    row = {"serial": "P1", "name": "Workshop", "model": "x", "family": "bambu", "configured": True, "pinned": True,
           "state": "connected", "report_why": None, "ip": "10.0.0.2", "port": None}
    waiting = {"state": "waiting", "spool_id": 7, "text": "Waiting for PLA Black to be loaded into any slot of Workshop."}
    choose = {"state": "choose", "spool_id": 7, "slots": ["A2", "A3"], "text": "Two slots were loaded. Which one got PLA Black?"}
    for auto, want in ((waiting, "Note: Waiting for PLA Black to be loaded into any slot of Workshop."),
                       (choose, "Which one got PLA Black? A2, A3")):
        hub = _ShowHub(auto)
        hub.rows = lambda: [row]
        hub.slots = lambda serial: []
        hub.up = True
        monkeypatch.setattr(cli, "_printer_named", lambda *a, **k: row)
        monkeypatch.setattr(cli, "_hub", lambda hub=hub: hub)
        monkeypatch.setattr(cli, "_address_of", lambda r: "10.0.0.2")
        assert cli.cmd_printer_show("Workshop") == 0
        out = capsys.readouterr().out
        assert " ".join(want.split()) in " ".join(out.split())
        if auto is choose:
            assert "spoolwarden printer load Workshop A2 7" in out


def test_forgetting_a_printer_ends_its_auto_assignment(capsys, monkeypatch):
    a = _AutoApi()
    hub = type("H", (), {"api": a, "up": True})()
    cli._drop_auto(hub, "P1")
    assert a.calls == [("POST", "/api/printers/P1/auto/cancel", {})]
    hub = type("H", (), {"api": None, "up": False})()
    cli._drop_auto(hub, "P1")                                  # no Hub: nothing to end, no error


# ---------- finding 12 and 13: the viewer gate and the import size ----------

def test_a_viewer_token_may_read_and_nothing_more():
    c = TestClient(create_app())
    h = auth(token("Home Assistant", role=pairing.VIEWER_ROLE))
    assert c.get("/api/health", headers=h).status_code == 200
    assert c.get("/api/events", headers=h).status_code == 200
    for method, path in (("POST", "/api/printers"), ("GET", "/api/export/locations.csv"),
                         ("POST", "/api/import/locations"), ("POST", "/api/events/1/undo"),
                         ("POST", "/api/alerts/test")):
        r = c.request(method, path, headers=h)
        assert r.status_code == 403 and r.json()["error"] == "forbidden", (method, path)


def test_an_import_body_over_5_mib_is_refused():
    c = TestClient(create_app())
    r = c.post("/api/import/locations", content=b"a" * (5 * 1024 * 1024 + 1), headers=auth(token()))
    assert r.status_code == 413 and r.json()["error"] == "too_large" and "5 MB" in r.json()["message"]


# ---------- finding 14: the texts are the catalogue's ----------

def test_the_satellite_and_device_rows_say_the_designs_words(monkeypatch):
    from filament_kit import station_client
    monkeypatch.setattr(station_client, "station_https", lambda url: "plain")
    monkeypatch.setattr(status, "_plain_reason", lambda: None)
    rows = list(status._satellite_rows({"station": {"url": "https://192.168.1.50:8711"}}))
    assert rows[0].fix == "Update it: on that machine run sudo spoolwarden update."
    monkeypatch.setattr(status, "_plain_reason", lambda: "no openssl")
    rows = list(status._satellite_rows({"station": {"url": "https://192.168.1.50:8711"}}))
    assert rows[0].fix == address_texts.say("check_station_not_https_why_fix")
    assert address_texts.say("check_devices_out_of_date", count=1) == "1 device is out of date."
    assert address_texts.say("check_devices_out_of_date", count=3) == "3 devices are out of date."


def test_the_catalogue_holds_what_the_reworked_code_names():
    cat = json.loads((Path(__file__).resolve().parents[2] / "strings" / "en.json").read_text(encoding="utf-8"))
    for key in ("spools_toast_weight", "spools_undo_name", "spools_put_back", "spools_events_title", "spools_csv_failed",
                "spools_more_actions", "station_page_title", "station_page_page_from_subnets_note", "station_refusal_forbidden",
                "auto_waiting", "auto_stopped", "auto_slot_gone", "auto_not_waiting", "station_not_pinned",
                "reader_auto_queued", "cli_actor_page", "cli_slot_auto_stopped"):
        assert key in cat, key
    assert cat["station_not_pinned"].endswith("on that machine run sudo {command} update.")
    named = lambda t: t.replace("{product}", "Spoolwarden").replace("{command}", "spoolwarden")      # noqa: E731
    for key, text in history_cli.catalogue_texts().items():
        assert named(cat[key]) == text, key


# ---------- 01.6: the backup step takes the helper's answer ----------

def test_getting_started_takes_the_backup_answer_without_listing_the_folder(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("the root-only folder was listed")
    monkeypatch.setattr(backup, "listing", boom)
    for done in (True, False):
        steps = {s["id"]: s for s in getting_started.view(backup_done=done)["steps"]}
        assert (steps["backup"]["state"] == "done") is done


# ---------- 15: restore passes its arguments straight on ----------

def test_restore_passes_password_file_and_actor_without_probing(capsys, monkeypatch, tmp_path):
    z = tmp_path / "b.zip"
    z.write_bytes(b"PK")
    seen = {}
    monkeypatch.setattr(backup, "restore", lambda path, **kw: seen.update(kw) or 0)
    monkeypatch.setattr(cli.firstrun, "hide_after_restore", lambda: None)
    assert run(capsys, ["restore", str(z), "-y"])[0] == 0
    assert seen["password_file"] is None and seen["actor"] == "cli"


# ---------- requests from builder 13: the cli actor, the health rows, the spool number ----------

def test_a_command_runs_under_the_cli_actor(capsys, monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "_dispatch", lambda args, **kw: seen.append(event_sink.current_actor()) or 0)
    assert cli.main(["status"]) == 0
    assert seen[0]["type"] == "cli"


def test_the_health_route_adds_the_overviews_rows_with_their_group(monkeypatch):
    monkeypatch.setattr(status, "collect", lambda cfg=None, *a, **k: [
        status.Row(status.OK, "display name: Hub", group="Hub", key="hub.display-name"),
        status.Row(status.INFO, "a note", group="Hub", key="hub.note")])
    got = TestClient(create_app()).get("/api/health", headers=auth(token())).json()
    row = next(c for c in got["checks"] if c["key"] == "hub.display-name")
    assert row["group"] == "Hub" and row["now"] == "ok" and not any(c["key"] == "hub.note" for c in got["checks"])


def test_printer_load_without_a_spool_number_says_so(capsys):
    assert klipper_cli.cmd_printer_slot(_AutoApi(), {"serial": "P1", "name": "X1"}, "load", "A1", None) == 2
    assert "load needs the spool's number." in capsys.readouterr().err
