"""Plan CLI command mounting (printer slots/usage/watch, devices show/set, catalog local/add --ask,
Share corrections in config/status, non-sudo corrections list/package, and help --markdown table)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from filament_kit import cli, config, klipper_cli, modules, output, pairing, spools_cli, status as status_mod
from filament_kit.api import create_app
from filament_kit.bridge import ams, registry
from filament_kit.bridge.registry import Printer
from filament_kit.spoolman import Spoolman
from test_inventory import RULES, item


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir(exist_ok=True)
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    pairing.make_cert("fkhost", [], tmp_path / "etc")
    config.save({"modules": {n: True for n in modules.MODES["server"]},
                 "server_name": "Workshop", "spoolman_url": "http://127.0.0.1:7912",
                 "placement": {"box_count": 2}})
    return tmp_path


class _HubKitAdapter:
    """Thin adapter forwarding KitApi-style .get/.post/.delete calls to a Starlette TestClient."""

    def __init__(self, client: TestClient, token: str):
        self.client = client
        self.headers = {"Authorization": f"Bearer {token}", "X-Spoolwarden-Client": "web/0.7.0"}

    def _call(self, method: str, path: str, body=None):
        from filament_kit.web.slots import SlotError
        r = getattr(self.client, method.lower())(path, headers=self.headers, **({"json": body} if body is not None else {}))
        data = r.json()
        if r.status_code >= 400:
            raise SlotError(r.status_code, data.get("message") or data.get("error") or str(r.status_code), body=data)
        return data

    def get(self, path: str):
        return self._call("GET", path)

    def post(self, path: str, body=None):
        return self._call("POST", path, body)

    def delete(self, path: str):
        return self._call("DELETE", path)


# ---------- a: printer load / unload / clear / usage & printer show --watch ----------

def test_printer_slot_usage_and_watch_commands_are_mounted(monkeypatch, capsys):
    serial = "01P00A000000001"
    registry.upsert(Printer(serial, "Bench X1", "X1C", "10.0.0.5", "12345678", True))
    seen: list[tuple] = []

    class _FakeApi:
        def get(self, path):
            seen.append(("GET", path))
            if path.endswith("/usage"):
                return {"entries": []}
            return {"printer": {"id": serial, "name": "Bench X1", "state": "connected"}, "status": {"ams": []}}

    monkeypatch.setattr(cli, "_server_api", lambda: _FakeApi())
    monkeypatch.setattr(klipper_cli, "cmd_printer_slot",
                        lambda api, printer, action, slot, spool=None, yes=False, stop=False:
                        seen.append(("SLOT", printer["serial"], action, slot, spool, yes, stop)) or 0)

    assert cli.main(["printer", "load", "Bench X1", "A1", "42", "-y"]) == 0
    assert cli.main(["printer", "load", "Bench X1", "auto", "42", "-y"]) == 0
    assert cli.main(["printer", "load", "Bench X1", "auto", "--stop"]) == 0
    assert cli.main(["printer", "unload", "Bench X1", "A1", "-y"]) == 0
    assert cli.main(["printer", "clear", "Bench X1", "A1", "-y"]) == 0
    assert cli.main(["printer", "usage", "Bench X1"]) == 0
    capsys.readouterr()

    # Non-terminal --watch fails with exit 2 unless --json
    monkeypatch.setattr(output, "terminal", lambda: False)
    assert cli.main(["printer", "show", "Bench X1", "--watch"]) == output.USAGE
    _, err = capsys.readouterr()
    assert "--watch needs a terminal" in err

    assert cli.main(["printer", "show", "Bench X1", "--watch", "--json"]) == 0
    out, _ = capsys.readouterr()
    assert json.loads(out)["printer"]["id"] == serial
    assert ("SLOT", serial, "load", "A1", "42", True, False) in seen
    assert ("SLOT", serial, "load", "auto", "42", True, False) in seen
    assert ("SLOT", serial, "load", "auto", None, False, True) in seen
    assert ("SLOT", serial, "unload", "A1", None, True, False) in seen
    assert ("SLOT", serial, "clear", "A1", None, True, False) in seen
    assert ("GET", f"/api/printers/{serial}/usage") in seen


# ---------- b: devices show / set ----------

def test_devices_show_and_set_dispatch_through_device_tab(monkeypatch, capsys):
    tok = pairing.redeem_code(pairing.issue("My Pixel", role="phone").code)
    pairing.authorize(tok, client="android/0.7.1")
    dev_id = pairing.devices()[0]["id"]

    assert cli.main(["devices", "show", "My Pixel"]) == 0
    out, _ = capsys.readouterr()
    assert "My Pixel" in out and "Phone" in out and "0.7.1" in out

    # devices set dispatches through _page_call -> device_tab POST /device/{id}/settings
    called: list[tuple] = []
    monkeypatch.setattr(spools_cli, "cmd_devices_set",
                        lambda page, ident, setting, value, yes=False:
                        called.append((callable(page), ident, setting, value, yes)) or 0)
    assert cli.main(["devices", "set", dev_id, "volume", "40", "-y"]) == 0
    assert called == [(True, dev_id, "volume", "40", True)]


# ---------- c: catalog local, catalog local remove, catalog add --ask ----------

def test_catalog_local_remove_and_add_ask_are_mounted(spoolman_url, monkeypatch, capsys):
    sm = Spoolman(spoolman_url)
    sm.ensure_fields()
    app = create_app(sm=sm)
    tok = pairing.redeem_code(pairing.issue("cli").code)
    adapter = _HubKitAdapter(TestClient(app), tok)
    monkeypatch.setattr(cli, "_server_api", lambda: adapter)

    # Empty local catalog
    assert cli.main(["catalog", "local"]) == 0
    out, _ = capsys.readouterr()
    assert "No entries of your own yet." in out

    # catalog add with neither file nor --ask -> usage exit 2
    assert cli.main(["catalog", "add"]) == output.USAGE
    capsys.readouterr()

    # catalog add --ask creates a local entry via POST /api/library/local/form
    monkeypatch.setattr(output, "terminal", lambda: True)
    answers = iter(["Acme", "Proto PLA", "PLA", "n"])
    monkeypatch.setitem(spools_cli.cmd_catalog_add_ask.__kwdefaults__, "ask", lambda _p="": next(answers))
    assert cli.main(["catalog", "add", "--ask", "-y"]) == 0
    out, _ = capsys.readouterr()
    assert "Added." in out

    # catalog local lists it, catalog local remove removes it
    assert cli.main(["catalog", "local", "--json"]) == 0
    entries = json.loads(capsys.readouterr()[0])["entries"]
    assert len(entries) == 1
    eid = entries[0]["id"]

    assert cli.main(["catalog", "local", "remove", eid, "-y"]) == 0
    out, _ = capsys.readouterr()
    assert "Removed Acme Proto PLA." in out


# ---------- d: Share corrections in config/status & list/package without sudo ----------

def test_corrections_list_and_package_need_no_sudo_and_status_config_show_share_corrections(tmp_path, monkeypatch, capsys):
    from filament_kit import local_intake_install as lii, local_reader, reader_corrections
    cfg = config.load()
    cfg["modules"]["local_intake"] = True
    cfg.setdefault("label_reader", {})["share_corrections"] = True
    config.save(cfg)

    monkeypatch.setattr(cli, "_require_root", lambda: False)
    monkeypatch.setattr(lii, "installed", lambda: "/opt/local-reader")
    monkeypatch.setattr(local_reader, "health", lambda c=None: {
        "ok": True, "url": "http://127.0.0.1:7915", "version": "0.2.0", "api_version": "1",
        "tiers": {"cpu": {"usable": True}}, "degraded": False, "error": None, "last_read_at": None,
        "capabilities": {"feedback": True, "opt_in": True, "count": 2},
    })
    monkeypatch.setattr(reader_corrections, "waiting", lambda c: {
        "opt_in": True, "count": 2, "bytes": 1048576, "entries": [{"id": "c1", "created_at": "2026-11-22", "corrected": {"brand": "Acme"}}],
        "not_queued": [],
    })
    monkeypatch.setattr(reader_corrections, "package", lambda c: (b"PK\x05\x06" + b"\x00" * 18, "corr.zip"))

    assert cli.cmd_local_intake_corrections_list() == 0
    out, _ = capsys.readouterr()
    assert "Corrections waiting: 2" in out

    dest = tmp_path / "out.zip"
    assert cli.cmd_local_intake_corrections_package(str(dest)) == 0
    assert dest.is_file()
    capsys.readouterr()

    # status row for Share corrections
    rows = list(status_mod.local_reader_rows(cfg))
    sc_rows = [r for r in rows if r.key == "label-reader.share-corrections"]
    assert len(sc_rows) == 1 and "Share corrections: on, 2 waiting" in sc_rows[0].text

    # config menu summary & group_screen include Share corrections
    class _FakeUi:
        def __init__(self):
            self.said = []
        def say(self, msg):
            self.said.append(msg)
        def error(self, msg):
            self.said.append(msg)
        def yesno(self, q):
            self.said.append(q)
            return False

    menu = cli._ConfigMenu(cfg, _FakeUi())
    summary, _ = menu.summary("reader")
    assert "Share corrections: on, 2 waiting" in summary
    sc, targets = menu.group_screen("reader")
    assert "share_corrections" in targets
    menu.edit(cli._ITEMS["share_corrections"])
    assert any("Project maintainer" in s.lower() or "corrections waiting" in s.lower() for s in menu.ui.said)
    assert any("Delete the 2 corrections still waiting?" in s for s in menu.ui.said)


def test_share_corrections_uses_singular_for_one_waiting(monkeypatch, tmp_path, capsys):
    from filament_kit import share_corrections as sc
    monkeypatch.setenv("FK_CONFIG", str(tmp_path / "cfg"))
    cfg = config.load()
    cfg.setdefault("modules", {})["local_intake"] = True
    cfg.setdefault("label_reader", {})["share_corrections"] = True
    config.save(cfg)
    monkeypatch.setattr(sc, "state", lambda _cfg: {"saved": True, "actual": True, "reason": None, "differs": False, "count": 1})
    monkeypatch.setattr(sc, "apply", lambda _cfg, say=None: {"want": False, "changed": True})

    class _FakeUi:
        def __init__(self):
            self.said = []
        def say(self, msg):
            self.said.append(msg)
        def error(self, msg):
            self.said.append(msg)
        def yesno(self, q):
            self.said.append(q)
            return False
        def suspended(self):
            import contextlib
            return contextlib.nullcontext()

    menu = cli._ConfigMenu(cfg, _FakeUi())
    menu.edit(cli._ITEMS["share_corrections"])
    assert any("Delete the 1 correction still waiting?" in s for s in menu.ui.said)
    menu.save()
    out, _ = capsys.readouterr()
    assert "1 correction kept." in out


# ---------- e: _COMMAND_TABLE & help --markdown ----------

def test_help_markdown_includes_a3_and_previously_missing_commands(capsys):
    rc = cli.main(["help", "--markdown"])
    assert rc == 0
    md, _ = capsys.readouterr()
    for expected in (
        "`printer show <printer> [--watch]`",
        "`printer load <printer> <slot> <spool>`",
        "`printer unload <printer> <slot>`",
        "`printer clear <printer> <slot>`",
        "`printer usage <printer> [--days N]`",
        "`devices show <device>`",
        "`devices set <device> <setting> <value>`",
        "`catalog add --ask`",
        "`catalog local`",
        "`catalog local remove <id>`",
        "`activity [--spool N] [--who WHO] [--days N]`",
        "`undo [<event id>] [-y]`",
        "`usage [--by month] [--printer PRINTER]`",
        "`export <dataset> [--out FILE] [--days N] [--printer PRINTER]`",
        "`import spools\\|locations <file> [-y]`",
        "`report [--out FILE]`",
        "`status --history [--days 7\\|30]`",
        "`pair --home-assistant [--text] [--no-wait]`",
        "`local-intake status`",
        "`label-reader corrections [list]`",
        "`label-reader corrections package <file>`",
    ):
        assert expected in md
    for r in md.splitlines():
        if r.startswith("| `label-reader corrections [list]` |") or r.startswith("| `label-reader corrections package <file>` |"):
            assert "(root)" not in r


def test_every_parser_command_appears_in_help_command_table(capsys):
    import argparse
    import re
    assert cli.main(["help", "--markdown"]) == 0
    md, _ = capsys.readouterr()
    assert "| `backup password [--remove \\| --show]` |" in md and "| `certificate --renew` |" in md
    ap, _ = cli._build_parser()
    old_top = {"cert", "ensure-fields", "intake", "klipper", "library", "modules", "profiles", "reader", "station",
               "sync", "unpair"}
    aliases = {a for a, _ in cli._ALIASES} | old_top

    def leaves(p, prefix=()):
        subs = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]
        if not subs:
            yield prefix
            return
        if not subs[0].required and prefix:
            yield prefix
        for name, child in subs[0].choices.items():
            if name == "help" or child is None:
                continue
            if not subs[0].required and name in ("list", "history"):
                continue
            cmd = " ".join(prefix + (name,))
            if cmd in aliases:
                continue
            yield from leaves(child, prefix + (name,))

    def table_matches(s: str, tc: str) -> bool:
        words = s.split()
        norm = tc.replace("\\|", "|").replace("[", "").replace("]", "").split()
        if len(norm) < len(words):
            return False
        return all(w == n or w in n.split("|") for w, n in zip(words, norm))

    table_spans = [re.sub(r"\s+", " ", sp) for row in cli._COMMAND_TABLE for sp in re.findall(r"`([^`]+)`", row[0])]
    missing = []
    for lf in leaves(ap):
        s = " ".join(lf)
        if s in aliases or s.startswith("help"):
            continue
        if not any(table_matches(s, tc) for tc in table_spans):
            missing.append(s)
    assert missing == [], f"parser commands missing from _COMMAND_TABLE: {missing}"
    assert "certificate --renew" in table_spans
    assert any(tc.startswith("local-intake update") for tc in table_spans)


def test_local_intake_update_routes_to_cmd_local_intake_install(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "cmd_local_intake_install", lambda tier, version, yes, planned=False: seen.append((tier, version, yes, planned)) or 0)
    assert cli.main(["local-intake", "update", "--tier", "cpu", "--version", "1.2.3", "-y"]) == 0
    assert seen == [("cpu", "1.2.3", True, False)]
