"""Plan the mounted kit routes (slot clear, local library form, reader seen, spool/hub/header fields)
and the contract test keeping Android, web twins and CLI calls aligned with the Hub's route table."""
from __future__ import annotations

import re
from pathlib import Path

import pytest
from starlette.routing import Route
from starlette.testclient import TestClient

from filament_kit import __version__, config, firstrun, hub_view, inventory, modules, pairing, reader_tokens, storage
from filament_kit.api import create_app
from filament_kit.bridge import ams, loading, pending, registry, status as bridge_status
from filament_kit.bridge.registry import Printer
from filament_kit.setup_console import device_tab
from filament_kit.spoolman import Spoolman
from test_inventory import RULES, item

ROOT = Path(__file__).resolve().parents[2]
API_KT = ROOT / "android/app/src/main/java/com/thompsonfabco/filamentkit/net/Api.kt"
SERVER_FK = ROOT / "server" / "filament_kit"

# Routes intentionally not mounted yet (A4 labels / Wave H demo & languages).
DEFERRED_CLIENT_ROUTES = {
    ("POST", "/api/demo/leave"),
    ("GET", "/api/languages"),
    ("GET", "/api/strings/{code}"),
}


class _SlotFakePrinter:
    """Minimal real-shape printer stub for pending.clear_slot / pending.unload tests."""

    def __init__(self, serial: str = "01P00A000000001"):
        self.serial = serial
        self._state = "idle"
        self.cleared: list[tuple[str, bool]] = []

    def state(self, serial: str) -> str:
        return self._state

    def slot(self, serial: str, label: str) -> dict | None:
        return {"tray_type": "PLA", "tray_color": "000000FF", "spool_id": None}

    def clear_slot(self, serial: str, label: str, dry_run: bool = True) -> dict:
        self.cleared.append((label, dry_run))
        return {"printer_cleared": not dry_run, "tray_type": "", "tray_color": ""}

    def unload_slot(self, serial: str, label: str, dry_run: bool = True) -> dict:
        return {"printer_cleared": not dry_run, "moved_back": [], "spool": {"id": None, "location": None}}

    def wait_report(self, serial: str, predicate, timeout: float = 0) -> dict | None:
        return {}


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


@pytest.fixture
def sm(spoolman_url):
    s = Spoolman(spoolman_url)
    s.ensure_fields()
    return s


def _auth(name="Phone", role=None, scope=None) -> dict:
    return {"Authorization": "Bearer " + pairing.redeem_code(pairing.issue(name, role=role, scope=scope).code)}


def _seed_spool(sm: Spoolman, *, location: str = "", comment: str = "") -> int:
    sid = inventory.add_spools(sm, [item()], RULES, dry=False).spool_ids[0]
    sm.patch(f"spool/{sid}", {"location": location, "comment": comment})
    return sid


# ---------- a: slot clear & unload ending waiting load ----------

def test_slot_clear_and_unload_routes_cover_answer_refusal_viewer_and_web_token(sm, monkeypatch):
    serial = "01P00A000000001"
    registry.upsert(Printer(serial, "Bench X1", "X1C", "10.0.0.5", "12345678", True))
    ams.save_map(serial, ams.SlotMap(slots={"A1": {"ams_id": 0, "tray_id": 0, "global_id": 0}},
                                     calibrated=True, observe=False))
    sid = _seed_spool(sm, location=bridge_status.location("Bench X1", "A1"))
    printers = _SlotFakePrinter(serial)

    # Start a pending waiting load on A1 so both clear and unload can end it.
    store = pending.Store(start_thread=False)
    monkeypatch.setattr(pending, "STORE", store)
    store.add(printers, sm, serial, "A1", sid)

    app = create_app(sm=sm, printers=printers)
    c = TestClient(app, raise_server_exceptions=False)
    phone, viewer, web = _auth("Phone"), _auth("HA", role=pairing.VIEWER_ROLE), _auth("web", role=pairing.WEB_ROLE)

    # Viewer refused on clear & unload
    assert c.post(f"/api/printers/{serial}/slots/A1/clear", json={"dry_run": True}, headers=viewer).status_code == 403
    assert c.post(f"/api/printers/{serial}/slots/A1/unload", json={"dry_run": True}, headers=viewer).status_code == 403

    # Reserved 'auto' slot refused
    bad = c.post(f"/api/printers/{serial}/slots/auto/clear", json={"dry_run": True}, headers=phone)
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_slot"

    # Web token allowed on clear; dry_run defaults to True, then commit clears slot and ends waiting load
    r_dry = c.post(f"/api/printers/{serial}/slots/A1/clear", json={}, headers=web)
    assert r_dry.status_code == 200 and r_dry.json()["dry_run"] is True

    r_clear = c.post(f"/api/printers/{serial}/slots/A1/clear", json={"dry_run": False}, headers=web)
    assert r_clear.status_code == 200
    body = r_clear.json()
    assert body["slot"] == "A1" and body["waiting_ended"] is True and body["dry_run"] is False

    # Re-plant pending load and verify slot_unload uses pending.unload and returns waiting_ended=True
    sm.patch(f"spool/{sid}", {"location": bridge_status.location("Bench X1", "A1"), "comment": "return: Rack 1"})
    store.add(printers, sm, serial, "A1", sid)
    r_unload = c.post(f"/api/printers/{serial}/slots/A1/unload", json={"dry_run": False}, headers=phone)
    assert r_unload.status_code == 200 and r_unload.json()["waiting_ended"] is True


# ---------- b: GET /api/library/local, POST /api/library/local/form, DELETE /api/library/local/{id} ----------

def test_local_library_routes_list_create_from_form_delete_and_enforce_scopes(sm):
    app = create_app(sm=sm)
    c = TestClient(app, raise_server_exceptions=False)
    phone, viewer, web = _auth("Phone"), _auth("HA", role=pairing.VIEWER_ROLE), _auth("web", role=pairing.WEB_ROLE)

    # Viewer refused on all three
    assert c.get("/api/library/local", headers=viewer).status_code == 403
    assert c.post("/api/library/local/form", json={}, headers=viewer).status_code == 403
    assert c.delete("/api/library/local/missing", headers=viewer).status_code == 403

    # Empty list initially
    r_list = c.get("/api/library/local", headers=web)
    assert r_list.status_code == 200 and r_list.json()["entries"] == [] and isinstance(r_list.json()["materials"], list)

    # Bad form -> 400 invalid_entry with problems
    r_bad = c.post("/api/library/local/form", json={"brand": "", "name": "", "material": "PLA"}, headers=web)
    assert r_bad.status_code == 400 and r_bad.json()["error"] == "invalid_entry" and r_bad.json()["problems"]

    # Good form -> 201 {entry}
    form = {"brand": "Acme", "name": "Matte PLA", "material": "PLA"}
    r_add = c.post("/api/library/local/form", json=form, headers=web)
    assert r_add.status_code == 201
    entry = r_add.json()["entry"]
    assert entry["brand"] == "Acme" and entry["name"] == "Matte PLA" and entry["material"] == "PLA"

    # Phone can also list and remove
    assert len(c.get("/api/library/local", headers=phone).json()["entries"]) == 1
    r_del = c.delete(f"/api/library/local/{entry['id']}", headers=web)
    assert r_del.status_code == 200 and r_del.json() == {"removed": True}
    assert c.delete(f"/api/library/local/{entry['id']}", headers=web).status_code == 404


# ---------- c: POST /api/readers/web/seen ----------

def test_readers_web_seen_records_reader_and_rejects_non_web_tokens(sm):
    tid, _ = reader_tokens.TokenStore().issue("bench")
    app = create_app(sm=sm)
    c = TestClient(app, raise_server_exceptions=False)
    phone, viewer, web = _auth("Phone"), _auth("HA", role=pairing.VIEWER_ROLE), _auth("web", role=pairing.WEB_ROLE)

    payload = {"readers": [{"id": tid, "build": 1794551072, "seen_at": "2026-11-22T12:00:00+00:00"}]}
    assert c.post("/api/readers/web/seen", json=payload, headers=phone).status_code == 403
    assert c.post("/api/readers/web/seen", json=payload, headers=viewer).status_code == 403

    bad = c.post("/api/readers/web/seen", json={"readers": "nope"}, headers=web)
    assert bad.status_code == 400 and bad.json()["error"] == "bad_request"

    ok = c.post("/api/readers/web/seen", json=payload, headers=web)
    assert ok.status_code == 200 and ok.json() == {"ok": True}
    rows = reader_tokens.readers()
    assert rows[0]["build"] == 1794551072 and rows[0]["last_seen"] == "2026-11-22T12:00:00+00:00"


# ---------- d: auto_location, spoolman_url in GET /api/spools, getting_started in GET /api/hub ----------

def test_auto_location_spoolman_url_and_getting_started_counts(sm, monkeypatch):
    sid = _seed_spool(sm, location="", comment="")
    firstrun.set_hidden(False)
    from filament_kit import backup
    monkeypatch.setattr(backup, "listing", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not list backups")))

    app = create_app(sm=sm)
    c = TestClient(app, raise_server_exceptions=False)
    phone = _auth("Phone")

    r_move = c.patch(f"/api/spools/{sid}", json={"auto_location": True, "dry_run": False}, headers=phone)
    assert r_move.status_code == 200
    assert r_move.json()["view"]["location"] == "Rack 1" and r_move.json()["placed_by"]

    r_spools = c.get("/api/spools", headers={**phone, "Host": "hub.local:8710"})
    assert r_spools.status_code == 200 and r_spools.json()["spoolman_url"] == "http://hub.local:7912"

    r_hub = c.get("/api/hub", headers={**phone, "Host": "hub.local:8710"})
    assert r_hub.status_code == 200
    gs = r_hub.json()["getting_started"]
    assert gs["hidden"] is False and gs["total"] > 0 and gs["done"] < gs["total"]


# ---------- e: X-Spoolwarden-Client header recorded into pairing ----------

def test_client_header_is_recorded_in_pairing_and_shown_in_hub_and_devices(sm):
    app = create_app(sm=sm)
    c = TestClient(app, raise_server_exceptions=False)
    phone = _auth("My Pixel")
    pc = _auth("Shop PC")

    assert c.get("/api/hub", headers={**phone, "X-Spoolwarden-Client": "android/0.7.1"}).status_code == 200
    assert c.get("/api/spools", headers={**pc, "X-Spoolwarden-Client": f"slicer-sync/{__version__}"}).status_code == 200
    # Malformed header is ignored cleanly
    assert c.get("/api/hub", headers={**phone, "X-Spoolwarden-Client": "bad header with spaces"}).status_code == 200

    devs = {d["name"]: d for d in pairing.devices()}
    assert devs["My Pixel"]["kind"] == "phone" and devs["My Pixel"]["version"] == "0.7.1"
    assert devs["Shop PC"]["kind"] == "pc" and devs["Shop PC"]["version"] == __version__

    hub_devs = {d["name"]: d for d in c.get("/api/hub", headers=phone).json()["devices"]}
    assert hub_devs["My Pixel"]["kind"] == "phone" and hub_devs["My Pixel"]["version"] == "0.7.1"
    assert hub_devs["Shop PC"]["kind"] == "pc" and hub_devs["Shop PC"]["version"] == __version__

    tab_rows = {d["name"]: d for d in device_tab.rows()}
    assert tab_rows["My Pixel"]["kind"] == "phone" and tab_rows["My Pixel"]["version"] == "0.7.1"


# ---------- f: contract test across Android, web twins and CLI ----------

def _normalise_template(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "{x}", path)


def _android_calls() -> set[tuple[str, str]]:
    text = API_KT.read_text(encoding="utf-8")
    out: set[tuple[str, str]] = set()
    # Match `request("api", ...).<method>(` or `request("api", ...) { ... }.<method>(`
    for m in re.finditer(r'request\(([^)]+)\)(?:\s*\{[^}]*\})?\s*\.(get|post|put|patch|delete)\b', text):
        parts = [p.strip() for p in m.group(1).split(",") if "auth" not in p]
        segs = [p[1:-1] if p.startswith('"') and p.endswith('"') else "{x}" for p in parts]
        path = "/" + "/".join(segs)
        if path.startswith("/api/"):
            out.add((m.group(2).upper(), path))
    # Helper wrappers inside Api.kt (`history(...)`, `manual(...)`)
    for m in re.finditer(r'\bhistory\(([^)]*)\)\s*\.(get|post)\b', text):
        args = [p.strip() for p in m.group(1).split(",") if p.strip()]
        segs = [p[1:-1] if p.startswith('"') and p.endswith('"') else "{x}" for p in args]
        out.add((m.group(2).upper(), "/api/intake/history" + ("/" + "/".join(segs) if segs else "")))
    for m in re.finditer(r'\bmanual\(([^)]+)\)\s*\.(get|post)\b', text):
        args = [p.strip() for p in m.group(1).split(",") if p.strip()][1:]
        segs = [p[1:-1] if p.startswith('"') and p.endswith('"') else "{x}" for p in args]
        out.add((m.group(2).upper(), "/api/printers/{x}/manual-map" + ("/" + "/".join(segs) if segs else "")))
    out.update({
        ("GET", "/api/printers/{x}/camera/{x}"),
        ("GET", "/api/printers/{x}/camera.mjpeg"),
        ("GET", "/api/spools/{x}/label.svg"),
        ("GET", "/api/spools"),
        ("GET", "/api/events"),
        ("GET", "/api/filaments"),
        ("GET", "/api/printers/{x}/usage"),
        ("GET", "/api/profiles/parents"),
        ("GET", "/api/profiles/orca-parents"),
        ("GET", "/api/export/{x}"),
        ("GET", "/api/intake/history/{x}/photos/{x}"),
    })
    return out


def _replace_braces(path: str) -> str:
    out: list[str] = []
    depth = 0
    for ch in path:
        if ch == "{":
            if depth == 0:
                out.append("{x}")
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(out)


def _web_and_cli_calls() -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    files = list((SERVER_FK / "web").glob("*.py")) + [SERVER_FK / "reader_tokens.py"] + list(SERVER_FK.glob("*cli*.py"))
    for f in files:
        txt = f.read_text(encoding="utf-8")
        for pat in (r'\.(get|post|put|patch|delete)\(\s*f?"(/api/[^"?]+)',
                    r"\.(get|post|put|patch|delete)\(\s*f?'(/api/[^'?]+)"):
            for m in re.finditer(pat, txt):
                path = _replace_braces(m.group(2))
                if path.endswith("/"):
                    continue
                if path == "/api/export/{x}":
                    path = "/api/export/{x}.csv"
                elif path == "/api/printers/{x}/usage{x}":
                    path = "/api/printers/{x}/usage"
                elif path == "/api/printers/{x}/slots/{x}/{x}":
                    continue
                out.add((m.group(1).upper(), path))
    out.update({
        ("POST", "/api/printers/{x}/slots/{x}/load"),
        ("POST", "/api/printers/{x}/slots/{x}/unload"),
        ("POST", "/api/printers/{x}/slots/{x}/clear"),
        ("GET", "/api/printers/{x}/camera.mjpeg"),
        ("GET", "/api/printers/{x}/camera/{x}"),
        ("POST", "/api/events/{x}/undo"),
        ("POST", "/api/events/web"),
        ("POST", "/api/readers/web/seen"),
    })
    return out


def test_every_path_called_by_android_web_twins_and_cli_is_mounted_on_the_hub(sm):
    app = create_app(sm=sm)
    mounted: list[tuple[str, re.Pattern[str]]] = []
    for r in app.routes:
        if isinstance(r, Route) and r.path.startswith("/api/"):
            rx_text = re.sub(r"\{\w+:path\}", ".+", re.sub(r"\{\w+(?::int)?\}", "[^/]+", r.path))
            rx = re.compile(rx_text.replace("[^/]+.csv", "[^/]+"))
            for m in r.methods or ():
                if m != "HEAD":
                    mounted.append((m, rx))

    deferred = {(m, _normalise_template(p)) for m, p in DEFERRED_CLIENT_ROUTES}
    called = _android_calls() | _web_and_cli_calls()
    missing = []
    for method, path in sorted(called):
        if (method, _normalise_template(path)) in deferred:
            continue
        sample = path.replace("{x}.csv", "usage.csv").replace("{x}", "1")
        if not any(m == method and rx.fullmatch(sample) for m, rx in mounted):
            missing.append((method, path))
    assert missing == [], f"Routes called by apps/CLI but missing on Hub: {missing}"
