"""Per-reader remote configuration and command queue, stored atomically in `FK_STATE/reader-config.xml` (0600)."""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

from defusedxml import ElementTree as DET
from defusedxml.common import DefusedXmlException

from . import config, reader_tokens

log = logging.getLogger(__name__)

FILE = "reader-config.xml"
MAX_BYTES = 128 * 1024
VERSION = "1"
COMMANDS = ("ota", "reboot")

_lock = threading.Lock()


class ReaderConfigError(ValueError):
    """Invalid reader configuration key, value, or command."""


_SPECS: tuple[dict, ...] = (
    {"id": "default_printer", "label": "Default printer", "kind": "text", "value": "", "max_len": 32},
    {"id": "buzzer_enabled", "label": "Buzzer enabled", "kind": "switch", "value": True},
    {"id": "buzzer_low", "label": "Low-volume buzzer", "kind": "switch", "value": False},
    {"id": "auto_ota", "label": "Automatic firmware updates", "kind": "switch", "value": True},
    {"id": "dim_after_s", "label": "Screen dim timeout (seconds)", "kind": "number", "value": 120, "min": 10, "max": 3600},
    {"id": "off_after_s", "label": "Screen off timeout (seconds)", "kind": "number", "value": 600, "min": 30, "max": 7200},
    {"id": "scan_cooldown_ms", "label": "Tag scan cooldown (ms)", "kind": "number", "value": 2500, "min": 500, "max": 10000},
    {"id": "list_refresh_s", "label": "Slot list refresh (seconds)", "kind": "number", "value": 60, "min": 10, "max": 600},
    {"id": "ota_check_hours", "label": "OTA check interval (hours)", "kind": "number", "value": 24, "min": 1, "max": 168},
    {"id": "encoder_invert", "label": "Invert rotary encoder", "kind": "switch", "value": False},
    {"id": "hub_ip", "label": "Hub IP override", "kind": "text", "value": "", "max_len": 45},
)
_SPEC_MAP = {s["id"]: s for s in _SPECS}


def specs() -> list[dict]:
    """Return a copy of all reader config specifications with their default values."""
    return [dict(s) for s in _SPECS]


def _default_path() -> Path:
    return config.paths().state / FILE


def _check_tid(tid: str) -> str:
    if not isinstance(tid, str) or not reader_tokens.TOKEN_ID.fullmatch(tid):
        raise ReaderConfigError(f"invalid reader id: {tid!r}")
    return tid


def _clean_value(spec: dict, value):
    kind = spec["kind"]
    if kind == "switch":
        if isinstance(value, bool):
            return value
        if isinstance(value, (str, int)):
            text = str(value).strip().lower()
            if text in ("1", "on", "true"):
                return True
            if text in ("0", "off", "false"):
                return False
    elif kind == "number":
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        elif isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value.strip())
        if isinstance(value, int) and not isinstance(value, bool) and spec["min"] <= value <= spec["max"]:
            return value
    elif kind == "text":
        if isinstance(value, str) and len(value) <= spec["max_len"] and not any(c in value for c in ("\x00", "\r", "\n")):
            return value
    raise ReaderConfigError(f"{spec['id']}: invalid value")


def _encode_value(value) -> str:
    if isinstance(value, bool):
        return "on" if value else "off"
    return str(value)


def _read_all(p: Path) -> dict[str, dict]:
    try:
        fd = os.open(p, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        return {}
    except OSError as e:
        log.warning("reader config: could not open %s (%s)", p, e)
        return {}
    try:
        with os.fdopen(fd, "rb") as fh:
            raw = fh.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError("too big")
        root = DET.fromstring(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
        if root.tag != "reader-config":
            raise ValueError("not a reader-config file")
    except (OSError, ValueError, DET.ParseError, DefusedXmlException) as e:
        log.warning("reader config: could not parse %s (%s)", p, e)
        return {}
    out: dict[str, dict] = {}
    for el in root:
        if el.tag != "reader":
            continue
        tid = el.get("id") or ""
        if not reader_tokens.TOKEN_ID.fullmatch(tid):
            continue
        try:
            ver = max(0, int(el.get("config-version") or "0"))
        except ValueError:
            ver = 0
        values: dict = {}
        for s_el in el:
            if s_el.tag != "setting":
                continue
            sid = s_el.get("id") or ""
            spec = _SPEC_MAP.get(sid)
            if not spec:
                continue
            try:
                val = _clean_value(spec, s_el.get("value"))
            except ReaderConfigError:
                continue
            if val != spec["value"]:
                values[sid] = val
        out[tid] = {
            "config_version": ver,
            "ota_now": (el.get("ota-now") or "0") == "1",
            "reboot_now": (el.get("reboot-now") or "0") == "1",
            "values": values,
        }
    return out


def _write_all(p: Path, rows: dict[str, dict]) -> None:
    root = ET.Element("reader-config", version=VERSION)
    for tid in sorted(rows):
        entry = rows[tid]
        r_el = ET.SubElement(
            root,
            "reader",
            id=tid,
            **{
                "config-version": str(int(entry.get("config_version") or 0)),
                "ota-now": "1" if entry.get("ota_now") else "0",
                "reboot-now": "1" if entry.get("reboot_now") else "0",
            },
        )
        for sid, val in sorted((entry.get("values") or {}).items()):
            ET.SubElement(r_el, "setting", id=sid, value=_encode_value(val))
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    tmp = None
    try:
        p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=FILE + ".", suffix=".tmp")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
            if hasattr(os, "fchmod"):
                os.fchmod(fh.fileno(), 0o600)
        os.replace(tmp, p)
    except OSError as e:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
        raise ReaderConfigError(f"could not save reader config: {e.strerror or e}") from e


def _entry(rows: dict[str, dict], tid: str) -> dict:
    return rows.get(tid) or {"config_version": 0, "ota_now": False, "reboot_now": False, "values": {}}


def effective(tid: str, path: Path | None = None) -> dict:
    """Effective `{id: value}` map for `tid` (defaults overlaid with stored overrides)."""
    _check_tid(tid)
    p = Path(path) if path is not None else _default_path()
    with _lock:
        entry = _entry(_read_all(p), tid)
    overrides = entry.get("values") or {}
    return {s["id"]: overrides.get(s["id"], s["value"]) for s in _SPECS}


def listing(tid: str, path: Path | None = None) -> list[dict]:
    """List of `{id, label, kind, value, ...}` specs with effective values for `tid`."""
    eff = effective(tid, path=path)
    return [{**s, "value": eff[s["id"]]} for s in _SPECS]


def update(tid: str, values: dict, path: Path | None = None) -> list[dict]:
    """Validate and store setting overrides for `tid`, incrementing `config_version`."""
    _check_tid(tid)
    if not isinstance(values, dict) or not values:
        raise ReaderConfigError("nothing to change")
    cleaned: dict = {}
    for k, v in values.items():
        spec = _SPEC_MAP.get(k)
        if spec is None:
            raise ReaderConfigError(f"{str(k)[:40]}: no such setting")
        cleaned[k] = _clean_value(spec, v)
    p = Path(path) if path is not None else _default_path()
    with _lock:
        rows = _read_all(p)
        entry = dict(_entry(rows, tid))
        merged = dict(entry.get("values") or {})
        for k, v in cleaned.items():
            if v == _SPEC_MAP[k]["value"]:
                merged.pop(k, None)
            else:
                merged[k] = v
        entry["values"] = merged
        entry["config_version"] = int(entry.get("config_version") or 0) + 1
        rows[tid] = entry
        _write_all(p, rows)
    return listing(tid, path=p)


def reset(tid: str, path: Path | None = None) -> list[dict]:
    """Reset all settings for `tid` back to defaults, incrementing `config_version`."""
    _check_tid(tid)
    p = Path(path) if path is not None else _default_path()
    with _lock:
        rows = _read_all(p)
        entry = dict(_entry(rows, tid))
        entry["values"] = {}
        entry["config_version"] = int(entry.get("config_version") or 0) + 1
        rows[tid] = entry
        _write_all(p, rows)
    return listing(tid, path=p)


def queue_command(tid: str, cmd: str, path: Path | None = None) -> None:
    """Queue a one-shot command (`ota` or `reboot`) for `tid`."""
    _check_tid(tid)
    if cmd not in COMMANDS:
        raise ReaderConfigError(f"unknown command: {cmd!r}")
    p = Path(path) if path is not None else _default_path()
    key = "ota_now" if cmd == "ota" else "reboot_now"
    with _lock:
        rows = _read_all(p)
        entry = dict(_entry(rows, tid))
        entry[key] = True
        rows[tid] = entry
        _write_all(p, rows)


def ack_command(tid: str, cmd: str, path: Path | None = None) -> None:
    """Acknowledge (clear) a queued command (`ota` or `reboot`) for `tid`."""
    _check_tid(tid)
    if cmd not in COMMANDS:
        raise ReaderConfigError(f"unknown command: {cmd!r}")
    p = Path(path) if path is not None else _default_path()
    key = "ota_now" if cmd == "ota" else "reboot_now"
    with _lock:
        rows = _read_all(p)
        entry = dict(_entry(rows, tid))
        entry[key] = False
        rows[tid] = entry
        _write_all(p, rows)


def pending(tid: str, path: Path | None = None) -> dict:
    """Return `{"config_version": int, "ota_now": bool, "reboot_now": bool}` for `tid`."""
    _check_tid(tid)
    p = Path(path) if path is not None else _default_path()
    with _lock:
        entry = _entry(_read_all(p), tid)
    return {
        "config_version": int(entry.get("config_version") or 0),
        "ota_now": bool(entry.get("ota_now")),
        "reboot_now": bool(entry.get("reboot_now")),
    }
