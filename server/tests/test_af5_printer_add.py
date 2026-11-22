"""AF5: `printer add` in the guided order (R82 section 5; 06 9.2): the found list, the details, the test with Add anyway,
and Map its slots now."""
from __future__ import annotations

import builtins

import pytest

from filament_kit import cli, config, output, setup_page
from filament_kit.bridge import registry
from filament_kit.bridge.klipper import discovery

X1 = "00M00A000000001"


@pytest.fixture(autouse=True)
def kit(tmp_path, monkeypatch):
    for name, folder in (("FK_ETC", "etc"), ("FK_STATE", "state")):
        monkeypatch.setenv(name, str(tmp_path / folder))
        (tmp_path / folder).mkdir(exist_ok=True)
    monkeypatch.setenv("FK_NO_SYSTEMD", "1")
    monkeypatch.setenv("COLUMNS", "100")
    monkeypatch.setattr(cli, "_is_root", lambda: True)
    monkeypatch.setattr(cli, "_server_api", lambda: None)
    config.save({**config.load(), "modules": {"printers": True, "klipper": True}})


def run(capsys, *argv):
    try:
        rc = cli.main(list(argv))
    except SystemExit as e:
        rc = e.code
    got = capsys.readouterr()
    return rc, got.out, got.err


def terminal(monkeypatch, at_terminal, *answers):
    left = list(answers)
    asked = []
    monkeypatch.setattr(output, "terminal", lambda: at_terminal)

    def fake_input(prompt=""):
        asked.append(prompt)
        if not left:
            raise EOFError
        return left.pop(0)

    def fake_ask(prompt):
        asked.append(prompt)
        return left.pop(0) if left else None
    monkeypatch.setattr(builtins, "input", fake_input)
    monkeypatch.setattr(cli, "_ask", fake_ask)
    monkeypatch.setattr(cli.secret_input, "ask", lambda prompt: asked.append(prompt) or "")
    return asked


def found(monkeypatch, *hosts):
    monkeypatch.setattr(discovery, "scan", lambda net, **kw: [(h, 7125) for h in hosts])


def klipper_test(monkeypatch, answers):
    seen = []
    monkeypatch.setattr(cli, "_klipper_test", lambda host, port, key: seen.append((host, port)) or answers)
    return seen


def klippers():
    return [p for p in registry.load().values() if p.family == "klipper" and p.configured]


def test_the_found_list_comes_first_and_a_pick_fills_the_address_and_port(capsys, monkeypatch):
    found(monkeypatch, "192.0.2.57")
    seen = klipper_test(monkeypatch, True)
    asked = terminal(monkeypatch, True, "1", "Garage Voron", "n")          # the number, the name, no to the map
    rc, out, err = run(capsys, "printer", "add", "--klipper")
    assert rc == 0, err
    assert asked[0] == "Use one of these? Number, or Enter to type an address: "
    assert "Found on your network:" in out and "  1  " in out and "192.0.2.57:7125" in out
    assert "Address: " not in asked and "Port [7125]: " not in asked        # the pick answered them
    assert seen == [("192.0.2.57", 7125)]
    assert [(p.host, p.port, registry.display_name(p)) for p in klippers()] == [("192.0.2.57", 7125, "Garage Voron")]


def test_enter_on_the_found_list_types_the_address(capsys, monkeypatch):
    found(monkeypatch, "192.0.2.57")
    klipper_test(monkeypatch, True)
    asked = terminal(monkeypatch, True, "", "192.0.2.99", "7126", "Box", "n")
    assert run(capsys, "printer", "add", "--klipper")[0] == 0
    assert asked[:3] == ["Use one of these? Number, or Enter to type an address: ", "Address: ", "Port [7125]: "]
    assert [(p.host, p.port) for p in klippers()] == [("192.0.2.99", 7126)]


def test_nothing_found_says_so_and_asks_for_the_address(capsys, monkeypatch):
    found(monkeypatch)
    klipper_test(monkeypatch, True)
    terminal(monkeypatch, True, "192.0.2.99", "", "Box", "n")
    rc, out, err = run(capsys, "printer", "add", "--klipper")
    assert rc == 0 and "None found. Type its address." in out


def test_flags_skip_the_found_list(capsys, monkeypatch):
    monkeypatch.setattr(discovery, "scan", lambda net, **kw: pytest.fail("a search was made"))
    klipper_test(monkeypatch, True)
    terminal(monkeypatch, True, "n")
    assert run(capsys, "printer", "add", "--klipper", "--address", "192.0.2.99", "--port", "7125", "--name", "Box")[0] == 0


def test_a_bambu_printer_cannot_be_listed_so_the_command_says_it_is_added_by_address(capsys, monkeypatch):
    monkeypatch.setattr(discovery, "scan", lambda net, **kw: pytest.fail("a search was made"))
    asked = terminal(monkeypatch, True, "192.0.2.40", "Workshop", "")
    monkeypatch.setattr(setup_page, "setup_serial", lambda ip, timeout=5: X1)
    monkeypatch.setattr(cli.secret_input, "ask", lambda prompt: "24681357")
    rc, out, err = run(capsys, "printer", "add")
    assert rc == 0, err
    assert out.splitlines()[0] == "Bambu Lab printers are added by address."
    assert asked[0].startswith("IP address or name")


def test_a_klipper_printer_that_does_not_answer_is_not_saved_without_a_terminal(capsys, monkeypatch):
    klipper_test(monkeypatch, False)
    terminal(monkeypatch, False)
    rc, out, err = run(capsys, "printer", "add", "--klipper", "--address", "192.0.2.50", "--port", "7125")
    assert rc == 1 and "The Hub could not reach a Klipper printer at 192.0.2.50:7125." in err
    assert klippers() == [] and "Added" not in out


def test_json_never_asks_add_anyway(capsys, monkeypatch):
    klipper_test(monkeypatch, False)
    terminal(monkeypatch, False)
    rc, out, err = run(capsys, "printer", "add", "--klipper", "--address", "192.0.2.50", "--json")
    assert rc == 1 and klippers() == []


def test_add_anyway_at_a_terminal_yes_saves_it(capsys, monkeypatch):
    klipper_test(monkeypatch, False)
    asked = terminal(monkeypatch, True, "y", "n")          # Add anyway? y; then the map question: no
    rc, out, err = run(capsys, "printer", "add", "--klipper", "--address", "192.0.2.50", "--port", "7125",
                       "--name", "Garage Voron")
    assert rc == 0, err
    assert "The Hub could not reach a Klipper printer at 192.0.2.50:7125." in err
    assert "Add anyway? [y/N] " in asked
    assert [registry.display_name(p) for p in klippers()] == ["Garage Voron"]


def test_add_anyway_no_saves_nothing(capsys, monkeypatch):
    klipper_test(monkeypatch, False)
    terminal(monkeypatch, True, "n")
    rc, out, err = run(capsys, "printer", "add", "--klipper", "--address", "192.0.2.50", "--port", "7125")
    assert rc == 1 and klippers() == [] and "Nothing was added." in out


def test_a_refused_module_is_the_hubs_refusal_before_any_test(capsys, monkeypatch):
    config.save({**config.load(), "modules": {"printers": True, "klipper": False}})
    monkeypatch.setattr(cli, "_klipper_test", lambda *a: pytest.fail("tested"))
    rc, out, err = run(capsys, "printer", "add", "--klipper", "--address", "127.0.0.1", "--port", "9")
    assert rc == 1 and "the klipper module is off on this server" in err


@pytest.mark.parametrize(("host", "port", "message"), [
    ("[broken:host", "7125", "give an IP address or a host name"),
    ("192.0.2.50", "0", "the port must be a number from 1 to 65535"),
    ("192.0.2.50", "65536", "the port must be a number from 1 to 65535"),
])
def test_invalid_klipper_details_are_refused_before_the_probe(capsys, monkeypatch, host, port, message):
    terminal(monkeypatch, False)
    monkeypatch.setattr(cli, "_klipper_test", lambda *a: pytest.fail("tested"))
    rc, _, err = run(capsys, "printer", "add", "--klipper", "--address", host, "--port", port)
    assert rc == output.USAGE and message in err
    assert klippers() == []


def test_an_invalid_port_answer_is_refused_before_asking_for_the_name(capsys, monkeypatch):
    asked = terminal(monkeypatch, True, "wrong")
    monkeypatch.setattr(cli, "_klipper_test", lambda *a: pytest.fail("tested"))
    rc, _, err = run(capsys, "printer", "add", "--klipper", "--address", "192.0.2.50")
    assert rc == output.USAGE and "the port must be a number from 1 to 65535" in err
    assert asked == ["Port [7125]: "] and klippers() == []


def test_a_bracketed_ipv6_address_is_probed_as_it_is_saved(capsys, monkeypatch):
    terminal(monkeypatch, False)
    seen = klipper_test(monkeypatch, True)
    rc, _, err = run(capsys, "printer", "add", "--klipper", "--address", "[::1]")
    assert rc == 0, err
    assert seen == [("::1", 7125)] == [(p.host, p.port) for p in klippers()]


def test_the_test_is_the_moonraker_info_call(monkeypatch):
    from filament_kit.bridge.klipper import moonraker
    monkeypatch.setattr(moonraker.Moonraker, "info", lambda self: {"state": "ready"})
    assert cli._klipper_test("192.0.2.50", 7125, "") is True

    def down(self):
        raise moonraker.Offline("down")
    monkeypatch.setattr(moonraker.Moonraker, "info", down)
    assert cli._klipper_test("192.0.2.50", 7125, "") is False


def test_map_its_slots_now_yes_runs_the_map_flow_of_the_new_printer(capsys, monkeypatch):
    klipper_test(monkeypatch, True)
    terminal(monkeypatch, True, "")                               # Enter: yes
    mapped = []
    monkeypatch.setattr(cli, "cmd_printer_map", lambda serial, mf, manual=False, yes=False: mapped.append((serial, manual)) or 0)
    rc, out, err = run(capsys, "printer", "add", "--klipper", "--address", "192.0.2.50", "--port", "7125", "--name", "Box")
    assert rc == 0 and mapped == [("box", True)]
    assert "Mapping tells the Hub which slot is which. Nothing heats or moves." in out


def test_map_its_slots_now_no_says_how_to_do_it_later(capsys, monkeypatch):
    klipper_test(monkeypatch, True)
    terminal(monkeypatch, True, "n")
    monkeypatch.setattr(cli, "cmd_printer_map", lambda *a, **k: pytest.fail("mapped"))
    rc, out, err = run(capsys, "printer", "add", "--klipper", "--address", "192.0.2.50", "--port", "7125", "--name", "Box")
    assert rc == 0 and 'Map them later: sudo spoolwarden printer map "Box"' in out


def test_without_a_terminal_or_under_json_nothing_is_asked_about_the_map(capsys, monkeypatch):
    klipper_test(monkeypatch, True)
    terminal(monkeypatch, False)
    monkeypatch.setattr(cli, "cmd_printer_map", lambda *a, **k: pytest.fail("mapped"))
    rc, out, err = run(capsys, "printer", "add", "--klipper", "--address", "192.0.2.50", "--port", "7125", "--name", "Box")
    assert rc == 0 and "Map" not in out, err
