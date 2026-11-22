"""Review findings on plan steps A3 and A4: the Share corrections item of the Label Reader screen (its
sub-heading, the waiting count, the singular), the program-name constant in the texts, `sudo` in the catalog add fix.
The wrapper's cases are in test_cli_wrapper_routing.py, the screen's numbering in test_cli_menus.py."""
import contextlib
import re
from pathlib import Path

import pytest

from filament_kit import cli, config, modules, output, share_corrections as sc, spools_cli

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir(exist_ok=True)
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    config.save({"modules": {n: True for n in modules.MODES["server"]} | {"local_intake": True},
                 "server_name": "Workshop", "label_reader": {"share_corrections": True}})
    return tmp_path


class _Ui:
    def __init__(self):
        self.said = []

    def say(self, msg):
        self.said.append(msg)

    error = say

    def yesno(self, q):
        self.said.append(q)
        return True

    @contextlib.contextmanager
    def suspended(self):
        yield


def _menu():
    return cli._ConfigMenu(config.load(), _Ui())


def test_share_corrections_stands_under_its_own_sub_heading_and_adds_the_waiting_count(monkeypatch):
    """The design (config menu, Label Reader screen): `Share corrections` above Share corrections, after Recently added's Records kept; the
    row adds `12 waiting` when there are any."""
    monkeypatch.setattr(sc, "state", lambda cfg, h=None: {"count": 12})
    screen, targets = _menu().group_screen("reader")
    row = screen.rows[targets.index("share_corrections")]
    assert row.before == ("Share corrections",)
    assert targets[-2:] == ["keep", "share_corrections"]
    assert row.value.endswith("  12 waiting") and not row.value.endswith("   12 waiting")
    monkeypatch.setattr(sc, "state", lambda cfg, h=None: {"count": 0})
    screen, targets = _menu().group_screen("reader")
    assert "waiting" not in screen.rows[targets.index("share_corrections")].value


@pytest.mark.parametrize("n, said", [(1, "1 correction deleted."), (2, "2 corrections deleted."),
                                      (0, "0 corrections deleted.")])
def test_the_deleted_corrections_are_counted_in_the_singular_too(monkeypatch, capsys, n, said):
    monkeypatch.setattr(sc, "state", lambda cfg, h=None: {"count": n})
    monkeypatch.setattr(sc, "delete_waiting", lambda cfg: n)
    monkeypatch.setattr(sc, "apply", lambda cfg, run=None, say=print: None)
    menu = _menu()
    menu.stage(cli._ITEMS["share_corrections"], False)
    menu._delete_waiting = True
    menu.save()
    assert said in capsys.readouterr().out


def test_the_program_name_in_these_texts_is_the_constant_not_a_literal():
    text = (ROOT / "server" / "filament_kit" / "cli.py").read_text(encoding="utf-8")
    assert not re.findall(r'output\.fill\([^\n]*command="spoolwarden"', text)


def test_the_share_corrections_description_names_the_command(monkeypatch):
    monkeypatch.setattr(sc, "state", lambda cfg, h=None: {"count": 0})
    menu = _menu()
    menu.edit(cli._ITEMS["share_corrections"])
    assert any(f"sudo {spools_cli.COMMAND} label-reader corrections send" in s for s in menu.ui.said)


def test_the_catalog_add_ask_fixes_say_sudo(monkeypatch, capsys):
    """The design (config menu, Filament Catalog screen): `sudo spoolwarden catalog add --ask`."""
    assert cli.main(["catalog", "add"]) == output.USAGE
    assert "run: sudo spoolwarden catalog add --ask" in capsys.readouterr().err
