"""The demo's Spoolman: the REST paths the Hub uses, answered from `<state>/demo/inventory.xml` (no JSON is stored).

`spoolman.Spoolman._request` calls `request()` while the demo is on, so every client the Hub makes (the Hub, the
command line, the web unit's own client reads the same file) talks to this inventory and never to the real Spoolman.
The answers have Spoolman's shapes (a spool with its filament and the filament's vendor nested; extra fields as JSON
text, as Spoolman keeps them). A path the Hub does not use answers 404.

File: `<inventory version="1">` with `<vendors>`, `<filaments>` (`<extra key value/>` children), `<spools>` (the same)
and `<locations>`. Written whole and atomically on every change (a few dozen records); a lock keeps threads apart.
"""
from __future__ import annotations

import math
import os
import tempfile
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .. import locks
from ..bridge import xmlfile

FILE = "inventory.xml"
VERSION = "1"
MAX_BYTES = 8_000_000
_lock = locks.ProcessLock(lambda: _path().with_name("inventory.lock"))

_VENDOR = {"id": int, "name": str, "empty_spool_weight": float}
_FILAMENT = {"id": int, "vendor_id": int, "name": str, "material": str, "color_hex": str, "density": float,
             "diameter": float, "weight": float, "spool_weight": float, "settings_extruder_temp": int,
             "settings_bed_temp": int, "price": float}
_SPOOL = {"id": int, "filament_id": int, "remaining_weight": float, "initial_weight": float, "spool_weight": float,
          "location": str, "lot_nr": str, "comment": str, "price": float, "registered": str, "first_used": str,
          "last_used": str}


class DemoSpoolmanError(Exception):
    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.message, self.status = message, status


def _name(key: str) -> str:
    return key.replace("_", "-")


def _children(parent, tag: str) -> list:
    sec = parent.find(tag)
    return list(sec) if sec is not None else []


def _read_section(parent, tag: str, spec: dict) -> dict[int, dict]:
    out: dict[int, dict] = {}
    for el in _children(parent, tag):
        rec: dict = {}
        for key, kind in spec.items():
            raw = el.get(_name(key))
            if raw is None:
                continue
            try:
                rec[key] = kind(raw)
            except ValueError:
                continue                          # an odd value is left out, as if the file never had it
        if "id" not in rec:
            continue
        rec["extra"] = {e.get("key"): e.get("value", "") for e in el.findall("extra") if e.get("key")}
        if "archived" in el.attrib:
            rec["archived"] = el.get("archived") == "true"
        out[rec["id"]] = rec
    return out


class Inventory:
    """The records, read from one file. `load` reads, `save` writes; both under the module lock."""

    def __init__(self, vendors, filaments, spools, locations):
        self.vendors, self.filaments, self.spools, self.locations = vendors, filaments, spools, locations

    @classmethod
    def parse(cls, data: bytes) -> "Inventory":
        root = xmlfile.fromstring(data)
        if root.tag not in ("inventory", "demo-sample"):
            raise ValueError("not a demo inventory")
        locations = [e.get("name") for e in _children(root, "locations") if e.get("name")]
        return cls(_read_section(root, "vendors", _VENDOR), _read_section(root, "filaments", _FILAMENT),
                   _read_section(root, "spools", _SPOOL), locations)

    def element(self) -> ET.Element:
        root = ET.Element("inventory", version=VERSION)

        def put(tag, child, spec, records):
            sec = ET.SubElement(root, tag)
            for rid in sorted(records):
                r = records[rid]
                el = ET.SubElement(sec, child, {_name(k): xmlfile.clean(r[k]) for k in spec if k in r})
                if r.get("archived"):
                    el.set("archived", "true")
                for k, v in sorted((r.get("extra") or {}).items()):
                    ET.SubElement(el, "extra", key=xmlfile.clean(k), value=xmlfile.clean(v))
        put("vendors", "vendor", _VENDOR, self.vendors)
        put("filaments", "filament", _FILAMENT, self.filaments)
        put("spools", "spool", _SPOOL, self.spools)
        ls = ET.SubElement(root, "locations")
        for name in self.locations:
            ET.SubElement(ls, "location", name=xmlfile.clean(name))
        ET.indent(root)
        return root

    def next_id(self, records: dict) -> int:
        return max(records, default=0) + 1


def seed(path: Path) -> None:
    """Make the demo's inventory file from the sample set."""
    from . import SAMPLE
    inv = Inventory.parse(SAMPLE.read_bytes())
    _save(inv, path)


def _path() -> Path:
    from . import folder
    return folder() / FILE


def _save(inv: Inventory, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            ET.ElementTree(inv.element()).write(fh, encoding="utf-8", xml_declaration=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def load() -> Inventory:
    p = _path()
    if p.stat().st_size > MAX_BYTES:
        raise DemoSpoolmanError("the demo inventory is too large", 500)
    return Inventory.parse(p.read_bytes())


# --- Spoolman's shapes -------------------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _vendor_out(inv: Inventory, vid) -> dict | None:
    v = inv.vendors.get(vid)
    return None if v is None else {"id": v["id"], "name": v["name"], "registered": "2026-01-01T00:00:00+00:00",
                                   **({"empty_spool_weight": v["empty_spool_weight"]} if "empty_spool_weight" in v
                                      else {}), "extra": {}}


def _filament_out(inv: Inventory, f: dict) -> dict:
    out = {k: f[k] for k in _FILAMENT if k in f and k != "vendor_id"}
    out.update(registered="2026-01-01T00:00:00+00:00", vendor=_vendor_out(inv, f.get("vendor_id")),
               extra=dict(f.get("extra") or {}))
    return out


def _spool_out(inv: Inventory, s: dict) -> dict:
    f = inv.filaments.get(s.get("filament_id"), {})
    initial = s.get("initial_weight", f.get("weight", 1000.0))
    remaining = s.get("remaining_weight", initial)
    out = {"id": s["id"], "registered": s.get("registered", ""), "filament": _filament_out(inv, f) if f else None,
           "remaining_weight": remaining, "used_weight": round(initial - remaining, 3), "initial_weight": initial,
           "spool_weight": s.get("spool_weight", f.get("spool_weight")), "location": s.get("location", ""),
           "lot_nr": s.get("lot_nr", ""), "comment": s.get("comment", ""), "archived": bool(s.get("archived")),
           "extra": dict(s.get("extra") or {})}
    for k in ("first_used", "last_used", "price"):
        if k in s:
            out[k] = s[k]
    return out


def _bad(message: str, status: int = 400):
    raise DemoSpoolmanError(message, status)


def _take(body: dict, spec: dict, rec: dict, skip=("id",)) -> None:
    for key, kind in spec.items():
        if key in skip or key not in body or body[key] is None:
            continue
        try:
            rec[key] = kind(body[key])
        except (TypeError, ValueError):
            _bad(f"{key}: not a valid value", 422)
    if isinstance(body.get("extra"), dict):
        rec["extra"] = {str(k): str(v) for k, v in body["extra"].items()}
    if "archived" in body:
        rec["archived"] = bool(body["archived"])


def _spool(inv: Inventory, sid: int) -> dict:
    s = inv.spools.get(sid)
    if s is None:
        _bad(f"No spool with ID {sid} found.", 404)
    return s


def _apply_spool(inv: Inventory, s: dict, body: dict) -> None:
    _take(body, _SPOOL, s)
    if "used_weight" in body and "remaining_weight" not in body:
        try:
            used_weight = float(body["used_weight"])
        except (ValueError, TypeError):
            _bad("used_weight: must be a number", 422)
        if not math.isfinite(used_weight):
            _bad("used_weight: must be a finite number", 422)
        f = inv.filaments.get(s.get("filament_id"), {})
        s["remaining_weight"] = max(0.0, s.get("initial_weight", f.get("weight", 1000.0)) - used_weight)
    if s.get("filament_id") not in inv.filaments:
        _bad("filament_id: no such filament", 422)
    if s.get("location") and s["location"] not in inv.locations and " – " not in s["location"]:
        inv.locations.append(s["location"])


def request(method: str, path: str, body=None):
    """Answer one Spoolman call (`method`, `path` relative to /api/v1) from the demo's inventory."""
    with _lock:
        parts = urllib.parse.urlsplit("/" + path.lstrip("/"))
        seg = [p for p in parts.path.split("/") if p]
        query = urllib.parse.parse_qs(parts.query)
        body = body if isinstance(body, dict) else {}
        if seg == ["info"] and method == "GET":
            return {"version": "demo"}
        if seg[:1] == ["field"] and len(seg) in (2, 3):
            if method == "GET" and len(seg) == 2:
                from ..spoolman import EXTRA_FIELDS
                return [{"key": k, "name": k, "field_type": t} for e, k, t in EXTRA_FIELDS if e == seg[1]]
            return {}                                 # the fields are all there already: making one changes nothing
        if seg and seg[0] in ("vendor", "filament", "spool"):
            return _records(method, seg, query, body)
        _bad(f"Not Found: {path}", 404)


def _records(method: str, seg: list[str], query: dict, body: dict):
    inv = load()
    kind = seg[0]
    table = {"vendor": inv.vendors, "filament": inv.filaments, "spool": inv.spools}[kind]
    if len(seg) == 1:
        if method == "GET":
            if kind == "vendor":
                return [_vendor_out(inv, i) for i in sorted(table)]
            if kind == "filament":
                return [_filament_out(inv, table[i]) for i in sorted(table)]
            archived = (query.get("allow_archived") or ["false"])[0].lower() == "true"
            return [_spool_out(inv, table[i]) for i in sorted(table) if archived or not table[i].get("archived")]
        if method == "POST":
            rec: dict = {"id": inv.next_id(table)}
            if kind == "vendor":
                _take(body, _VENDOR, rec)
                if not rec.get("name"):
                    _bad("name: required", 422)
            elif kind == "filament":
                _take(body, _FILAMENT, rec)
                if rec.get("vendor_id") is not None and rec["vendor_id"] not in inv.vendors:
                    _bad("vendor_id: no such vendor", 422)
            else:
                rec["registered"] = _now()
                _apply_spool(inv, rec, body)
            table[rec["id"]] = rec
            _save(inv, _path())
            return (_vendor_out(inv, rec["id"]) if kind == "vendor" else
                    _filament_out(inv, rec) if kind == "filament" else _spool_out(inv, rec))
        _bad("Method Not Allowed", 405)
    try:
        rid = int(seg[1])
    except ValueError:
        _bad("not a number", 422)
    rec = table.get(rid)
    if rec is None:
        _bad(f"No {kind} with ID {rid} found.", 404)
    out = {"vendor": lambda r: _vendor_out(inv, r["id"]), "filament": lambda r: _filament_out(inv, r),
           "spool": lambda r: _spool_out(inv, r)}[kind]
    if len(seg) == 2:
        if method == "GET":
            return out(rec)
        if method == "PATCH":
            if kind == "spool":
                _apply_spool(inv, rec, body)
            else:
                _take(body, _VENDOR if kind == "vendor" else _FILAMENT, rec)
            _save(inv, _path())
            return out(rec)
        if method == "DELETE":
            del table[rid]
            _save(inv, _path())
            return None
        _bad("Method Not Allowed", 405)
    if kind == "spool" and seg[2] == "use" and len(seg) == 3 and method == "PUT":
        raw_weight = body.get("use_weight")
        try:
            grams = float(0 if raw_weight is None or raw_weight == "" else raw_weight)
        except (ValueError, TypeError):
            _bad("use_weight: must be a number", 422)
        if not math.isfinite(grams):
            _bad("use_weight: must be a finite number", 422)
        f = inv.filaments.get(rec.get("filament_id"), {})
        initial = rec.get("initial_weight", f.get("weight", 1000.0))
        rec["remaining_weight"] = round(min(initial, max(0.0, rec.get("remaining_weight", initial) - grams)), 3)
        rec.setdefault("first_used", _now())
        rec["last_used"] = _now()
        _save(inv, _path())
        return out(rec)
    _bad(f"Not Found: {'/'.join(seg)}", 404)
