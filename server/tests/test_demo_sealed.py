"""RC6 / R85 assumption 5: the demo's stops are complete. The Hub's kit app runs in demo with every way out of the
process recorded (sockets, name lookups, Spoolman's client, child processes) and the real state, config and backups
folders compared before and after each request; every route is walked and anything that reaches outside or writes
outside the demo's folder is a missing stop.

What is proved here and what is not (the hooks live in files other steps hold; see the RC6 handoff):
- STOPPED today: Spoolman (the client answers from the demo inventory: no socket, no write to the real one), the
  pretend printers (no connection, no certificate, no Access Code), the demo's own folder holds what the demo writes.
- MISSING today: `LEAKS` below lists, per route, what still gets out or lands outside the demo's folder in this tree. The
  test holds that list EXACTLY: a stop that lands makes its route fall out of the list, and the test then fails until
  the list is cut, so the list can only shrink toward empty. It is empty since RC6c (the catalog, the registry, the
  per-printer and profiles folders follow the demo); a new leak fails the walk.
- Since the seal brief (RC6d): the walk sends REAL bodies (`BODIES`: backup, restore, job (update, backup ...), flash, label
  photos, the key check) and the printers are walked twice: the pretend families alone, and as `main()` wires them (a
  `Switch` over the real Bambu `Manager` and the real `KlipperFamily`, whose clients FAIL the test when made). Every fake
  that stands for the outside (Claude's client, the local reader, a printer's client, a push sender) records the call in
  `out.seen`, so a call that gets through is a leak of its own. What needs a stop in a file another builder holds is in
  `TEMPORARY_HELD_ROUTE_LEAKS`, held exactly like the lists above.
"""
from __future__ import annotations

import builtins
import contextlib
import email
import io
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.response
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

from filament_kit import __main__ as hub_main
from filament_kit import cli, config, csv_io, demo, event_sink, events, intake, local_reader, modules, pairing, setup_code, setup_helper, setup_page, status, web_access
from filament_kit.api import create_app
from filament_kit.bridge import camera, family, mapping, registry
from filament_kit.bridge.connection import Manager
from filament_kit.bridge.klipper.manager import KlipperFamily
from filament_kit.demo import printers
from filament_kit.spoolman import Spoolman

ON = frozenset({"server", "printers", "klipper", "local_intake"})
CODE = "12345678"
HOSTNAME = "kit.local"
BASE = f"https://{HOSTNAME}:8710"
HOME = ("192.0.2.20", 50000)                  # on the Hub's own network, as the Hub page wants

READER_URL = "http://192.0.2.77:8765"                     # the configured local reader: the Outside fake fails a connect to it
# routes the every-route walk does not send (a request that never ends, or a test that needs its own set-up), each with the
# test that walks it
WALKED_ELSEWHERE: dict[str, str] = {
    "POST /api/pair": "pairing works in the demo (R85 4.3): test_pairing_in_the_demo_touches_only_the_pairing_files",
    "DELETE /api/pair": "pairing works in the demo (R85 4.3): test_pairing_in_the_demo_touches_only_the_pairing_files",
    "POST /setup/api/pair": "pairing works in the demo (R85 4.3): test_pairing_in_the_demo_touches_only_the_pairing_files",
    "GET /intake/api/printers/{id}/events": "a server-sent stream never ends: test_the_event_streams_serve_the_demo_only",
}

# what still leaves the demo or lands outside its folder in this tree, with the step that adds the stop. Filled from
# the first runs on the test boxes.
LEAKS: dict[str, str] = {}

# what the per-printer page routes reach with a real body (the walk's empty bodies stop before they get there), for the
# two Klipper printers and then the Bambu one: `POST /setup/api/printers/{serial}/test` calls Moonraker's /printer/info
# (a Bambu printer: a TLS handshake with 192.0.2.40) in `setup_page.py printer_test` (RC9, Antigravity's file): it needs a
# 409 "Not in the demo." (`code=demo.NOT_IN_DEMO_CODE`) before `_moonraker_test` and before `tlspin.leaf_fp`. A route
# listed here is held by `test_firmware_download_and_the_per_printer_routes_with_bodies_stay_inside` and left out of the
# every-route walk; empty when the stop lands (the walk then covers it).
KLIPPER_ROUTE_LEAKS: dict[str, list[str]] = {}

# what the calls with a real body leave outside the demo's folder (the walk's empty bodies stop before they get there)
LEAKS_WITH_BODY: dict[str, str] = {}

# TEMPORARY, NAMED: the three leaks below wait for stops in files the Server builder holds (api.py printer_add and
# printer_test, setup_page.py key_check; the hunks are in the RC6d report). The Server builder applies them, empties this
# dict and then sets `demo.MOUNTABLE = True`; `test_the_demo_is_not_mountable_while_a_leak_list_is_not_empty` fails if the
# demo is mountable while this or any other leak list here is not empty.
# What still leaves the demo through a stop that belongs in a file another builder holds (api.py, cli.py, setup_page.py,
# web/intake_app.py; the hunks are in the RC6d report). Per route, what a request with a real body reaches or writes. Such a
# route is left out of the every-route walk and held by `test_routes_waiting_for_a_held_file_...`, exactly: when the stop
# lands the route's entry must go (the test then fails until it does), so the dict can only shrink toward empty.
ANSWERS_500 = "answers 500: the stop's NotInDemo is not mapped to the page's 409"
# the page's route wrapper (setup_page.py `route`, held) answers every exception but `Refused` with "internal error" (500), so
# a stop that fires below `printer_add`, `printer_code` and `printer_forget` (tlspin's, registry's) is no 409 "Not in the demo."
# yet: the hunk is in the DPROOF report (one `except demo.DemoError` clause in `route`); the three leave when it lands
TEMPORARY_HELD_ROUTE_LEAKS: dict[str, list[str]] = {
    "POST /setup/api/printers": [ANSWERS_500],
    "POST /setup/api/printers/{serial}/code": [ANSWERS_500],
    "POST /setup/api/printers/{serial}/forget": [ANSWERS_500],
}

# Command-line leaks found by the CLI walk (first run on testbox-linux, DPROOF-02): each needs a demo stop in cli.py (held) or
# the module it calls. Held exactly like the route list: the test fails when a leak is gone (the entry must go) or a new one
# appears, so the dict can only shrink toward empty. "write x" is a write outside the demo's folder, "never ends" a command that
# waits for a pairing in the demo (no outside reach was recorded while it waited).
TEMPORARY_HELD_CLI_LEAKS: dict[str, list[str]] = {
    "never ends": ["pair"],
    "write etc/config.json": ["features setup server", "local-intake remove --yes", "modules set server"],
    "write etc/config.xml": ["features setup server", "local-intake remove --yes", "modules set server"],
    "write state/alert-test": ["alerts test"],
}

JPEG = bytes([0xFF, 0xD8, 0xFF, 0xE0]) + bytes(64)                  # a photo as far as the Hub checks one
ZIP = b"PK" + bytes([3, 4]) + bytes(64)                             # an upload as far as the page checks one
BACKUP = "filament-kit-backup-kit-20260101-0000-v0.6.3.zip"
PASSWORD = "correct horse battery"
WRITES = ("POST", "PUT", "PATCH", "DELETE")


def j(**body) -> dict:
    """A page request with the setup code and `body`."""
    return {"json": {"code": CODE, **body}}


def photo(field: str) -> dict:
    return {"files": {field: ("label.jpg", JPEG, "image/jpeg")}, "data": {"hint": "a label"}}


# the requests a route takes that get past its first checks (what the empty body of the walk never reaches): backups,
# restore, jobs (update, backup, restore ...), flashing, label photos and the key check
BODIES: dict[str, list[dict]] = {
    "POST /setup/api/backups/download": [j(name=BACKUP)],
    "POST /setup/api/backups/keep": [j(days=7)],
    "POST /setup/api/backups/password": [j(password=PASSWORD)],
    "POST /setup/api/restore/password": [j(password=PASSWORD)],
    "POST /setup/api/restore/upload": [{"content": ZIP, "headers": {"X-Setup-Code": CODE}}],
    "POST /setup/api/job": [j(name=n, **({"confirm": "restore", "sha256": "0" * 64} if n == "restore" else {}))
                            for n in setup_helper.JOBS],
    "POST /setup/api/flash/provision": [j(name="reader", printer="")],
    "POST /setup/api/flash/server": [j(ssid="net", buzzer=True, port="ttyUSB0", name="reader", **{"pass": "secret-pass"})],
    "POST /setup/api/key": [j(key="sk-ant-api03-" + "x" * 30)],
    "POST /api/intake/read": [photo("photo")],
    "POST /api/intake/cap/refund": [{"json": {"ticket": "t"}}],
    "POST /intake/api/read": [photo("photos")],
    "POST /setup/api/settings/local-intake/test": [j(url=READER_URL, tier="auto"), j(url="https://192.0.2.77:8765", tier="gpu", trust=True)],
}
FIRMWARE = ("printer-station.bin", "intake-panel.bin")      # GET /setup/api/firmware/{device}
PRINTER_IDS = ("garage-voron", "mainsailos", "00M00A000000001")
# one body for every per-printer kit route: the union of what they read (a field a route does not read is ignored)
KIT_BODY = {"action": "pause", "spool_id": 152, "dry_run": False, "slot": "A1", "step": "x", "confirm": True,
            "host": "192.0.2.99", "port": 7125, "ip": "192.0.2.99", "access_code": "12345678", "api_key": "k",
            "serial": "00M00A000000002", "name": "Renamed", "mode": "on", "subnet": "192.0.2.0/24"}


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
    cfg["modules"]["ai_intake"] = True                     # a label read (and the key check) would reach Claude
    cfg["modules"]["local_intake"] = True                  # the local reader's health, Test and corrections would probe it:
    cfg["local_intake"] = {**cfg.get("local_intake", {}), "url": READER_URL, "tier": "auto"}   # an address no socket may reach
    (tmp_path / "etc" / "anthropic_key").write_text("sk-ant-api03-test\n", encoding="utf-8")
    cfg["web"] = {**cfg["web"], "lan": ["192.0.2.0/24"]}
    config.save(cfg)
    setup_code.write(CODE)
    monkeypatch.setattr(cli.shutil, "chown", lambda *a, **k: None)         # no filament-kit group on a test machine
    monkeypatch.setattr(setup_helper, "_start_apply", lambda checked=False: None)


class Helper:
    """The root helper as the server sees it (JSON in, JSON out, in this process); every op it is asked is recorded."""

    def __init__(self, seen):
        self.seen = seen

    def note(self, req, reply) -> None:
        """An op the helper answered "Not in the demo." (the stop) did nothing: only an op it carried out is outside."""
        if reply.get("code") != demo.NOT_IN_DEMO_CODE:
            self.seen.append(f"root helper {req['op']}")

    def __call__(self, req):
        reply = json.loads(json.dumps(setup_helper.handle(json.loads(json.dumps(req)))))
        self.note(req, reply)
        return reply

    def open_file(self, req):
        r = setup_helper.handle(json.loads(json.dumps(req)))
        self.note(req, r)
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
        self.last = None                   # the answer of the last `probe`
        self.paired: list[str] = []        # the pairing files written (a paired device is allowed: where they are is checked)
        real_open, real_os_open, real_replace, real_mkdir = builtins.open, os.open, os.replace, os.mkdir

        def where(path):
            try:
                rel = Path(os.fsdecode(path)).resolve().relative_to(root.resolve()).as_posix()
            except (TypeError, ValueError, OSError):
                return None
            if PAIRING.search(rel) and not rel.startswith("state/demo"):
                self.paired.append(rel)
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
        monkeypatch.setattr(socket.socket, "sendto", note("send", socket.socket.sendto))      # a datagram (mDNS, SSDP) leaves too
        monkeypatch.setattr(socket, "getaddrinfo", note("lookup"))
        monkeypatch.setattr(urllib.request, "urlopen", note("urlopen"))
        monkeypatch.setattr(subprocess, "Popen", note("process"))
        monkeypatch.setattr(subprocess, "run", note("process"))

    def reached(self, what: str):
        """A fake for something outside (Claude, the local reader, a printer's client, a push service): it records the
        call as a way out, so the test fails when the demo gets to it."""
        def fake(*a, **k):
            self.seen.append(f"cloud {what}")
            raise OSError(f"the demo reached outside: {what}")
        return fake


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
    elif what == "send":                      # sendto(data, address): the address is the last argument
        addr = args[-1] if args else ""
        target = str(addr[0] if isinstance(addr, tuple) and addr else addr)
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
    return re.sub(r"\{(\w+)(?::\w+)?\}", lambda m: names.get(m.group(1), "1"), path)


# what a path parameter takes in the walk (every value is walked; a parameter not listed takes "1", an `:int` one a number)
VALUES: dict[str, list[str]] = {
    "id": list(PRINTER_IDS), "serial": list(PRINTER_IDS), "label": ["A1", "T1"], "entry_id": ["x"], "entry": ["x"],
    "code": ["en"], "set": sorted(csv_io.EXPORT_SETS), "device": list(FIRMWARE), "hid": ["0123456789ab"],
    "read_id": ["0123456789ab"], "cid": ["7"], "request_id": ["x"], "uid": ["04AABBCCDD"], "line_id": ["1"],
    "name": [*sorted(csv_io.EXPORT_SETS), *(n + ".csv" for n in sorted(csv_io.EXPORT_SETS)), *FIRMWARE, "en", "x.js"],
}
SPOOLS = ["152", "171"]                         # spools of the demo's inventory: one in a drawer, one in a slot of a pretend printer
INTS = {"id": SPOOLS, "n": ["1"]}               # an integer parameter: a spool, the first photo


def urls_of(path: str) -> list[str]:
    """Every address a route is walked at: the k-th value of each parameter for k in 0 .. (the most values any has). A
    spool route's `id` is a spool of the demo; a printer route's is each of the pretend printers."""
    path = re.sub(r"\{(\w+):path\}", "x", path)
    found = re.findall(r"\{(\w+)(?::(\w+))?\}", path)

    def values(name, kind):
        if name == "id" and "/spools/" in path:
            return SPOOLS
        return INTS.get(name, ["1"]) if kind == "int" else (VALUES.get(name) or ["1"])
    most = max((len(values(n, k)) for n, k in found), default=1) or 1

    def pick(k):
        def sub(m):
            vals = values(m.group(1), m.group(2))
            return vals[min(k, len(vals) - 1)]
        return sub
    return [re.sub(r"\{(\w+)(?::(\w+))?\}", pick(k), path) for k in range(most)]


def routes_of(app):
    seen = set()
    for r in app.routes:
        for m in sorted(getattr(r, "methods", None) or ()):
            if m in ("HEAD", "OPTIONS"):
                continue
            if (m, r.path) not in seen:
                seen.add((m, r.path))
                yield m, r.path


class SteppedTime:
    """`time` as the setup page sees it, with `monotonic` moving 10 s at every call: the 3 s gap between two checks
    (`CHECK_GAP`) is never in the way, so a route behind it is reached by the walk whatever the speed of the machine."""

    def __init__(self):
        self._now = 1000.0

    def monotonic(self) -> float:
        self._now += 10.0
        return self._now

    def __getattr__(self, name):
        return getattr(time, name)


def probe(c, out, root, method: str, url: str, settle: float = 0.0, **kw) -> set[str]:
    """Send one request; what it reached outside, and every write outside the demo's folder (made by anything). `settle`:
    seconds to wait after the answer, so a thread the request left behind is counted for this request, not the next."""
    before, n, w = snapshot(root), len(out.seen), len(out.wrote)
    out.last = c.request(method, url, **kw)
    time.sleep(settle)
    found = {k for k in out.seen[n:] if not INSIDE.match(k)}
    after = snapshot(root)
    found |= {"write " + norm(k) for k in set(before) | set(after) if before.get(k) != after.get(k)}
    return found | {"write " + norm(k) for k in out.wrote[w:]}


# the whole body of a mutating route's fields: the union of what the routes read (a field a route does not read is ignored)
SINK = {**KIT_BODY, "password": PASSWORD, "key": "sk-ant-api03-" + "x" * 30, "url": READER_URL, "tier": "auto",
        "address": "192.0.2.99", "api_key": "k", "hint": "a label", "days": 7, "enabled": True, "id": "x", "rows": [],
        "kind": "spools", "text": "x", "q": "x", "lines": [], "devices": [], "token": "t", "ticket": "t", "uid": "04AABBCC",
        "weight": 100, "grams": 1, "location": "Shelf A", "color": "#112233", "material": "PLA"}
SINK_ON = {**SINK, "dry_run": False}
CSV_BYTES = bytes([110, 97, 109, 101, 10, 120, 10])          # `name`, a line, `x`, a line
WEB_AUTH = {"Authorization": ""}              # the phone web app's own token (the web role): the hub fixture sets it
BODIES.update({
    "PATCH /api/printers/{id}": [{"json": b} for b in ({"name": "Renamed"}, {"address": "192.0.2.99"}, {"access_code": "12345678"},
                                                       {"address": "192.0.2.99", "port": 7125}, {"api_key": "k"}, {"unpin": True})],
    "POST /api/spools/{id:int}/link": [{"json": {"tag": "04AABBCCDD", "dry_run": False}}],
    "PATCH /api/spools/{id:int}": [{"json": b} for b in ({"home": "Shelf A", "dry_run": False}, {"location": "Shelf A", "dry_run": False},
                                                         {"remaining": 400, "dry_run": False}, {"home": "Shelf A", "dry_run": True})],
    "POST /api/spools/{id}/return": [{"json": {}}, {"json": {"dry_run": False}}],
    "POST /intake/api/spools/{id:int}/return": [{"json": {}}],
    "POST /api/intake/add": [{"json": {"items": [{"name": "x", "material": "PLA", "color": "#112233"}], "dry_run": False, "tag": "04AABBCCDD"}}],
    "POST /api/station/save": [{"json": {"uid": "04AABBCCDD", "gross_g": 600, "return": True}}],
    "POST /api/station/return": [{"json": {"uid": "04AABBCCDD"}}],
    "POST /api/station/recognize": [photo("image")],
    "POST /api/events/web": [{"json": {"events": [{"kind": "spool.low", "subject": {"type": "spool", "id": "152"},
                                                    "data": {"spool": 152, "grams": 10}}]}, "headers": WEB_AUTH}],
    "POST /setup/api/library/add": [j(entry="{}")],
    "POST /setup/api/import/spools": [{"content": CSV_BYTES, "headers": {"X-Setup-Code": CODE}}],
    "POST /setup/api/import/locations": [{"content": CSV_BYTES, "headers": {"X-Setup-Code": CODE}}],
    **{f"POST /api/profiles/web/{n}": [{"json": SINK, "headers": WEB_AUTH}]
       for n in ("generate", "line_add", "line_new", "restore_deleted", "updates_accept")},
})
# extra query strings a read is walked with (the long-poll of the event feed, the activity range)
QUERIES: dict[str, list[str]] = {
    "GET /api/events": ["?since=0&wait=1", "?since_at=2020-01-01T00:00:00Z&kinds=spool.*&limit=5"],
    "GET /intake/api/events": ["?since=0&wait=1"],
    "GET /setup/api/events": ["?since=0&wait=1"],
}


def requests_for(method: str, path: str) -> list[tuple[str, dict]]:
    """What the walk sends to a route, at every address `urls_of` gives it: an empty body (the setup code under /setup/),
    the kitchen-sink body of every field a route reads (a mutating route), and every body the route takes that gets past
    its first checks (`BODIES`); a read also with its extra queries."""
    out = []
    for url in urls_of(path):
        page = path.startswith("/setup/")
        base = {"json": {"code": CODE} if page else {}} if method in WRITES else {}
        out.append((url, base))
        if method in WRITES:
            for body in (SINK, {**SINK, "family": "klipper"}, SINK_ON):
                out.append((url, {"json": {**body, "code": CODE} if page else body}))
        out += [(url + q, {}) for q in QUERIES.get(f"{method} {path}", [])]
        out += [(url, kw) for kw in BODIES.get(f"{method} {path}", [])]
    return out


def serve_kit(c, req):
    """What the kit server answers the phone web app's own call over the loopback (the web app is a part of the Hub and calls
    the Hub's API through `KitApi`): the same request, handed to the in-process app, as the response `urlopen` gives (an
    error status raises `HTTPError`, as it does there)."""
    r = c.request(req.get_method(), urllib.parse.urlsplit(req.full_url).path + (
        "?" + urllib.parse.urlsplit(req.full_url).query if urllib.parse.urlsplit(req.full_url).query else ""),
        content=req.data, headers={"Authorization": req.get_header("Authorization"),
                                   "Content-Type": req.get_header("Content-type") or "application/json", "Origin": BASE})
    heads = email.message_from_string("Content-Type: " + r.headers.get("content-type", "application/json") + "\n\n")
    if r.status_code >= 400:
        raise urllib.error.HTTPError(req.full_url, r.status_code, "answer", heads, io.BytesIO(r.content))
    return urllib.response.addinfourl(io.BytesIO(r.content), heads, req.full_url, r.status_code)


class FakeClaude:
    """The Anthropic client as the Hub holds it (`beta.messages.create` for a read, `models.list` for the key check):
    nothing in the demo may call it."""

    def __init__(self, out):
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=out.reached("claude messages")))
        self.models = SimpleNamespace(list=out.reached("claude models"))


@pytest.fixture(params=["pretend", "wired"])
def hub(request, tmp_path, monkeypatch, mountable):
    """The kit app in demo: the demo's own registry (the hook registry.py needs, put in by the test), the real camera
    hub and mapper, every way out recorded; the families are the pretend ones alone ("pretend") or wired as `main()`
    wires them ("wired": a `Switch` over the real Bambu `Manager` and the real `KlipperFamily`, started with the demo on,
    their clients failing the test when made). Returns (client, recorder, the two families)."""
    demo.start(spoolman=type("E", (), {"get": lambda self, p: []})(), registry_load=lambda: {})
    monkeypatch.setattr(setup_page, "time", SteppedTime())
    monkeypatch.setattr(registry, "load", printers.registry_printers)
    out = Outside(monkeypatch, tmp_path)
    claude = FakeClaude(out)
    for name in ("read", "revise"):                        # the local label reader: no read goes to it in the demo
        monkeypatch.setattr(local_reader, name, out.reached("local reader " + name))
    sm = Spoolman("http://127.0.0.1:1")
    bambu, klipper = printers.Family("bambu", sm), printers.Family("klipper", sm)
    wired = request.param == "wired"
    if wired:
        bambu = printers.Switch(Manager(spoolman=sm, client_factory=out.reached("bambu mqtt client"), port=1), bambu)
        klipper = printers.Switch(KlipperFamily(sm, mr_factory=out.reached("moonraker client"),
                                                browser_factory=hub_main._browser), klipper)
        bambu.start()
        klipper.start()
    camera_hub = camera.Hub(printer_state=family.Printers(bambu=bambu, klipper=klipper).state,
                            klipper_url=klipper.camera_url)
    app = create_app(sm, claude, printers=family.Printers(bambu=bambu, klipper=klipper), camera_hub=camera_hub,
                     mapper=mapping.Mapper(bambu), enabled_modules=ON | {"ai_intake"},
                     setup={"helper": Helper(out.seen), "hosts": lambda: {HOSTNAME}, "client_factory": lambda key: claude})
    c = TestClient(app, base_url=BASE, client=HOME, raise_server_exceptions=False)
    c.headers.update({"Authorization": "Bearer " + pairing.redeem_code(pairing.issue().code), "Origin": BASE})
    web = pairing.issue("web (MCP)", role=web_access.WEB_ROLE).token                  # the web role's own token (the web unit's)
    pairing.check(web)
    WEB_AUTH["Authorization"] = "Bearer " + web
    outside_urlopen = urllib.request.urlopen

    def urlopen(req, timeout=None, context=None, **kw):
        if urllib.parse.urlsplit(getattr(req, "full_url", str(req))).netloc == f"127.0.0.1:{config.load()['port']}":
            return serve_kit(c, req)                       # the Hub's own API, over its loopback
        return outside_urlopen(req, timeout=timeout, context=context, **kw)
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    yield c, out, (bambu, klipper)
    if wired:
        bambu.stop()
        klipper.stop()


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
    """The routes are read from the app (not typed here): every one is walked (at every address `urls_of` gives it, with
    an empty body, the kitchen-sink body and its `BODIES`) or is in `WALKED_ELSEWHERE` with the test that walks it."""
    c, out, _ = hub
    app = c.app
    leaks: dict[str, set] = {}
    codes: dict[str, set] = {}
    broken: dict[str, str] = {}
    walked, every = 0, set()
    for method, path in routes_of(app):
        every.add(f"{method} {path}")
        if f"{method} {path}" in WALKED_ELSEWHERE or f"{method} {path}" in {**KLIPPER_ROUTE_LEAKS, **TEMPORARY_HELD_ROUTE_LEAKS}:
            continue                                  # (the last two: held by the tests below, with their own lists)
        for url, kw in requests_for(method, path):
            try:
                found = probe(c, out, tmp_path, method, url, **kw)
            except Exception as e:  # noqa: BLE001 - a route that breaks is a finding of its own
                leaks.setdefault(f"crash {type(e).__name__}", set()).add(f"{method} {path}")
                continue
            walked += 1
            codes.setdefault(f"{method} {path}", set()).add(out.last.status_code)
            if out.last.status_code >= 500:                # a refusal is never an internal error
                broken.setdefault(f"{method} {path}", f"{out.last.status_code} {url} {out.last.text[:80]}")
            for k in found:
                leaks.setdefault(k, set()).add(f"{method} {path}")
    print("DPROOF-CODES", json.dumps({k: sorted(v) for k, v in codes.items()}))
    assert walked > 130                                           # the walk really covered the app, with its bodies
    assert broken == {}, broken
    report = "\n".join(f"    {k!r}: {len(v)} routes, e.g. {sorted(v)[0]}" for k, v in sorted(leaks.items()))
    assert set(leaks) == set(LEAKS), "\n" + report
    assert set(WALKED_ELSEWHERE) <= every, sorted(set(WALKED_ELSEWHERE) - every)           # a route that is gone is no reason
    assert all(len(why) > 20 for why in WALKED_ELSEWHERE.values())


def test_firmware_download_and_the_per_printer_routes_with_bodies_stay_inside(hub, tmp_path):
    """R587 M2 and M4, R609 N1: the firmware image is no download in the demo, and every per-printer page route for each
    pretend printer (both Klipper ones, then the Bambu one), with a body that gets past the checks and the check gap
    out of the way, reaches nothing and writes nothing outside the demo's folder."""
    c, out, _ = hub
    before, n, w = snapshot(tmp_path), len(out.seen), len(out.wrote)
    r = c.get("/setup/api/firmware/printer-station.bin")
    assert r.status_code == 409 and r.json()["code"] == "in_demo"
    body = {"code": CODE, "name": "Renamed", "host": "192.0.2.99", "port": 7125, "api_key": "k", "mode": "on",
            "serial": "x", "name_": "x"}
    walked, reached = 0, {"firmware": [x for x in out.seen[n:] if not INSIDE.match(x)]}
    for method, path in routes_of(c.app):
        if not path.startswith("/setup/api/printers/{serial}") or method == "GET":
            continue
        for pid in ("garage-voron", "mainsailos", "00M00A000000001"):
            k = len(out.seen)
            c.request(method, sample_path(path).replace("00M00A000000001", pid), json=body)
            walked += 1
            time.sleep(0.3)                      # a route that leaves a thread behind is caught at its own request
            reached.setdefault(f"{method} {path}", []).extend(x for x in out.seen[k:] if not INSIDE.match(x))
    assert walked >= 30
    assert {r: v for r, v in reached.items() if v} == KLIPPER_ROUTE_LEAKS      # held exactly, like LEAKS
    assert sorted(k for k in out.seen[n:] if not INSIDE.match(k)) == sorted(x for v in KLIPPER_ROUTE_LEAKS.values() for x in v)
    after = snapshot(tmp_path)
    written = {"write " + norm(k) for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert written | {"write " + norm(k) for k in out.wrote[w:]} == set()


def test_a_printer_edit_in_the_demo_answers_the_stop_not_an_internal_error(hub, tmp_path):
    """R609 N3: every registry writer stops in the demo, and the page's routes answer 409 "Not in the demo." for it."""
    c, out, _ = hub
    before = snapshot(tmp_path)
    body = {"code": CODE, "name": "Renamed", "address": "192.0.2.99", "host": "192.0.2.99", "port": 7125, "api_key": "k",
            "mode": "on", "access_code": "12345678"}
    for pid, what in (("00M00A000000001", "rename"), ("00M00A000000001", "address"), ("00M00A000000001", "unpin"),
                      ("garage-voron", "rename"), ("garage-voron", "address"), ("garage-voron", "api_key"),
                      ("garage-voron", "lane_data"), ("mainsailos", "connect")):
        sent = body if pid == "00M00A000000001" else {k: v for k, v in body.items() if k != "access_code"}   # Klipper: no code
        r = c.post(f"/setup/api/printers/{pid}/{what}", json=sent)
        assert (r.status_code, r.json()) == (409, {"error": demo.NOT_IN_DEMO, "code": demo.NOT_IN_DEMO_CODE}), (pid, what)
    assert snapshot(tmp_path) == before


def test_map_slots_stops_in_the_demo(hub, tmp_path):
    """R609 N2: Map slots has a stop (the slot test writes to a printer); nothing is written outside the demo's folder."""
    c, out, (bambu, _) = hub
    before = snapshot(tmp_path)
    assert bambu.command("00M00A000000001", "stop")["ok"]              # the print is over, so the printer is idle
    r = c.post("/setup/api/printers/00M00A000000001/map", json={"code": CODE})
    assert (r.status_code, r.json()) == (409, {"error": demo.NOT_IN_DEMO, "code": demo.NOT_IN_DEMO_CODE})
    time.sleep(0.3)                                                    # a job thread would be left behind by now
    assert snapshot(tmp_path) == before and [k for k in out.seen if not INSIDE.match(k)] == [] and out.wrote == []


def test_routes_waiting_for_a_held_file_leak_exactly_what_is_listed(hub, tmp_path):
    """The routes whose stop belongs in a file another builder holds (`TEMPORARY_HELD_ROUTE_LEAKS`), and every per-printer kit route
    for each pretend printer (the two Klipper ones, then the Bambu one) with a body that gets past the checks: what they
    reach or write outside the demo's folder is held exactly, so the dict can only shrink when the stop lands."""
    c, out, _ = hub
    found: dict[str, set[str]] = {}
    seen_by: list[str] = []                      # which request reached what (the message of a failure)
    for method, path in routes_of(c.app):
        key = f"{method} {path}"
        sends = requests_for(method, path) if key in TEMPORARY_HELD_ROUTE_LEAKS else []
        if path.startswith("/api/printers") and method in WRITES:
            sends += [(sample_path(path).replace("00M00A000000001", pid), {"json": body})
                      for pid in PRINTER_IDS for body in (KIT_BODY, {**KIT_BODY, "family": "klipper"})]
        for url, kw in sends:
            try:
                got = probe(c, out, tmp_path, method, url, settle=0.3, **kw)    # a thread left behind counts here
            except Exception as e:  # noqa: BLE001 - a route that breaks is a finding of its own
                got = {f"crash {type(e).__name__}"}
            if out.last.status_code >= 500 and key in TEMPORARY_HELD_ROUTE_LEAKS:
                got = got | {ANSWERS_500}
            if got:
                found.setdefault(key, set()).update(got)
            seen_by.append(f"{method} {url} {sorted(kw.get('json', {}))[:3]} -> {out.last.status_code} {out.last.text[:60]}: {sorted(got)}")
    assert {k: sorted(v) for k, v in found.items()} == {k: sorted(v) for k, v in TEMPORARY_HELD_ROUTE_LEAKS.items()}, "\n".join(seen_by)


def leak_lists() -> dict[str, object]:
    return {"LEAKS": LEAKS, "LEAKS_WITH_BODY": LEAKS_WITH_BODY, "KLIPPER_ROUTE_LEAKS": KLIPPER_ROUTE_LEAKS,
            "TEMPORARY_HELD_ROUTE_LEAKS": TEMPORARY_HELD_ROUTE_LEAKS, "TEMPORARY_HELD_CLI_LEAKS": TEMPORARY_HELD_CLI_LEAKS}


def not_mountable_yet(mountable: bool, lists: dict[str, object]) -> list[str]:
    """What stands in the way of a demo that is mountable: the names of the leak lists that are not empty."""
    return [name for name, v in lists.items() if v] if mountable else []


def test_the_demo_is_not_mountable_while_a_leak_list_is_not_empty():
    """The seal is not complete while any leak list here is not empty (R85 assumption 5): `demo.MOUNTABLE` (what RC9's
    mount consults) stays False until they are all empty, and whoever flips it with a list left fails this test."""
    assert not_mountable_yet(demo.MOUNTABLE, leak_lists()) == [], "empty the leak lists before demo.MOUNTABLE = True"


def test_the_mount_guard_fails_a_mountable_demo_with_a_list_left(monkeypatch):
    monkeypatch.setattr(demo, "MOUNTABLE", True)
    assert not_mountable_yet(demo.MOUNTABLE, {"A": {}, "B": {"POST /x": ["connect"]}}) == ["B"]       # flipped, one list left
    assert not_mountable_yet(demo.MOUNTABLE, {"A": {}, "B": []}) == []                                  # flipped, all empty
    assert not_mountable_yet(False, {"B": {"POST /x": ["connect"]}}) == []                             # not mountable: fine
    if any(leak_lists().values()):               # the real lists, as they stand: a flip now is caught
        with pytest.raises(AssertionError):
            assert not_mountable_yet(demo.MOUNTABLE, leak_lists()) == [], "flipped with a leak list left"


def test_start_refuses_while_the_demo_is_not_mountable(tmp_path):
    """`demo.MOUNTABLE` is False in the product: `start()` raises `NotMountable` and makes nothing, so wiring the demo into
    a command or a route before the leak lists are empty switches nothing on."""
    from filament_kit.demo import routes
    assert demo.MOUNTABLE is False
    before = snapshot(tmp_path)
    with pytest.raises(demo.NotMountable) as e:
        demo.start(spoolman=type("E", (), {"get": lambda self, p: []})(), registry_load=lambda: {})
    assert e.value.message == demo.NOT_MOUNTABLE and e.value.code == "not_mountable"
    assert not demo.is_on() and not demo.folder().exists() and snapshot(tmp_path) == before
    assert routes.post_demo({"on": True}) == (409, {"error": demo.NOT_MOUNTABLE, "code": "not_mountable"})
    assert not demo.is_on()


def test_manual_map_on_the_pretend_printers_is_a_refusal_never_a_500(hub, tmp_path):
    """The kit manual-map routes for a pretend Klipper printer (its family had no `manual`: AttributeError, 500) and a
    pretend Bambu one answer with a manual-map refusal or an answer, and nothing real is touched."""
    c, out, _ = hub
    before = snapshot(tmp_path)
    steps = [("GET", ""), ("POST", "/start"), ("POST", "/confirm"), ("POST", "/skip"), ("POST", "/stop"), ("POST", "/offer")]
    for pid in PRINTER_IDS + ("03000A000000002",):
        for method, tail in steps:
            r = c.request(method, f"/api/printers/{pid}/manual-map{tail}", json={} if method == "POST" else None)
            assert r.status_code < 500, f"{method} {pid}{tail} -> {r.status_code} {r.text[:80]}"
            if pid in ("garage-voron", "mainsailos"):       # a Klipper printer: the demo's stop, as a manual-map refusal
                assert r.status_code == 409 and r.json()["error"] == demo.NOT_IN_DEMO_CODE, (pid, tail, r.text)
    assert snapshot(tmp_path) == before and [k for k in out.seen if not INSIDE.match(k)] == [] and out.wrote == []


def test_the_local_reader_is_never_probed_in_the_demo(hub, tmp_path):
    """Settings > Label Reader's health and Test, the corrections waiting at the reader, and the status row all stop with
    the demo's sentence: nothing connects to the configured reader (a connect to it is a leak in `out.seen`), no
    certificate pin is kept (that is a write)."""
    c, out, _ = hub
    asks = [("GET", "/setup/api/settings/local-intake", {}),
            ("POST", "/setup/api/settings/local-intake/test", j(url=READER_URL, tier="auto")),
            ("POST", "/setup/api/settings/local-intake/test", j(url="https://192.0.2.77:8765", tier="auto", trust=True)),
            ("GET", "/setup/api/label-reader/corrections", {}),
            ("GET", "/setup/api/label-reader/corrections/package", {}),
            ("POST", "/setup/api/label-reader/corrections/remove-all", j()),
            ("POST", "/setup/api/label-reader/corrections/send", j()),
            ("POST", "/setup/api/label-reader/corrections/7/remove", j()),
            ("POST", "/setup/api/label-reader/corrections/7/retry", j()),
            ("GET", "/api/label-reader/corrections", {}),
            ("POST", "/api/label-reader/corrections/7/remove", {})]
    for method, url, kw in asks:
        assert probe(c, out, tmp_path, method, url, **kw) == set(), (method, url, out.last.status_code, out.last.text[:80])
        assert out.last.status_code == 409, (method, url, out.last.status_code, out.last.text[:80])
        body = out.last.json()
        assert demo.NOT_IN_DEMO in (body.get("error"), body.get("message")), (method, url, body)
    rows = list(status.local_reader_rows(config.load()))
    assert [r.text for r in rows if r.text == demo.NOT_IN_DEMO] == [demo.NOT_IN_DEMO] and not any("answering" in r.text for r in rows) and [k for k in out.seen if not INSIDE.match(k)] == []


def test_the_label_reader_stops_in_the_demo(hub, tmp_path):
    """R85 4.3, assumption 5: no read goes to Claude (or the local reader) in the demo, and nothing is counted, swept or
    kept under the real state folder; the phone web app's answer is the stop's sentence."""
    c, out, _ = hub
    sm = Spoolman("http://127.0.0.1:1")
    claude = FakeClaude(out)
    before = snapshot(tmp_path)
    r = c.post("/intake/api/read", files={"photos": ("label.jpg", JPEG, "image/jpeg")}, data={"hint": "a label"})
    assert r.status_code in (400, 409) and r.json()["error"] == demo.NOT_IN_DEMO, r.text
    cap = intake.ReadCap(config.paths().state / "reads.xml", 5)
    for what, call in {
        "read_labels": lambda: intake.read_labels(sm, [JPEG], "a label", None, claude),
        "retry": lambda: intake.retry("0123456789ab", sm, claude),
        "_call": lambda: intake._call(sm, [JPEG], "a label", None, claude),
        "_call revise": lambda: intake._call(sm, [JPEG], "a note", None, claude, revise=({"name": "x"}, "a note")),
        "_cloud": lambda: intake._cloud(config.load(), [], [], [JPEG], "a label", None, "now", claude, None),
        "ReadCap.take": lambda: cap.take("device"),
        "ReadCap.refund": lambda: cap.refund("ticket", "device"),
    }.items():
        with pytest.raises(demo.NotInDemo):
            call()
    assert [k for k in out.seen if not INSIDE.match(k)] == [] and out.wrote == []
    assert snapshot(tmp_path) == before


def test_backups_jobs_and_flashing_answer_the_stop_before_anything_is_written(hub, tmp_path):
    """R85 4.3: a restore upload is not written, a pending one is not dropped, no job starts (update, backup, restore ...),
    no backup is listed, kept, downloaded or protected, no device is flashed; all answer 409 "Not in the demo."."""
    c, out, _ = hub
    before = snapshot(tmp_path)
    sent = [("POST", "/setup/api/restore/upload", {"content": ZIP, "headers": {"X-Setup-Code": CODE}}),
            ("POST", "/setup/api/restore/forget", j())]
    for method, path in (("POST", "/setup/api/backups/list"), ("POST", "/setup/api/backups/download"),
                         ("POST", "/setup/api/backups/keep"), ("POST", "/setup/api/backups/password"),
                         ("POST", "/setup/api/backups/password/remove"), ("POST", "/setup/api/restore/password"),
                         ("POST", "/setup/api/job"), ("POST", "/setup/api/flash/provision"),
                         ("POST", "/setup/api/flash/server")):
        sent += [(method, path, kw) for kw in BODIES.get(f"{method} {path}", [j()])]
    sent += [("GET", f"/setup/api/firmware/{name}", {}) for name in FIRMWARE]
    assert len(sent) >= len(setup_helper.JOBS) + 12
    for method, path, kw in sent:
        r = c.request(method, path, **kw)
        got = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        assert (r.status_code, got.get("error"), got.get("code")) == (409, demo.NOT_IN_DEMO, demo.NOT_IN_DEMO_CODE), (
            method, path, kw.get("json", {}).get("name"), r.status_code, r.text)
    assert snapshot(tmp_path) == before and out.wrote == []
    assert [k for k in out.seen if not INSIDE.match(k)] == []


def test_the_key_row_asks_no_cloud_in_the_demo(hub):
    """The Overview's live key check (`status`) makes no call to Anthropic in the demo."""
    _, out, _ = hub
    rows = list(status._key_lines(lambda key: FakeClaude(out), True))
    assert [(r.state, r.text) for r in rows] == [(status.INFO, demo.NOT_IN_DEMO)]
    assert [k for k in out.seen if not INSIDE.match(k)] == []


class WebKit:
    """The kit API as the web unit holds it; the push routes ask it nothing."""

    def get(self, path):
        return {}

    def post(self, path, body):
        return {}


def test_push_stops_in_the_demo(tmp_path, monkeypatch, mountable):
    """R85 4.3 "sends no push": in the web unit the push routes (key, subscribe, unsubscribe, the test alert) answer 409
    "Not in the demo.", the alert feed tells no phone, and the sender, the subscriptions file and the VAPID key are not
    touched; a subscription made before the demo is left as it was."""
    pytest.importorskip("mcp")
    from filament_kit.web import app as web_app, auth, push
    real_sender = push.webpush_sender                     # the real one, for the direct call below
    public, origin = "https://mcp.example.test/filament", "https://mcp.example.test"
    cfg = config.load()
    cfg["modules"]["web"] = True
    cfg["web"] = {**cfg["web"], "public_url": public, "push": True}
    config.save(cfg)
    (tmp_path / "etc" / "web_passphrase").write_text(auth.hash_passphrase(PASSWORD) + "\n", encoding="utf-8")
    sent: list = []

    def sender(info, payload, **kw):
        sent.append(info)                     # the push service: the test fails below when this is reached
        return 201
    monkeypatch.setattr(push, "available", lambda: True)
    monkeypatch.setattr(push, "webpush_sender", lambda subject: sender)
    monkeypatch.setattr(push.Feed, "start", lambda self: None)
    app = web_app.build_app(cfg, sm=Spoolman("http://127.0.0.1:9"), store=auth.Store(web_app.oauth_file()),
                            lockout=auth.Lockout(), kit_api=WebKit())
    web = TestClient(app, base_url=origin, client=HOME)
    base, head = "/intake/api/printers", {"Origin": origin}
    sub = {"subscription": {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "k", "auth": "a"}}}
    assert web.post(base + "/push/subscribe", json=sub, headers=head).status_code == 200      # a phone, before the demo
    demo.start(spoolman=type("E", (), {"get": lambda self, p: []})(), registry_load=lambda: {})
    out = Outside(monkeypatch, tmp_path)
    before = snapshot(tmp_path)
    stop = {"error": demo.NOT_IN_DEMO, "code": demo.NOT_IN_DEMO_CODE}
    for method, path, kw in (("GET", base + "/push/key", {}), ("POST", base + "/push/subscribe", {"json": sub}),
                             ("POST", base + "/push/unsubscribe", {"json": {"endpoint": sub["subscription"]["endpoint"]}}),
                             ("POST", base + "/push/test", {"json": {}}), ("POST", "/intake/api/alerts/test", {"json": {}})):
        found = probe(web, out, tmp_path, method, path, headers=head, **kw)
        assert (out.last.status_code, out.last.json()) == (409, stop), (path, out.last.text)
        assert found == set(), (path, found)
    store = push.Store(config.paths().web / "push.xml")
    feed = push.Feed(WebKit(), store, sender, "https://hub.example.test/")
    feed.send({"id": 1, "alert": True, "title": "Print paused", "text": "x", "severity": "warn",
               "condition": {"key": "print-paused:p1", "state": "raised"}})              # the feed tells no phone
    for call in (lambda: push.send_all(store, "t", "b", "tag", sender=sender),
                 lambda: real_sender("mailto:x@example.test")(sub["subscription"], "{}"),
                 lambda: push.vapid(),
                 lambda: store.add(push.check_subscription(sub["subscription"]))):
        with pytest.raises(demo.NotInDemo):
            call()
    assert sent == [] and out.wrote == [] and [k for k in out.seen if not INSIDE.match(k)] == []
    assert snapshot(tmp_path) == before


# ---- the event streams (R85 assumption 5, item 1) -------------------------------------------------------------------

def open_stream(c, path: str, take: int = 1, timeout: float = 20.0) -> tuple[int, list[bytes]]:
    """GET `path` from the app and read the first `take` chunks of a response that never ends, then the client goes away
    (`http.disconnect`) as a phone closing its stream does; (status, the chunks)."""
    import anyio

    async def run():
        chunks, status, gone, asked = [], [], anyio.Event(), []

        async def receive():
            if not asked:
                asked.append(1)
                return {"type": "http.request", "body": b"", "more_body": False}
            await gone.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            if message["type"] == "http.response.start":
                status.append(message["status"])
            elif message["type"] == "http.response.body" and message.get("body"):
                chunks.append(message["body"])
                if len(chunks) >= take:
                    gone.set()
        parts = urllib.parse.urlsplit(path)
        heads = {k.lower(): v for k, v in c.headers.items()} | {"host": f"{HOSTNAME}:8710"}
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "https",
                 "path": parts.path, "raw_path": parts.path.encode(), "query_string": parts.query.encode(), "root_path": "",
                 "headers": [(k.encode(), v.encode()) for k, v in heads.items()], "client": HOME, "server": (HOSTNAME, 8710)}
        with anyio.fail_after(timeout):
            await c.app(scope, receive, send)
        return status[0], chunks
    return anyio.run(run)


def test_the_event_streams_serve_the_demos_events_only(hub, tmp_path):
    """R85 4.3: the Hub's event feed (`GET /api/events`, with its long-poll, and the web twin) and the web app's live status
    stream of each printer serve the demo's own events and printers: an event the real log holds is not in them, the sink as
    `main()` sets it writes into the demo's log and not the real one, and nothing reaches outside or is written outside."""
    c, out, _ = hub
    real_root = config.paths().state / "events"
    events.EventStore(real_root).write("print.started", {"type": "printer", "id": "REAL01", "name": "Real printer"},
                                       {"type": "hub"}, {"job": "real-job"})              # the real log's own event
    real_files = {p.name: p.stat().st_size for p in real_root.rglob("*") if p.is_file()}
    before, n, w = snapshot(tmp_path), len(out.seen), len(out.wrote)
    sink_before = event_sink.sink()
    event_sink.set_sink(event_sink.StoreSink())                                            # as main() sets it
    try:
        event_sink.emit("print.started", {"type": "printer", "id": "garage-voron", "name": "Garage Voron"}, {"job": "demo-job"})
    finally:
        event_sink.set_sink(sink_before)
    assert any("demo-job" in json.dumps(e) for e in events.EventStore(demo.folder() / "events").read(0)["events"])
    for url in ("/api/events?since=0&limit=500", "/api/events?since=0&wait=1", "/intake/api/events?since=0",
                "/setup/api/events?since=0&limit=500"):
        r = c.get(url)
        assert r.status_code == 200 and "demo-job" in r.text and "real-job" not in r.text, (url, r.status_code, r.text[:200])
    for pid in PRINTER_IDS + ("03000A000000002",):
        status_code, chunks = open_stream(c, f"/intake/api/printers/{pid}/events")
        assert status_code == 200 and chunks[0].startswith((b"data:", b": ping")), (pid, status_code, chunks[:1])
        assert b"REAL01" not in b"".join(chunks)
    assert {p.name: p.stat().st_size for p in real_root.rglob("*") if p.is_file()} == real_files     # the real log is as it was
    assert [e["data"].get("job") for e in events.EventStore(real_root).read(0)["events"]] == ["real-job"]
    assert [k for k in out.seen[n:] if not INSIDE.match(k)] == [] and out.wrote[w:] == []
    assert snapshot(tmp_path) == before


# ---- pairing (R85 assumption 5, item 2) -----------------------------------------------------------------------------

def test_pairing_in_the_demo_touches_only_the_pairing_files(hub, tmp_path):
    """R85 4.3: pairing a phone or a PC works in the demo, and it writes the pairing files of the real state and etc folders
    (a paired device stays paired when the demo is left) and nothing else: no file outside the demo's folder that is not a
    pairing file, no socket, no process beyond the local address lookup the pairing link needs."""
    c, out, _ = hub
    n, w = len(out.seen), len(out.wrote)
    out.paired.clear()
    token = pairing.redeem_code(pairing.issue().code)
    heads = {"Authorization": "Bearer " + token}
    asks = [("POST", "/api/pair", {"json": {"code": pairing.issue().code}}),
            ("POST", "/setup/api/pair", j()), ("POST", "/setup/api/pair", j(kind="slicer")),
            ("POST", "/api/station/pair-request", {"json": {"name": "Station", "fp": "ab" * 32}}),
            ("DELETE", "/api/pair", {"headers": heads})]
    for method, url, kw in asks:
        r = c.request(method, url, **kw)
        assert r.status_code < 500, (method, url, r.status_code, r.text[:100])
    assert c.post("/api/pair", json={"code": pairing.issue().code}).status_code == 200      # and a code works
    here = {f"process hostname", f"lookup {socket.gethostname()}"}                          # `hostname -I`: this machine's own address
    assert [k for k in out.seen[n:] if not INSIDE.match(k) and k not in here] == []
    assert out.wrote[w:] == []                                                              # nothing outside the pairing files
    assert out.paired, "pairing wrote no file: the test does not reach the pairing store"
    assert all(PAIRING_FILE.fullmatch(p) for p in out.paired), sorted({p for p in out.paired if not PAIRING_FILE.fullmatch(p)})


# the pairing files a pairing writes: directly in the real etc and state folders (a paired device is the Hub's, not the
# demo's), with the temporary names an atomic write goes through
PAIRING_FILE = re.compile(r"(etc|state)/(devices|pending|spent|setup_lockout|trusted|pairing)[\w.-]*")


# ---- a printer family started before the demo (R85 assumption 5, item 5) ---------------------------------------------

class FakeMqtt:
    """A paho client as the Bambu `Manager` drives it (it connects and publishes through this): every call is logged and a
    publish is kept, so a call made after the demo was switched on is seen and fails the test. It does what the real class's
    calls need and no more: no network, no callbacks of its own."""

    def __init__(self, log):
        self.log = log
        self.published: list[str] = []
        self.closed = False

    def tls_set_context(self, ctx):
        pass

    def reconnect_delay_set(self, min_delay=1, max_delay=120):
        pass

    def username_pw_set(self, user, password=None):
        pass

    def connect_async(self, host, port=1883, keepalive=60):
        self.log.append(f"connect {host}")

    def loop_start(self):
        self.log.append("loop_start")

    def loop_stop(self):
        self.log.append("loop_stop")

    def disconnect(self):
        self.closed = True
        self.log.append("disconnect")

    def subscribe(self, topic, qos=0):
        self.log.append("subscribe")

    def publish(self, topic, payload=None, qos=0):
        self.published.append(topic)
        self.log.append("publish")


def wait_for(check, seconds: float = 5.0, what: str = "the condition") -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return
        time.sleep(0.02)
    raise AssertionError(f"timed out waiting for {what}")


def test_a_bambu_manager_started_before_the_demo_goes_quiet_when_it_starts(tmp_path, monkeypatch, mountable):
    """R85 assumption 5: the real Bambu `Manager`, started and connected to a (fake) real printer BEFORE the demo is switched on
    (as `main()` wires it, behind a `Switch`), keeps no connection open and sends the printer nothing once `demo.start()`
    returned, and `demo.leave()` puts the connection back for what the real registry holds. The fake client records every
    call: a call after the switch fails the test; before it, the calls prove the test sees a printer being talked to."""
    from filament_kit.bridge import connection
    monkeypatch.setattr(connection, "QUIET_PUSHALL", 0)                  # a connected printer gets a pushall every round
    out = Outside(monkeypatch, tmp_path)
    sm = Spoolman("http://127.0.0.1:1")
    registry.add_bambu("00M00A000000009", "Real X1", "X1C", "192.0.2.50", "12345678")
    made: list[FakeMqtt] = []

    def factory(client_id):
        made.append(FakeMqtt([]))
        return made[-1]
    mgr = Manager(spoolman=sm, client_factory=factory, port=1)
    bambu = printers.Switch(mgr, printers.Family("bambu", sm))
    bambu.start(interval=0.05)
    try:
        wait_for(lambda: len(made) == 1, what="the first connection")
        mgr._conns["00M00A000000009"]._on_connect(made[0], None, {}, 0)           # the printer answers: connected
        wait_for(lambda: len(made[0].published) >= 3, what="the printer being talked to (the test's positive control)")
        registry.remove("00M00A000000009")                                       # it is removed: the Hub is empty again ...
        demo.start(spoolman=type("E", (), {"get": lambda self, p: []})())        # ... and the demo is switched on
        assert made[0].closed and mgr._conns == {}, made[0].log                   # at once, not at the next round
        sent = len(made[0].published)
        time.sleep(0.4)                                                           # eight rounds of the housekeeping
        assert len(made[0].published) == sent and len(made) == 1, (made[0].log[-6:], len(made))
        assert [k for k in out.seen if not INSIDE.match(k)] == []
        demo.leave()
        # the restore: a printer the real registry holds is connected again by the leave, at once
        registry.add_bambu("00M00A000000009", "Real X1", "X1C", "192.0.2.50", "12345678")
        mgr.sync()
        wait_for(lambda: len(made) == 2, what="the connection back")
        mgr._conns["00M00A000000009"]._on_connect(made[1], None, {}, 0)
        demo.start(spoolman=type("E", (), {"get": lambda self, p: []})(), registry_load=lambda: {})   # a Hub "empty" for the check
        assert made[1].closed
        sent = len(made[1].published)
        time.sleep(0.4)
        assert len(made[1].published) == sent and len(made) == 2
        demo.leave()
        wait_for(lambda: len(made) == 3, what="the connection back after leaving")
    finally:
        bambu.stop()
        if demo.is_on():
            demo.leave()


class FakeMoonraker:
    """A Moonraker client as the Klipper family drives it: every call is logged and answers `Offline`, so a connection
    attempt that is made is seen (and retried by the family)."""

    timeout = 0.1

    def __init__(self, log, host, port, api_key=""):
        self.log, self.host, self.port, self._key = log, host, port, api_key

    def __getattr__(self, name):
        from filament_kit.bridge.klipper.moonraker import Offline

        def call(*a, **k):
            self.log.append(f"{name}")
            raise Offline(f"fake Moonraker at {self.host}: down")
        return call


def test_a_klipper_family_started_before_the_demo_goes_quiet_when_it_starts(tmp_path, monkeypatch, mountable):
    """The same for the real `KlipperFamily`: a printer it is connecting to before the demo gets no further call after
    `demo.start()` returned, and `demo.leave()` connects it again."""
    out = Outside(monkeypatch, tmp_path)
    sm = Spoolman("http://127.0.0.1:1")
    registry.add_klipper("Real Voron", "192.0.2.60", 7125)
    p = next(p for p in registry.load().values() if p.family == "klipper")
    log: list[str] = []
    made: list[FakeMoonraker] = []

    def factory(host, port, api_key=""):
        made.append(FakeMoonraker(log, host, port, api_key))
        return made[-1]
    fam = printers.Switch(KlipperFamily(sm, mr_factory=factory, backoff=(0.05, 0.05), sync_every=3600), printers.Family("klipper", sm))
    fam.start()
    try:
        wait_for(lambda: len(log) >= 3, what="the printer being asked (the test's positive control)")
        registry.remove(p.serial)
        demo.start(spoolman=type("E", (), {"get": lambda self, p: []})())
        sent = len(log)
        time.sleep(0.4)                                                           # eight retry rounds
        assert len(log) == sent, log[-5:]
        demo.leave()
    finally:
        fam.stop()
        if demo.is_on():
            demo.leave()


# ---- the root helper, the apply and the update (R85 assumption 5, item 6) ---------------------------------------------

def test_the_root_helper_and_the_system_jobs_refuse_before_any_process(tmp_path, monkeypatch, mountable):
    """R85 4.3: with the real paths on (no FK_NO_SYSTEMD: systemctl, apt, the job runner), every op of the root helper but the
    page's own code check answers 409 "Not in the demo." before it starts anything; and the code that runs as root on its
    own (the apply, the update and its rollback, the backup and its timer, the job runner) refuses the same way. A process,
    a socket or a write outside the demo's folder fails the test."""
    from filament_kit import apply, backup, updates
    monkeypatch.delenv("FK_NO_SYSTEMD")
    demo.start(spoolman=type("E", (), {"get": lambda self, p: []})(), registry_load=lambda: {})
    out = Outside(monkeypatch, tmp_path)
    before, ran = snapshot(tmp_path), []

    def run(cmd, *a, **k):
        ran.append(cmd)
        raise OSError("the demo ran a command: " + " ".join(map(str, cmd)))
    for op in setup_helper.OPS:
        reply = setup_helper.handle({"op": op, "code": CODE, "client": HOME[0], "name": "update", "confirm": "restore"})
        if op == "check":
            continue                                          # the page's own unlock: the code is checked by the helper
        assert (reply.get("status"), reply.get("code")) == (409, demo.NOT_IN_DEMO_CODE), (op, reply)
    cfg = config.load()
    calls = {
        "apply.execute": lambda: apply.execute([apply.Action("systemctl", ("enable", "x"))] if hasattr(apply, "Action") else [], run=run),
        "apply.finish": lambda: apply.finish(cfg, run=run, pairing=False),
        "apply.refresh_station": lambda: apply.refresh_station(cfg, run=run),
        "updates.update": lambda: updates.update(cfg),
        "updates.rollback": lambda: updates.rollback(cfg),
        "updates.feed_now": lambda: updates.feed_now(cfg),
        "backup.create": lambda: backup.create(),
        "backup.pin_upload": lambda: backup.pin_upload(),
        "backup.restore": lambda: backup.restore(Path("x.zip"), yes=True, run=run, out=lambda *a: None, ask=lambda p: "yes"),
        "setup_helper.run_job": lambda: setup_helper.run_job("update", ["true"]),
    }
    reached = {}
    for what, call in calls.items():
        try:
            answer = call()
        except (demo.NotInDemo, backup.InDemo):
            continue
        except Exception as e:  # noqa: BLE001 - anything but the stop: the demo got further than it may
            reached[what] = f"{type(e).__name__}: {e}"[:100]
            continue
        if not (what == "backup.restore" and answer == 1):      # a restore answers the stop with the exit code 1
            reached[what] = f"returned {answer!r}"
    assert reached == {}, reached
    assert ran == [] and [k for k in out.seen if not INSIDE.match(k)] == [] and out.wrote == []
    assert snapshot(tmp_path) == before


# ---- the command line (R85 assumption 5, item 7) ----------------------------------------------------------------------

def cli_commands() -> list[tuple[list[str], bool]]:
    """Every command of the command line, read from the argparse tree `cli._main` builds (nothing is typed here): the words
    that reach each leaf, with a sample for what it needs (and whether the command takes `--yes`), and the group alone where
    the group is a command too."""
    import argparse
    from unittest import mock
    found = []

    def capture(self, args=None, namespace=None):
        found.append(self)
        raise SystemExit(0)
    with mock.patch.object(cli._Parser, "parse_args", capture):
        try:
            cli.main([])
        except SystemExit:
            pass
    root = found[0]

    def sample(action) -> str:
        if action.choices:
            return str(next(iter(action.choices)))
        return "1" if action.type in (int, float) else "x"

    def needs(parser) -> list[str]:
        out = []
        for a in parser._actions:
            if isinstance(a, (argparse._HelpAction, argparse._VersionAction, argparse._SubParsersAction)):
                continue
            if not a.option_strings and a.nargs in (None, 1, "+"):
                out.append(sample(a))
            elif a.option_strings and a.required:
                out += [a.option_strings[0], sample(a)]
        return out

    def walk(parser, words):
        subs = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        yes = any("--yes" in a.option_strings for a in parser._actions)
        if not subs:
            yield [*words, *needs(parser)], yes
            return
        for action in subs:
            if not action.required:
                yield [*words, *needs(parser)], yes
            done = set()
            for name, sub in action.choices.items():
                if id(sub) not in done:                         # an alias is the same command
                    done.add(id(sub))
                    yield from walk(sub, [*words, name])
    return list(walk(root, []))


CLI_SECONDS = 15.0       # a command that has not ended by then counts as one that never ends: a finding, not a pass


# what the walk of the command line does not run, with the reason it cannot leak (an interactive or never-ending command)
CLI_NOT_RUN: dict[str, str] = {
    "serve": "the Hub's own server loop (the unit's start command): it is the Hub, not a command that reaches out",
    "watch": "a never-ending loop over the Hub's own events",
    "events --follow": "a never-ending tail of the Hub's own events",
}


def test_every_command_of_the_command_line_stays_inside_in_the_demo(tmp_path, monkeypatch, mountable):
    """R85 4.3: every command the command line has (read from its argparse tree), alone and with `--yes` where it has one, run
    in the demo with Spoolman, Claude, the printers' clients, the sockets, the name lookups, the child processes and every
    write outside the demo's folder recorded and failing: nothing reaches outside."""
    demo.start(spoolman=type("E", (), {"get": lambda self, p: []})(), registry_load=lambda: {})
    monkeypatch.setattr(registry, "load", printers.registry_printers)
    out = Outside(monkeypatch, tmp_path)
    sm = Spoolman("http://127.0.0.1:1")
    monkeypatch.setattr(builtins, "input", lambda *a: (_ for _ in ()).throw(EOFError("the demo asked a question")))
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    commands = cli_commands()
    assert len(commands) > 60, commands
    leaks, results = {}, {}
    for argv, yes in commands:
        if " ".join(argv) in CLI_NOT_RUN or argv[:1] in (["serve"], ["watch"]):
            continue
        for extra in ([[]] + ([["--yes"]] if yes else [])):
            before, n, w = snapshot(tmp_path), len(out.seen), len(out.wrote)
            sink, buf, box = io.StringIO(), io.StringIO(), []
            print("DPROOF-RUN", *argv, *extra, file=sys.__stderr__, flush=True)

            def run_one():
                try:
                    with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(buf):
                        box.append(cli.main([*argv, *extra], client_factory=out.reached("claude"),
                                            manager_factory=out.reached("bambu manager"), sm_factory=lambda: sm))
                except SystemExit as e:
                    box.append(e.code)
                except BaseException as e:  # noqa: BLE001 - a command that breaks is a finding of its own
                    box.append(f"crash {type(e).__name__}")
            worker = threading.Thread(target=run_one, daemon=True, name="cli-walk")
            worker.start()
            worker.join(CLI_SECONDS)
            code = box[0] if box else "hung"
            results[" ".join([*argv, *extra])] = code
            if code == "hung":
                leaks.setdefault("never ends", set()).add(" ".join([*argv, *extra]))
            found = {k for k in out.seen[n:] if not INSIDE.match(k)}
            after = snapshot(tmp_path)
            found |= {"write " + norm(k) for k in set(before) | set(after) if before.get(k) != after.get(k)}
            found |= {"write " + norm(k) for k in out.wrote[w:]}
            for k in found:
                leaks.setdefault(k, set()).add(" ".join([*argv, *extra]))
    sys.stdout, sys.stderr = sys.__stdout__, sys.__stderr__       # a command that closed or swapped them must not hide the result
    print("DPROOF-CLI", json.dumps({k: str(v) for k, v in results.items()}), file=sys.__stderr__, flush=True)
    report = "\n".join(f"    {k!r}: {sorted(v)[:6]}" for k, v in sorted(leaks.items()))
    assert {k: sorted(v) for k, v in leaks.items()} == {k: sorted(v) for k, v in TEMPORARY_HELD_CLI_LEAKS.items()}, "\n" + report
