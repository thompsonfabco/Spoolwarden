"""The kit API, the command line and the string catalogue after the wave-1 review (refuter 03): each test fails on
wave1-int 70ea5acf and passes now. The command line asks a real `create_app` through a TestClient (never a fake
with a method the real KitApi lacks) and runs the root commands on the real modules."""
from __future__ import annotations

import argparse
import datetime as dt
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


def _shown(out: str) -> list[int]:
    return [int(m.group(1)) for m in re.finditer(r"^\s*(\d+)\s+\d{4}-\d\d-\d\d ", out, re.M)]


def test_activity_without_days_shows_the_newest_50_newest_first(capsys):
    """`newest=50`, not the newest 200 cut by 7 days. This test pinned the newest 200
    before; it now pins the """
    hub = HubApi(token())                                    # its pairing is an Activity event too: the oldest here
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    for n in range(1, 31):
        _spool_moved(n, "cli", at=old)                       # older than 7 days: still among the newest 50
    for n in range(31, 61):
        _spool_moved(n, "cli")
    assert history_cli.cmd_activity(hub) == 0
    out = capsys.readouterr().out
    shown, last = _shown(out), events.store().last_id()
    assert shown == list(range(last, last - 50, -1))
    assert "Showing" not in out and "in the last" not in out


def test_activity_with_days_shows_from_that_time_oldest_first(capsys):
    old = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=30)
    for n in range(1, 6):
        _spool_moved(n, "cli", at=old)
    recent = [_spool_moved(n, "cli")["id"] for n in range(6, 9)]
    assert history_cli.cmd_activity(HubApi(token()), who="cli", days=7) == 0      # without the pairing's own event
    out = capsys.readouterr().out
    assert _shown(out) == recent
    assert "3 events in the last 7 days." in out and "Showing" not in out


def test_activity_with_days_stops_at_1000_and_says_so(capsys):
    ids = events.write_many([{"kind": "spool.moved", "subject": {"type": "spool", "id": str(n)}, "actor": "cli",
                              "data": {"spool": str(n), "before_location": "Shelf A", "after_location": f"Shelf {n}"}}
                             for n in range(1, 1006)])
    assert history_cli.cmd_activity(HubApi(token()), who="cli", days=7) == 0
    out = capsys.readouterr().out
    assert _shown(out) == ids[:1000]                                          # the first 1000, oldest first
    assert "Showing the first 1000. Narrow it with --days, --who or --spool." in out


def test_activity_with_nothing_to_show_says_the_ruled_line(capsys):
    hub = HubApi(token())
    assert history_cli.cmd_activity(hub, who="cli") == 0
    assert capsys.readouterr().out == "Nothing has been done yet.\n"
    assert history_cli.cmd_activity(hub, who="cli", days=3) == 0
    assert capsys.readouterr().out == "Nothing has been done in the last 3 days.\n"


def test_activity_parses_with_no_days_by_default():
    parser = argparse.ArgumentParser()
    history_cli.add_parser(parser.add_subparsers(dest="cmd"))
    assert parser.parse_args(["activity"]).days is None


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
    events.write("spool.moved", {"type": "spool", "id": "1"}, {"type": "device", "id": "d1", "name": "Sam's phone"},
                 {"spool": "1", "before_location": "A", "after_location": "B"})
    _spool_moved(2, {"type": "page", "name": "Hub page"})
    assert history_cli.cmd_activity(HubApi(token()), who="sam's phone") == 0
    out = capsys.readouterr().out
    assert "Sam's phone" in out and "Hub page" not in out
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


def test_sudo_export_printers_keeps_the_slot_columns(capsys, spoolman_url):
    """`sudo spoolwarden export printers` goes through the command line (no live printers) and
    still lists a slot's spool, read from the spools' Spoolman locations."""
    from filament_kit import spoolman
    from filament_kit.bridge import registry
    config.save({"spoolman_url": spoolman_url})
    registry.add_klipper("Voron", "voron.local", 7125)
    sm = spoolman.Spoolman(spoolman_url)
    fil = sm.post("filament", {"name": "Red PLA", "material": "PLA"})
    spool = sm.post("spool", {"filament_id": fil["id"], "location": "Voron – T0"})
    rc, out, _ = run(capsys, ["export", "printers"])
    rows = [line.split(",") for line in out.strip().splitlines()]
    assert rc == 0 and ["Voron", "klipper", "", "T0", str(spool["id"])] in rows


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


SET_LINES = ("Write this password down. Without it these backups cannot be restored, and Spoolwarden cannot recover "
             "it.\nBackup password set. Every backup is now encrypted.\n")


def _words(text: str) -> str:
    return " ".join(text.split())                          # the terminal width wraps the long line


def _answer_yes(monkeypatch) -> list[str]:
    """A terminal whose owner answers y; returns the questions it was asked."""
    from filament_kit import output
    asked = []
    monkeypatch.setattr(output, "terminal", lambda *a, **k: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": asked.append(prompt) or "y")
    return asked


def test_backup_password_is_set_shown_and_removed_through_the_helper(capsys, monkeypatch):
    """The design, command line: this test pinned other texts before; it now pins the design's."""
    from filament_kit import output
    monkeypatch.setattr(output, "terminal", lambda *a, **k: True)
    monkeypatch.setattr(secret_input, "ask", lambda prompt: "correct horse battery")
    assert run(capsys, ["backup", "password", "--show"])[1] == "Backup password is not set.\n"
    assert _words(run(capsys, ["backup", "password"])[1]) == _words(SET_LINES)
    assert backup.password_set() and backup.read_password() == "correct horse battery"
    assert run(capsys, ["backup", "password", "--show"])[1] == "correct horse battery\n"      # the password itself
    asked = _answer_yes(monkeypatch)
    out = run(capsys, ["backup", "password", "--remove"])[1]
    assert out.endswith("Removed the backup password.\n") and asked                          # it asked first
    assert "Remove the backup password?" in " ".join((out + " ".join(asked)).split())
    assert not backup.password_set()


def test_backup_password_is_typed_twice_with_the_designs_prompts(capsys, monkeypatch):
    from filament_kit import output
    monkeypatch.setattr(output, "terminal", lambda *a, **k: True)
    answers = iter(["correct horse battery", "correct horse battery"])
    prompts = []
    monkeypatch.setattr(secret_input, "ask", lambda prompt: prompts.append(prompt) or next(answers))
    assert history_cli.run_backup_password() == 0
    assert prompts == ["New backup password, at least 10 characters: ", "New backup password again: "]
    assert backup.read_password() == "correct horse battery"
    out = capsys.readouterr().out
    assert _words(out) == _words(SET_LINES) and out.endswith("\nBackup password set. Every backup is now encrypted.\n")


def test_two_different_backup_passwords_are_refused(capsys, monkeypatch):
    from filament_kit import output
    monkeypatch.setattr(output, "terminal", lambda *a, **k: True)
    answers = iter(["correct horse battery", "correct horse batterY"])
    monkeypatch.setattr(secret_input, "ask", lambda prompt: next(answers))
    assert history_cli.run_backup_password() == 2
    assert "the two passwords do not match." in capsys.readouterr().err and not backup.password_set()


def test_backup_password_remove_without_a_yes_changes_nothing(capsys, monkeypatch):
    backup.write_password("correct horse battery")
    from filament_kit import output
    monkeypatch.setattr(output, "terminal", lambda *a, **k: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "n")
    assert history_cli.run_backup_password(remove=True) == 5
    assert backup.read_password() == "correct horse battery"


def test_a_password_that_is_too_short_is_refused_with_the_designs_words(capsys, monkeypatch):
    from filament_kit import output
    monkeypatch.setattr(output, "terminal", lambda *a, **k: True)
    monkeypatch.setattr(secret_input, "ask", lambda prompt: "short")
    rc, _, err = run(capsys, ["backup", "password"])
    assert rc == 2 and "use at least 10 characters." in err and not backup.password_set()


def test_backup_password_from_the_command_line_is_recorded_as_cli(monkeypatch):
    """The helper's settings.changed takes the actor of the command line, not a literal `page`."""
    from filament_kit import output
    monkeypatch.setattr(output, "terminal", lambda *a, **k: True)
    monkeypatch.setattr(secret_input, "ask", lambda prompt: "correct horse battery")
    with event_sink.acting("cli"):
        assert history_cli.run_backup_password() == 0
    ev = events.read(kinds="settings.changed")["events"][-1]
    assert ev["actor"]["type"] == "cli" and ev["data"]["names"] == "backup_password"
    from filament_kit import setup_helper
    setup_helper._backup_password_remove({})                # a request over the helper socket: the Hub page
    assert events.read(kinds="settings.changed")["events"][-1]["actor"]["type"] == "page"


# ---------- plain undo finds the newest change that can be undone ----------

def test_plain_undo_never_skips_to_an_older_change(capsys):
    """D15: plain `undo` is for the newest change of this command line. Once that one is undone, a second plain `undo` finds the
    compensating event as the newest and says so, naming `activity`; it does not undo the older change."""
    sm = _Spoolman({1: {"id": 1, "location": "Shelf B", "archived": False},
                    2: {"id": 2, "location": "Shelf B", "archived": False}})
    first, second = _spool_moved(1, {"type": "cli"}), _spool_moved(2, {"type": "cli"})
    hub = HubApi(token())
    assert history_cli.cmd_undo(hub, lambda: sm, yes=True) == 0
    assert events.store().get(second["id"])["undone"] is True
    capsys.readouterr()
    assert history_cli.cmd_undo(hub, lambda: sm, yes=True) == 0
    out = capsys.readouterr().out
    assert "cannot be undone" in out and "spoolwarden activity" in out and "spoolwarden undo <number>" in out
    assert not events.store().get(first["id"]).get("undone")             # the older change is untouched
    assert sm.spools[1]["location"] == "Shelf B" and sm.spools[2]["location"] == "Shelf A"


def test_plain_undo_says_so_when_the_newest_change_is_not_undoable(capsys):
    """D15: the newest command-line change is one the Hub does not undo (a settings change): the same sentence, nothing undone."""
    sm = _Spoolman({1: {"id": 1, "location": "Shelf B", "archived": False}})
    older = _spool_moved(1, {"type": "cli"})
    events.write("settings.changed", "hub", {"type": "cli"}, {"group": "backup", "names": "password"})
    assert history_cli.cmd_undo(HubApi(token()), lambda: sm, yes=True) == 0
    out = capsys.readouterr().out
    assert "cannot be undone" in out and "spoolwarden activity" in out
    assert not events.store().get(older["id"]).get("undone") and sm.spools[1]["location"] == "Shelf B"


# ---------- an automatic job is `hub` ----------

def test_the_scheduled_update_is_recorded_as_the_hub(monkeypatch):
    from filament_kit import updates
    seen = []
    monkeypatch.setattr(updates, "_update", lambda cfg, **kw: seen.append(event_sink.current_actor()["type"]) or 0)
    with event_sink.acting("cli"):                          # cli.main wraps every command so
        assert updates.update({}, scheduled=True) == 0
        assert updates.update({}) == 0
    assert seen == ["hub", "cli"]


# ---------- `status` without sudo asks the root helper about backups ----------

def test_the_backup_step_asks_the_root_helper_when_the_folder_is_closed(monkeypatch):
    import os
    (backup.backups_dir() / "2026-11-21.tar.gz").write_bytes(b"x")       # there, but this user may not list it
    monkeypatch.setattr(os, "access", lambda p, mode, **k: False)
    asked = []
    for last, want in (("2026-11-21 03:00", True), (None, False)):
        helper = lambda req, last=last: asked.append(req["op"]) or {"ok": True, "status": 200, "last": last}  # noqa: E731
        assert getting_started._backup_made(helper) is want
    assert asked == ["backup_last", "backup_last"]
    steps = {s["id"]: s for s in getting_started.view()["steps"]}           # `status`: no backup_done given
    assert steps["backup"]["state"] != "done"                               # no helper here: open, and no error


def test_the_backup_step_lists_the_folder_it_can_read():
    def no_helper(req):
        raise AssertionError("the helper was asked although the folder can be read")
    assert getting_started._backup_made(no_helper) is False
    (backup.backups_dir() / "2026-11-21.tar.gz").write_bytes(b"x")
    assert getting_started._backup_made(no_helper) is True


# ---------- the nightly backup fails closed ----------

def test_the_nightly_encrypt_fails_closed_when_the_password_file_cannot_be_read(tmp_path, monkeypatch):
    src, dst = tmp_path / "a.tar.gz", tmp_path / "a.tar.gz.enc"
    src.write_bytes(b"archive")
    assert backup.nightly_encrypt(src, dst) == 2                          # no password: plain, as before
    backup.write_password("correct horse battery")
    real = Path.read_text

    def unreadable(self, *a, **k):
        if self == backup.password_path():
            raise PermissionError(13, "Permission denied")
        return real(self, *a, **k)
    monkeypatch.setattr(Path, "read_text", unreadable)
    assert backup.nightly_encrypt(src, dst) == 1 and not dst.exists()   # never "no password" (2): no plain archive
    monkeypatch.setattr(Path, "read_text", real)
    assert backup.nightly_encrypt(src, dst) == 0 and dst.read_bytes() != b"archive"


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
