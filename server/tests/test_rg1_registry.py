"""RG1: the registry step. The spools commands of spools_cli.py are hooked into the command line (and the old `spools low`
and bare `spool` keep working), `weigh-station panel-wifi [--off]` stops the station, sets the panel up through panel_setup's
plain functions and starts the station again, the flash prints the notice, and the Weigh Station rows of `status` carry
the panel rows. The panel, systemctl and the Hub are fakes."""
from __future__ import annotations

import pytest

from filament_kit import alerts_cli, cli, config, output, spools_cli, status
from filament_kit.station import panel, panel_flash, panel_setup
from filament_kit.station import state as station_state


def run(capsys, argv):
    try:
        rc = cli.main(argv)
    except SystemExit as e:
        rc = e.code
    got = capsys.readouterr()
    return rc, got.out, got.err


# ---- (a) spools_cli hooked in ----

def test_the_help_table_lists_the_new_spool_commands(capsys):
    out = run(capsys, ["help", "--markdown"])[1]
    for text in ("`spools [<words>] [--sealed", "--checked-out]`", "`spool return <n>`", "`spool home <n> <location> [-y]`",
                 "`weigh-station panel-wifi [--off] [--address ADDRESS]`"):
        assert text in out, text


@pytest.mark.parametrize("argv", [["spools", "--checked-out"], ["spool", "return", "7"], ["spool", "home", "7", "Rack 1", "-y"]])
def test_the_new_commands_reach_spools_cli_with_the_hubs_api(argv, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(spools_cli, "run", lambda args, api: seen.append((args.cmd, api)) or 0)
    monkeypatch.setattr(cli, "_server_api", lambda: "the-api")
    assert run(capsys, argv)[0] == 0
    assert seen == [(argv[0], "the-api")]


def test_spools_checked_out_asks_the_filter_and_prints_the_count(monkeypatch, capsys):
    asked = []

    class Api:
        def get(self, path, *a, **kw):
            asked.append(path)
            return {"spools": [], "total": 0}
    monkeypatch.setattr(cli, "_server_api", lambda: Api())
    rc, out, _ = run(capsys, ["spools", "--checked-out"])
    assert rc == 0 and "No spool is checked out." in out and "filter=checked_out" in asked[0]


def test_the_old_spools_low_and_the_bare_spool_still_work(monkeypatch, capsys):
    monkeypatch.setattr(alerts_cli, "run_spools", lambda args, sm=None: print("the low list") or 0)
    assert run(capsys, ["spools", "low"])[:2] == (0, "the low list\n")
    rc, out, _ = run(capsys, ["spool"])
    assert rc == 0 and out.startswith("Spools are opened and tagged in the web app or app:")
    monkeypatch.setattr(spools_cli, "run", lambda args, api: print("the list", args.words, args.filter) or 0)
    assert run(capsys, ["spools", "--low"])[1] == "the list [] low\n"      # the flag is the new command's, the word is the old one's


# ---- (b) weigh-station panel-wifi ----

class Ser:
    panel_version = "14"

    def __init__(self):
        self.written, self.closed, self.timeout = [], False, None

    def write(self, data):
        self.written.append(data)

    def readline(self):
        return b"OK\n"

    def close(self):
        self.closed = True


@pytest.fixture
def bench(monkeypatch, tmp_path):
    monkeypatch.setattr(cli, "_is_root", lambda: True)
    monkeypatch.setattr(output, "terminal", lambda: True)
    monkeypatch.setattr(station_state, "panel_ttys", lambda: ["/dev/ttyACM0"])
    ser = Ser()
    monkeypatch.setattr(panel, "open_panel", lambda dev: ser)
    (tmp_path / "tls").mkdir()
    (tmp_path / "tls" / "key.pem").write_text("k")
    monkeypatch.setattr(station_state, "tls_dir", lambda: tmp_path / "tls")
    monkeypatch.setattr(station_state, "ensure_cert", lambda folder=None: (None, None, "ab" * 32))
    monkeypatch.setattr(cli.pairing, "local_ips", lambda: ["192.0.2.20"])
    calls, applied = [], []

    def systemctl(cmd, **kw):
        calls.append(cmd[1])
        return type("R", (), {"returncode": 0})()
    monkeypatch.setattr(cli, "_run_apply", lambda pairing=True: applied.append(pairing) or 0)
    return type("B", (), {"ser": ser, "calls": calls, "applied": applied, "run": staticmethod(systemctl)})


def test_panel_wifi_needs_root(bench, monkeypatch, capsys):
    monkeypatch.setattr(cli, "_is_root", lambda: False)
    assert cli.cmd_station_panel_wifi(run=bench.run) == 3 and bench.calls == []


def test_panel_wifi_needs_a_terminal_and_stops_nothing(bench, monkeypatch, capsys):
    monkeypatch.setattr(output, "terminal", lambda: False)
    assert cli.cmd_station_panel_wifi(run=bench.run) == 2 and bench.calls == []
    assert "terminal" in capsys.readouterr().err


def test_panel_wifi_stops_the_station_sets_up_the_panel_applies_and_starts_the_station(bench, monkeypatch, capsys):
    got = {}
    monkeypatch.setattr(panel_setup, "enable", lambda send, **kw: got.update(kw, reply=send("CFG X")) or {"panel_link": "wifi", "panel_port": 5004})
    rc = cli.cmd_station_panel_wifi(ask=lambda p: "Workshop net", secret=lambda p: "hunter2-wifi-pass", run=bench.run)
    out = capsys.readouterr()
    assert rc == 0 and bench.calls == ["is-active", "stop", "start"] and bench.applied == [False]
    assert got["ssid"] == "Workshop net" and got["password"] == "hunter2-wifi-pass" and got["version"] == "14"
    assert got["address"] == "192.0.2.20" and got["port"] == config.panel_port({}) and got["fingerprint"] == "ab" * 32
    assert got["reply"] == "OK\n" and bench.ser.written == [b"CFG X\n"] and bench.ser.timeout == 5 and bench.ser.closed
    assert "hunter2-wifi-pass" not in out.out + out.err


def test_a_refusal_is_printed_as_its_plain_sentence_and_the_station_is_started_again(bench, monkeypatch, capsys):
    def refuse(send, **kw):
        raise panel_setup.PanelRefused(panel_setup.TEXT_NOT_ACCEPTED)
    monkeypatch.setattr(panel_setup, "enable", refuse)
    rc = cli.cmd_station_panel_wifi(ask=lambda p: "net", secret=lambda p: "password1", run=bench.run)
    err = capsys.readouterr().err
    assert rc == 1 and "The panel did not accept the Wi-Fi settings." in err and "password1" not in err
    assert bench.calls == ["is-active", "stop", "start"] and bench.applied == [] and bench.ser.closed


def test_a_command_placeholder_in_a_sentence_gets_the_commands_name(bench, monkeypatch, capsys):
    def refuse(send, **kw):
        raise panel_setup.PanelSetupError(panel_setup.TEXT_FIRMWARE_OLD)
    monkeypatch.setattr(panel_setup, "enable", refuse)
    cli.cmd_station_panel_wifi(ask=lambda p: "net", secret=lambda p: "password1", run=bench.run)
    assert "sudo spoolwarden weigh-station flash-display" in capsys.readouterr().err


def test_panel_wifi_off_goes_back_to_usb_even_with_no_panel_there(bench, monkeypatch, capsys):
    monkeypatch.setattr(panel, "open_panel", lambda dev: None)
    seen = []
    monkeypatch.setattr(panel_setup, "disable", lambda send=None: seen.append(send) or {"panel_link": "usb", "panel_forgotten": False})
    assert cli.cmd_station_panel_wifi(off=True, run=bench.run) == 0
    assert seen == [None] and bench.applied == [False] and bench.calls == ["is-active", "stop", "start"]


def test_panel_wifi_is_parsed_with_its_options(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "cmd_station_panel_wifi", lambda **kw: seen.append(kw) or 0)
    cli.main(["weigh-station", "panel-wifi", "--off"])
    cli.main(["weigh-station", "panel-wifi", "--address", "station.local"])
    assert seen == [{"off": True, "address": None}, {"off": False, "address": "station.local"}]


# ---- the flash prints the notice ----

def test_the_flash_notice_is_printed_after_the_done_line(monkeypatch, capsys):
    ser = type("S", (), {"panel_version": "15", "close": lambda s: None})()
    monkeypatch.setattr(panel, "open_panel", lambda port: ser)
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    note = "Its Wi-Fi set-up is gone: sudo {command} weigh-station panel-wifi"
    monkeypatch.setattr(panel, "flash_panel_result", lambda port, version, have=None: panel_flash.Flash(True, True, note))
    assert cli._flash_and_verify(panel, "/dev/ttyACM0", "15") == 0
    out = capsys.readouterr().out
    assert out.index("The display answered.") < out.index("Its Wi-Fi set-up is gone: sudo spoolwarden weigh-station panel-wifi")


# ---- (c) status ----

def test_the_status_rows_of_the_station_carry_the_panel_rows(monkeypatch):
    class Client:
        def __init__(self, url, **kw):
            pass

        def state(self):
            return {"tag": {"nfc_id": "x"}, "scale": True, "panel": "wifi"}
    monkeypatch.setattr(cli, "StationClient", Client)
    monkeypatch.setattr(config, "load", lambda: {"station": {"url": "http://127.0.0.1:5003", "panel_link": "wifi"}})
    rows = list(status.station_rows("http://127.0.0.1:5003"))
    assert [(r.group, r.text) for r in rows if r.key == "devices.panel-link"] == [("Devices", "Panel: on Wi-Fi")]
    assert not [r for r in rows if r.key == "weigh-station.panel"]
