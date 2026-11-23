"""`spoolwarden port`, the Getting started block of `status` and the addresses without a port."""
from __future__ import annotations

import json
import re
import socket

import pytest

from filament_kit import cli, config, firstrun, getting_started, hubport, output, pairing


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for k, d in (("ETC", "etc"), ("STATE", "state"), ("OPT", "opt"), ("BACKUPS", "bk")):
        monkeypatch.setenv(f"FK_{k}", str(tmp_path / d))
        (tmp_path / d).mkdir()
    monkeypatch.setenv("FK_NO_SYSTEMD", "1")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setattr(cli, "_is_root", lambda: True)
    monkeypatch.setattr(pairing, "local_ips", lambda: ["192.0.2.10"])
    return tmp_path


def run(capsys, argv):
    try:
        rc = cli.main(argv)
    except SystemExit as e:
        rc = e.code
    got = capsys.readouterr()
    return rc, got.out, got.err


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def set_config(**kw):
    cfg = config.load()
    cfg.update(kw)
    config.save(cfg)


# ---------- port ----------

def test_port_shows_the_port_and_an_address_without_it_for_443(capsys):
    set_config(port=443)
    rc, out, _ = run(capsys, ["port"])
    assert rc == 0 and "Hub port: 443" in out and "Address: https://192.0.2.10/setup" in out


def test_port_shows_the_port_in_the_address_for_another(capsys):
    set_config(port=8710)
    out = run(capsys, ["port"])[1]
    assert "Hub port: 8710" in out and "Address: https://192.0.2.10:8710/setup" in out


def test_port_json(capsys):
    set_config(port=443)
    rc, out, _ = run(capsys, ["port", "--json"])
    assert rc == 0 and json.loads(out) == {"port": 443, "address": "https://192.0.2.10/setup"}


def test_reading_needs_no_sudo(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_is_root", lambda: False)
    assert run(capsys, ["port"])[0] == 0


def test_a_change_needs_sudo(capsys, monkeypatch):
    monkeypatch.setattr(cli, "_is_root", lambda: False)
    rc, _, err = run(capsys, ["port", "8710", "--yes"])
    assert rc == output.NEEDS_SUDO and "sudo" in err


@pytest.mark.parametrize("text", ["80", "1023", "70000", "abc"])
def test_a_port_that_is_not_allowed_is_refused_with_the_reason_and_nothing_is_changed(capsys, monkeypatch, text):
    monkeypatch.setattr(hubport, "change_port", lambda *a, **k: pytest.fail("changed"))
    rc, _, err = run(capsys, ["port", text, "--yes"])
    assert rc == 2 and "Error:" in err and ("not allowed" in err or "not a port number" in err)


def test_a_port_that_is_taken_is_refused_with_the_fix(capsys, monkeypatch):
    monkeypatch.setattr(hubport, "change_port", lambda *a, **k: pytest.fail("changed"))
    s = socket.socket()
    s.bind(("0.0.0.0", 0))
    s.listen()
    try:
        port = s.getsockname()[1]
        if port < 1024:
            pytest.skip("low port")
        rc, _, err = run(capsys, ["port", str(port), "--yes"])
    finally:
        s.close()
    assert rc == 2 and f"Port {port} is already in use on this machine." in " ".join(err.split())
    assert "Fix: choose another port: sudo spoolwarden port 8710" in " ".join(err.split())


def test_the_change_asks_the_dialogs_words_and_no_changes_nothing(capsys, monkeypatch):
    port = free_port()
    asked = []
    monkeypatch.setattr(output, "confirm", lambda q, **k: asked.append(q) or output.CANCELLED)
    monkeypatch.setattr(hubport, "change_port", lambda *a, **k: pytest.fail("changed"))
    rc, _, _ = run(capsys, ["port", str(port)])
    assert rc == output.CANCELLED
    assert asked == [f"Move the Hub to port {port}? {hubport.TEXT_PAIRED_DEVICES}"]


def test_the_change_runs_and_says_the_new_address(capsys, monkeypatch):
    port, called = free_port(), []

    def change(text, **k):
        called.append(text)
        set_config(port=int(text))
        return []
    monkeypatch.setattr(hubport, "change_port", change)
    rc, out, _ = run(capsys, ["port", str(port), "--yes"])
    assert rc == 0 and called == [str(port)]
    assert f"The Hub is on port {port}: https://192.0.2.10:{port}/setup" in " ".join(out.split())


def test_the_change_to_443_says_an_address_without_a_port(capsys, monkeypatch):
    def change(text, **k):
        set_config(port=int(text))
    monkeypatch.setattr(hubport, "change_port", change)
    monkeypatch.setattr(hubport, "check_port", lambda text, **k: (443, ""))
    rc, out, _ = run(capsys, ["port", "443", "--json", "--yes"])
    assert rc == 0 and json.loads(out) == {"ok": True, "port": 443, "address": "https://192.0.2.10/setup"}


def test_a_refusal_of_the_change_itself_is_an_error_with_its_fix(capsys, monkeypatch):
    def change(text, **k):
        raise hubport.PortError(hubport.TEXT_REMOTE_ON_443, "choose another port, or switch Remote access off first")
    monkeypatch.setattr(hubport, "change_port", change)
    monkeypatch.setattr(hubport, "check_port", lambda text, **k: (443, ""))
    rc, _, err = run(capsys, ["port", "443", "--yes"])
    assert rc == 2 and "Remote access is on and uses port 443" in err and "Fix: choose another port" in err


def test_port_is_in_the_help_and_the_help_table(capsys):
    assert "port" in run(capsys, ["help"])[1]
    md = run(capsys, ["help", "--markdown"])[1]
    assert ("| `port [<number>]` | Show the Hub's port, or move the Hub to another | "
            "Settings > Network and addresses, Hub port |") in md


# ---------- the address without a port ----------

def test_the_addresses_of_the_commands_have_no_port_on_443():
    set_config(port=443)
    assert cli._intake_url() == "https://192.0.2.10/intake"
    set_config(port=8710)
    assert cli._intake_url() == "https://192.0.2.10:8710/intake"


# ---------- Getting started in status ----------

def hub_on():
    cfg = config.load()
    cfg["modules"] = {n: True for n in ("spoolman", "server", "printers")}
    config.save(cfg)


def status_out(capsys, *extra):
    return run(capsys, ["status", *extra])[1]


def test_a_new_hub_shows_the_block_with_the_next_step_and_its_command(capsys, monkeypatch):
    hub_on()
    firstrun.install_first(["name"])
    monkeypatch.setattr(getting_started, "_has_spools", lambda cfg: False)
    block = status_out(capsys).split("\nHub\n")[0]
    assert "Getting started: 1 of" in block
    assert re.search(r"done\s+Name this Hub", block) and re.search(r"next\s+Check storage locations", block)
    assert "sudo spoolwarden locations" in block
    assert re.search(r"not yet\s+Add a printer", block) and "sudo spoolwarden printer add" not in block
    assert "Moving from another computer" not in block          # a step is done


def test_no_step_done_shows_the_restore_line(capsys, monkeypatch):
    hub_on()
    firstrun.install_first()
    monkeypatch.setattr(getting_started, "_has_spools", lambda cfg: False)
    assert "Moving from another computer? Restore its backup: sudo spoolwarden restore <zip>" in status_out(capsys)


@pytest.mark.parametrize("how", ["updated", "hidden", "finished"])
def test_no_block_while_the_list_is_hidden_or_finished_or_the_hub_is_an_updated_one(capsys, monkeypatch, how):
    hub_on()
    monkeypatch.setattr(getting_started, "_has_spools", lambda cfg: False)
    if how != "updated":
        firstrun.install_first()
        firstrun.set_hidden(True) if how == "hidden" else firstrun.finish()
    assert "Getting started" not in status_out(capsys)


def test_no_block_with_quiet(capsys, monkeypatch):
    hub_on()
    firstrun.install_first()
    monkeypatch.setattr(getting_started, "_has_spools", lambda cfg: False)
    assert "Getting started" not in status_out(capsys, "--quiet")


def test_json_carries_the_contract_object(capsys, monkeypatch):
    hub_on()
    firstrun.install_first(["name", "storage", "features"])
    monkeypatch.setattr(getting_started, "_has_spools", lambda cfg: False)
    g = json.loads(status_out(capsys, "--json"))["getting_started"]
    assert set(g) == {"show", "hidden", "finished", "restore_offer", "done", "total", "steps"}
    assert g["show"] and not g["hidden"] and not g["restore_offer"] and g["done"] == 3
    assert [s["id"] for s in g["steps"]] == ["name", "storage", "features", "printer", "slots", "phone", "spool",
                                              "backup"]
    assert [s["state"] for s in g["steps"]][:5] == ["done", "done", "done", "next", "open"]
    assert all(set(s) == {"id", "state", "line", "target"} for s in g["steps"])


def test_the_steps_of_a_feature_that_is_off_are_not_shown(monkeypatch):
    monkeypatch.setattr(getting_started, "_has_spools", lambda cfg: True)
    cfg = config.load()
    cfg["modules"] = {n: True for n in ("spoolman", "server")}
    config.save(cfg)
    firstrun.install_first()
    assert [s["id"] for s in getting_started.view()["steps"]] == ["name", "storage", "features", "phone", "spool", "backup"]
    cfg["modules"].update(ai_intake=True, local_intake=True, web=True, presets=True, printers=True)
    config.save(cfg)
    assert [s["id"] for s in getting_started.view()["steps"]] == list(getting_started.STEP_IDS)


def test_a_view_never_raises_and_a_step_that_cannot_be_read_is_open(monkeypatch):
    firstrun.install_first()
    monkeypatch.setattr(getting_started, "_has_spools", lambda cfg: 1 / 0)
    steps = {s["id"]: s for s in getting_started.view()["steps"]}
    assert steps["spool"]["state"] in ("open", "next")


# ---------- restore hides the list (carry 1) ----------

def test_a_restore_that_worked_hides_getting_started(capsys, monkeypatch, tmp_path):
    from filament_kit import backup
    firstrun.install_first()
    z = tmp_path / "b.zip"
    z.write_bytes(b"x")
    monkeypatch.setattr(backup, "restore", lambda *a, **k: 0)
    rc, _, _ = run(capsys, ["restore", str(z), "--yes"])
    assert rc == 0 and firstrun.load().hidden and firstrun.load().present


def test_a_restore_from_a_backup_without_the_file_makes_a_hidden_one(capsys, monkeypatch, tmp_path):
    from filament_kit import backup
    z = tmp_path / "b.zip"
    z.write_bytes(b"x")
    monkeypatch.setattr(backup, "restore", lambda *a, **k: 0)
    assert not firstrun.load().present
    run(capsys, ["restore", str(z), "--yes"])
    assert firstrun.load().present and firstrun.load().hidden and not firstrun.load().shows


def test_a_restore_that_failed_or_was_declined_changes_nothing(capsys, monkeypatch, tmp_path):
    from filament_kit import backup
    firstrun.install_first()
    z = tmp_path / "b.zip"
    z.write_bytes(b"x")
    for code in (1, output.CANCELLED):
        monkeypatch.setattr(backup, "restore", lambda *a, _c=code, **k: _c)
        assert run(capsys, ["restore", str(z), "--yes"])[0] == code
        assert firstrun.load().shows


# ---------- Label Reader: the Next line and the Checks row ----------

READER_CMD = "sudo spoolwarden local-intake install"


def test_switching_the_local_reader_on_ends_with_next_and_the_install_command(capsys, monkeypatch):
    from filament_kit import apply, local_intake_install
    hub_on()
    monkeypatch.setattr(local_intake_install, "installed", lambda: None)
    monkeypatch.setattr(cli, "_save_and_apply", lambda cfg, notes=(): config.save(cfg) or 0)
    rc, out, _ = run(capsys, ["features", "on", "label-reader-local", "--yes"])
    assert rc == 0, out
    lines = out.strip().splitlines()
    assert lines[-1] == f"Next: {READER_CMD}" and "Features applied" in lines[-2]


def test_no_next_line_when_the_reader_is_installed_or_was_already_on(capsys, monkeypatch):
    from filament_kit import local_intake_install
    hub_on()
    monkeypatch.setattr(cli, "_save_and_apply", lambda cfg, notes=(): config.save(cfg) or 0)
    monkeypatch.setattr(local_intake_install, "installed", lambda: {"version": "1.0.0", "tier": "cpu"})
    assert "Next:" not in run(capsys, ["features", "on", "label-reader-local", "--yes"])[1]


def test_the_checks_row_says_switched_on_but_not_installed_with_the_same_command(monkeypatch):
    from filament_kit import local_intake_install, status
    cfg = config.load()
    cfg["modules"] = {n: True for n in ("spoolman", "server", "local_intake")}
    monkeypatch.setattr(local_intake_install, "installed", lambda: None)
    monkeypatch.setattr("filament_kit.local_reader.health", lambda c: {"ok": False, "url": "u", "error": "down"})
    rows = list(status.local_reader_rows(cfg))
    row = next(r for r in rows if r.key == "label-reader.not-installed")
    assert (row.state, row.group, row.text, row.fix) == (
        "fail", "Label Reader", "The Label Reader is switched on but not installed.", READER_CMD)
    assert not any(r.key == "label-reader.local" for r in rows)       # no second "not answering" row for the same cause


def test_no_not_installed_row_for_a_reader_on_another_machine_or_one_that_is_off(monkeypatch):
    from filament_kit import local_intake_install, status
    monkeypatch.setattr(local_intake_install, "installed", lambda: None)
    cfg = config.load()
    cfg["modules"] = {n: True for n in ("spoolman", "server", "local_intake")}
    cfg["local_intake"] = {"url": "https://reader.example:7915"}
    monkeypatch.setattr("filament_kit.local_reader.health", lambda c: {"ok": False, "url": "u", "error": "down"})
    assert not any(r.key == "label-reader.not-installed" for r in status.local_reader_rows(cfg))
    cfg["modules"] = {n: True for n in ("spoolman", "server")}
    assert not getting_started.reader_install_pending(cfg)


def test_a_reader_that_answers_is_not_called_not_installed_even_when_nothing_was_installed_here(monkeypatch):
    from filament_kit import local_intake_install, status
    cfg = config.load()
    cfg["modules"] = {n: True for n in ("spoolman", "server", "local_intake")}
    monkeypatch.setattr(local_intake_install, "installed", lambda: None)
    monkeypatch.setattr("filament_kit.local_reader.health", lambda c: {
        "ok": True, "url": "u", "version": "1", "api_version": "1.0", "tiers": {}, "degraded": False, "last_read_at": None})
    assert not any(r.key == "label-reader.not-installed" for r in status.local_reader_rows(cfg))
