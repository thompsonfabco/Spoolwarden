"""RC6 / R85 assumption 5: the demo's stops are complete. The Hub's kit app runs in demo with every way out of the
process recorded (sockets, name lookups, Spoolman's client, child processes) and the real state, config and backups
folders compared before and after each request; every route is walked and anything that reaches outside or writes
outside the demo's folder is a missing stop.

What is proved here and what is not (the hooks live in files other steps hold; see the RC6 handoff):
- STOPPED today: Spoolman (the client answers from the demo inventory: no socket, no write to the real one), the
  pretend printers (no connection, no certificate, no Access Code), the demo's own folder holds what the demo writes.
- MISSING today: `LEAKS` below lists, per route, what still gets out or lands outside the demo's folder in this tree. The
  test holds that list EXACTLY: a stop that lands makes its route fall out of the list, and the test then fails until
  the list is cut, so the list can only shrink toward empty. When it is empty the assumption is proved.
"""
from __future__ import annotations

import builtins
import io
import json
import os
import re
import socket
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from filament_kit import cli, config, demo, modules, pairing, setup_code, setup_helper
from filament_kit.api import create_app
from filament_kit.bridge import camera, family, mapping, registry
from filament_kit.demo import printers
from filament_kit.spoolman import Spoolman

ON = frozenset({"server", "printers", "klipper"})
CODE = "12345678"
HOSTNAME = "kit.local"
BASE = f"https://{HOSTNAME}:8710"
HOME = ("192.0.2.20", 50000)                  # on the Hub's own network, as the Hub page wants

PAIRING_ROUTES = ("/api/pair", "/api/pairing/redeem")     # pairing works in the demo (R85 4.3)
STREAMS = ("/intake/api/printers/{id}/events",)           # a server-sent stream never ends: not walked

# what still leaves the demo or lands outside its folder in this tree, with the step that adds the stop. Filled from
# the first runs on the test boxes.
LEAKS: dict[str, str] = {
    # reaching a printer or the network: RC9's page route / the Klipper code
    "connect 192.0.2.1": "POST /setup/api/printers/scan looks at the local network (a UDP scan of the page's own subnet): "
                         "bridge/klipper/discovery.py or the page route must use the pretend family's scan()",
    "connect api.github.com": "POST /setup/api/printers/pack downloads the Klipper add-on pack from GitHub "
                              "(klipper_cli.py): stop with demo.stop()",
    # the root helper (RC7: setup_helper.py answers demo.NOT_IN_DEMO to every op but `check`, the page's own unlock)
    **{f"root helper {op}": "setup_helper.py (RC7)" for op in (
        "apply", "backup_keep", "backup_last", "backup_list", "general", "journal", "key_remove", "key_set",
        "library_token_remove", "library_token_set", "local_intake_token_remove", "local_intake_token_set", "mode",
        "module", "passphrase_set", "reader_usb", "restore_forget", "setting", "station_pairing", "trust_add",
        "trust_list", "trust_revoke_all", "trust_status")},
    # what the Hub records, in the real state folder (each must use demo.where(...))
    "write state/alerts.xml": "alerts.py:137 (the alert switches)",
    **{f"write state/library{rest}": "library.py (RC1d): the Filament Catalog's copy" for rest in (
        "", "/current.json", "/versions", "/versions/2-20261101T000000Z")},
    "write state/printers": "bridge/mapping.py, bridge/manual.py, bridge/printer_report.py: the per-printer folder",
    "write state/printers/<id>": "same",
    "write state/printers/<id>/...": "same: map, manual-map offer, printer report",
    "write state/profiles": "profiles_api.py / profiles/: the presets' folder",
    "write state/profiles/rebuild-request": "same",
    "write state/profiles/restore-deleted": "same",
    "write state/web": "reader_tokens.py: the Tag Reader tokens (a device pairing: decide whether it counts as pairing)",
}

# what the calls with a real body leave outside the demo's folder (the walk's empty bodies stop before they get there)
LEAKS_WITH_BODY: dict[str, str] = {
    "write state/storage.xml": "storage.py: the storage locations (R85 4.3 names them): slot loads and unloads write them",
}

# Files a paired device legitimately changes: "Pairing a phone or a PC works" (R85 4.3), so the demo can be seen on them.
PAIRING = re.compile(r"(^|/)[^/]*(devices|pairing|token|trusted|lockout|audit|pending|spent)[^/]*$|\.lock$|\.tmp$|(^|/)logs?(/|$)", re.I)


@pytest.fixture(autouse=True)
def fk_env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt", "backups"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    monkeypatch.setenv("FK_NO_SYSTEMD", "1")
    monkeypatch.setenv("FK_SETUP_RUN", str(tmp_path / "run"))
    pairing.make_cert("fkhost", [], tmp_path / "etc")
    cfg = config.load()
    cfg["modules"] = {n: n in modules.MODES["server"] for n in modules.MODULES}
    cfg["web"] = {**cfg["web"], "lan": ["192.0.2.0/24"]}
    config.save(cfg)
    setup_code.write(CODE)
    monkeypatch.setattr(cli.shutil, "chown", lambda *a, **k: None)         # no filament-kit group on a test machine
    monkeypatch.setattr(setup_helper, "_start_apply", lambda checked=False: None)


class Helper:
    """The root helper as the server sees it (JSON in, JSON out, in this process); every op it is asked is recorded."""

    def __init__(self, seen):
        self.seen = seen

    def __call__(self, req):
        self.seen.append(f"root helper {req['op']}")
        return json.loads(json.dumps(setup_helper.handle(json.loads(json.dumps(req)))))

    def open_file(self, req):
        self.seen.append(f"root helper {req['op']}")
        r = setup_helper.handle(json.loads(json.dumps(req)))
        fd = r.pop("fd", None)
        return json.loads(json.dumps(r)), (os.fdopen(fd, "rb") if fd is not None else None)


def snapshot(root) -> dict:
    """Every file and folder under `root` outside the demo's folder and the pairing files: (kind, size, mtime)."""
    out = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if rel == "state/demo" or rel.startswith("state/demo/") or PAIRING.search(rel):
            continue
        st = p.stat()
        out[rel] = ("d", 0, 0) if p.is_dir() else ("f", st.st_size, st.st_mtime_ns)    # a folder's own time moves with any file
    return out


class Outside:
    """Records what a request does outside the process's own memory: every way out of the process, and every write
    under `root` (the test's etc, state, opt and backups folders) but the demo's folder and the pairing files."""

    def __init__(self, monkeypatch, root):
        self.seen: list[str] = []
        self.wrote: list[str] = []
        real_open, real_os_open, real_replace, real_mkdir = builtins.open, os.open, os.replace, os.mkdir

        def where(path):
            try:
                rel = Path(os.fsdecode(path)).resolve().relative_to(root.resolve()).as_posix()
            except (TypeError, ValueError, OSError):
                return None
            return None if rel.startswith("state/demo") or PAIRING.search(rel) else rel

        def writing(path, flags):
            rel = where(path)
            if rel and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND):
                self.wrote.append(rel)

        def f_open(file, mode="r", *a, **k):
            if isinstance(file, (str, bytes, os.PathLike)) and any(c in mode for c in "wax+"):
                rel = where(file)
                if rel:
                    self.wrote.append(rel)
            return real_open(file, mode, *a, **k)

        def f_os_open(path, flags, *a, **k):
            writing(path, flags)
            return real_os_open(path, flags, *a, **k)

        def f_replace(src, dst, *a, **k):
            rel = where(dst)
            if rel:
                self.wrote.append(rel)
            return real_replace(src, dst, *a, **k)

        def f_mkdir(path, *a, **k):
            rel = where(path)
            if rel and not os.path.exists(path):
                self.wrote.append(rel)
            return real_mkdir(path, *a, **k)
        monkeypatch.setattr(builtins, "open", f_open)
        monkeypatch.setattr(io, "open", f_open)
        monkeypatch.setattr(os, "open", f_os_open)
        monkeypatch.setattr(os, "replace", f_replace)
        monkeypatch.setattr(os, "mkdir", f_mkdir)

        def note(what, real=None):
            def wrapper(*a, **k):
                kind = kind_of(what, a)
                if real is not None and INSIDE.match(kind):      # the machine's own loopback (Windows' event loop uses it)
                    return real(*a, **k)
                self.seen.append(kind)
                raise OSError(f"the demo reached outside: {what}")
            return wrapper
        monkeypatch.setattr(socket.socket, "connect", note("connect", socket.socket.connect))
        monkeypatch.setattr(socket.socket, "connect_ex", note("connect", socket.socket.connect_ex))
        monkeypatch.setattr(socket, "create_connection", note("connect", socket.create_connection))
        monkeypatch.setattr(socket, "getaddrinfo", note("lookup"))
        monkeypatch.setattr(urllib.request, "urlopen", note("urlopen"))
        monkeypatch.setattr(subprocess, "Popen", note("process"))
        monkeypatch.setattr(subprocess, "run", note("process"))


def kind_of(what: str, args: tuple) -> str:
    """One word and its target for a way out: `connect 192.0.2.40`, `lookup example.com`, `urlopen host`, `process cmd`."""
    first = args[0] if args else ""
    if what == "urlopen":
        target = urllib.parse.urlsplit(getattr(first, "full_url", str(first))).hostname or "?"
    elif what == "process":
        target = str((first[0] if isinstance(first, (list, tuple)) and first else first) or "?").split()[0]
    elif what == "connect":
        addr = args[1] if isinstance(args[0], socket.socket) else args[0]
        target = str(addr[0] if isinstance(addr, tuple) and addr else addr)
        target = "the local network" if target in ("<broadcast>", "255.255.255.255") else target
    else:
        target = str(first)
    return f"{what} {target}"


# what is not outside: the Hub's own machine, and the page's own unlock (the setup code is checked by the helper)
INSIDE = re.compile(r"^(\w+ (127\.0\.0\.1|localhost|::1|workshop-hub(\.local)?|hostname)|root helper check)$")


def norm(rel: str, serial: str = "00M00A000000001") -> str:
    rel = re.sub(r"printer-report-.*", "printer-report-*", rel).replace(serial, "<id>")
    return re.sub(r"<id>/.+", "<id>/...", rel)


def sample_path(path: str) -> str:
    path = re.sub(r"\{(\w+):path\}", "x", path)
    names = {"id": "00M00A000000001", "label": "A1", "serial": "00M00A000000001", "entry_id": "x", "code": "en"}
    return re.sub(r"\{(\w+)\}", lambda m: names.get(m.group(1), "1"), path)


def routes_of(app):
    seen = set()
    for r in app.routes:
        for m in sorted(getattr(r, "methods", None) or ()):
            if m in ("HEAD", "OPTIONS"):
                continue
            if (m, r.path) not in seen:
                seen.add((m, r.path))
                yield m, r.path


@pytest.fixture
def hub(tmp_path, monkeypatch):
    """The kit app in demo: the demo's own registry (the hook registry.py needs, put in by the test), the pretend
    families, the real camera hub and mapper, every way out recorded. Returns (client, recorder, the two families)."""
    demo.start(spoolman=type("E", (), {"get": lambda self, p: []})(), registry_load=lambda: {})
    monkeypatch.setattr(registry, "load", printers.registry_printers)
    sm = Spoolman("http://127.0.0.1:1")
    bambu, klipper = printers.Family("bambu", sm), printers.Family("klipper", sm)
    camera_hub = camera.Hub(printer_state=family.Printers(bambu=bambu, klipper=klipper).state,
                            klipper_url=klipper.camera_url)
    out = Outside(monkeypatch, tmp_path)
    app = create_app(sm, printers=family.Printers(bambu=bambu, klipper=klipper), camera_hub=camera_hub,
                     mapper=mapping.Mapper(bambu), enabled_modules=ON,
                     setup={"helper": Helper(out.seen), "hosts": lambda: {HOSTNAME}})
    c = TestClient(app, base_url=BASE, client=HOME, raise_server_exceptions=False)
    c.headers.update({"Authorization": "Bearer " + pairing.redeem_code(pairing.issue().code), "Origin": BASE})
    return c, out, (bambu, klipper)


def test_the_demo_moves_through_the_api_and_stays_inside(hub, tmp_path):
    c, out, (bambu, klipper) = hub
    before = snapshot(tmp_path)
    got = {p["id"]: p for p in c.get("/api/printers").json()["printers"]}
    assert set(got) == {"00M00A000000001", "garage-voron", "03000A000000002", "mainsailos"}
    assert got["00M00A000000001"]["state"] == "connected" and got["03000A000000002"]["state"] == "offline"
    live = c.get("/api/printers/00M00A000000001").json()
    assert live["status"]["gcode_state"] == "RUNNING" and live["status"]["percent"] == 42
    assert c.post("/api/printers/00M00A000000001/command", json={"action": "pause"}).json()["ok"] is True
    assert c.get("/api/printers/00M00A000000001").json()["status"]["gcode_state"] == "PAUSE"
    assert c.post("/api/printers/00M00A000000001/command", json={"action": "resume"}).json()["ok"] is True
    r = c.post("/api/printers/garage-voron/slots/T1/unload", json={"dry_run": False})
    assert r.status_code == 200 and r.json()["spool"]["id"] == 171
    r = c.post("/api/printers/garage-voron/slots/T1/load", json={"spool_id": 152, "dry_run": False})
    assert r.status_code == 200 and r.json()["confirmed"] is True
    assert Spoolman("http://127.0.0.1:1").get("spool/152")["location"].startswith("Garage Voron")
    assert c.get("/api/spools/152").status_code == 200 and c.get("/api/info").status_code == 200
    assert c.get("/api/printers/00M00A000000001/usage", params={"days": 30}).status_code == 200
    assert [k for k in out.seen if not INSIDE.match(k)] == []          # nothing out ...
    after = snapshot(tmp_path)                                         # ... and what is written outside the demo's folder
    written = {"write " + norm(k) for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert written | {"write " + norm(k) for k in out.wrote} == set(LEAKS_WITH_BODY)


def test_every_route_in_the_demo(hub, tmp_path):
    c, out, _ = hub
    app = c.app
    leaks: dict[str, set] = {}
    walked = 0
    for method, path in routes_of(app):
        if path in PAIRING_ROUTES or path in STREAMS:
            continue
        url = sample_path(path)
        before, n, w = snapshot(tmp_path), len(out.seen), len(out.wrote)
        kw = {"json": {"code": CODE} if path.startswith("/setup/") else {}} if method in (
            "POST", "PUT", "PATCH", "DELETE") else {}
        try:
            c.request(method, url, **kw)
        except Exception as e:  # noqa: BLE001 - a route that breaks is a finding of its own
            leaks.setdefault(f"crash {type(e).__name__}", set()).add(f"{method} {path}")
            continue
        walked += 1
        found = {k for k in out.seen[n:] if not INSIDE.match(k)}
        after = snapshot(tmp_path)
        found |= {"write " + norm(k) for k in set(before) | set(after) if before.get(k) != after.get(k)}
        found |= {"write " + norm(k) for k in out.wrote[w:]}
        for k in found:
            leaks.setdefault(k, set()).add(f"{method} {path}")
    assert walked > 100                                           # the walk really covered the app
    report = "\n".join(f"    {k!r}: {len(v)} routes, e.g. {sorted(v)[0]}" for k, v in sorted(leaks.items()))
    assert set(leaks) == set(LEAKS), "\n" + report
