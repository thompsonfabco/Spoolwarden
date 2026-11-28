"""Role-based access control (built-in and custom roles in `etc/enterprise/roles.xml`)."""
from __future__ import annotations

import copy
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import quoteattr

import defusedxml.ElementTree as DET
from defusedxml import DefusedXmlException

from .. import config, demo
from .policy import _locked_file, _read_nofollow, _write_atomic


class RoleError(ValueError):
    """Raised on invalid role definitions or operations."""


NA, RO, RW = 0, 1, 2
LEVEL_NAMES: dict[int, str] = {0: "NA", 1: "RO", 2: "RW"}
NAME_LEVELS: dict[str, int] = {"NA": 0, "RO": 1, "RW": 2}

SECTIONS: dict[str, tuple[str, ...]] = {
    "spools": ("spools.inventory", "spools.storage", "spools.import_export", "spools.labels"),
    "printers": ("printers.manage", "printers.slots", "printers.control", "printers.camera"),
    "presets": ("presets.manage", "presets.sync"),
    "analytics": ("analytics.view", "analytics.undo"),
    "recent": ("recent.intake", "recent.corrections"),
    "devices": ("devices.paired", "devices.stations", "devices.readers", "devices.flash"),
    "system": ("system.features", "system.settings", "system.backups", "system.updates", "system.logs"),
    "enterprise": (
        "enterprise.toggles",
        "enterprise.users",
        "enterprise.roles",
        "enterprise.sso",
        "enterprise.certs",
        "enterprise.audit",
    ),
}

ALL_FUNCTIONS: frozenset[str] = frozenset(fn for fns in SECTIONS.values() for fn in fns)
_FN_TO_SECTION: dict[str, str] = {fn: sec for sec, fns in SECTIONS.items() for fn in fns}
_ROLE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

BUILTIN_ROLES: dict[str, dict] = {
    "administrator": {
        "id": "administrator",
        "name": "Administrator",
        "builtin": True,
        "sections": {sec: RW for sec in SECTIONS},
        "functions": {},
    },
    "operator": {
        "id": "operator",
        "name": "Operator",
        "builtin": True,
        "sections": {
            "spools": RW,
            "printers": RW,
            "presets": RW,
            "analytics": RW,
            "recent": RW,
            "devices": RO,
            "system": RO,
            "enterprise": NA,
        },
        "functions": {},
    },
    "viewer": {
        "id": "viewer",
        "name": "Viewer",
        "builtin": True,
        "sections": {
            "spools": RO,
            "printers": RO,
            "presets": RO,
            "analytics": RO,
            "recent": RO,
            "devices": RO,
            "system": NA,
            "enterprise": NA,
        },
        "functions": {},
    },
    "sync": {
        "id": "sync",
        "name": "Sync",
        "builtin": True,
        "sections": {sec: NA for sec in SECTIONS},
        "functions": {
            "spools.inventory": RO,
            "presets.sync": RW,
        },
    },
}


def _roles_path() -> Path:
    return demo.where(config.paths().etc / "enterprise" / "roles.xml", "enterprise/roles.xml")


def _coerce_level(val: object) -> int:
    if isinstance(val, int) and not isinstance(val, bool) and val in LEVEL_NAMES:
        return val
    if isinstance(val, str):
        up = val.strip().upper()
        if up in NAME_LEVELS:
            return NAME_LEVELS[up]
        if up.isdigit() and int(up) in LEVEL_NAMES:
            return int(up)
    raise RoleError(f"Invalid permission level: {val!r}")


def _normalize_sections(sections: dict | None) -> dict[str, int]:
    out = {sec: NA for sec in SECTIONS}
    if not sections:
        return out
    for sec, lvl in sections.items():
        if sec not in SECTIONS:
            raise RoleError(f"Unknown section: {sec!r}")
        out[sec] = _coerce_level(lvl)
    return out


def _normalize_functions(functions: dict | None) -> dict[str, int]:
    out: dict[str, int] = {}
    if not functions:
        return out
    for fn, lvl in functions.items():
        if fn not in ALL_FUNCTIONS:
            raise RoleError(f"Unknown function key: {fn!r}")
        out[fn] = _coerce_level(lvl)
    return out


def _parse_roles_xml(raw: bytes) -> dict[str, dict]:
    if not raw.strip():
        return {}
    try:
        root = DET.fromstring(raw)
    except (DefusedXmlException, ET.ParseError) as exc:
        raise RoleError(f"Malformed roles XML: {exc}") from exc
    if root.tag != "enterprise-roles":
        raise RoleError("Invalid roles root element")

    roles: dict[str, dict] = {}
    for role_el in root.findall("role"):
        rid = role_el.attrib.get("id", "").strip()
        if not rid or rid in BUILTIN_ROLES:
            continue
        name = role_el.attrib.get("name", rid)
        sections = {sec: NA for sec in SECTIONS}
        for sec_el in role_el.findall("section"):
            sname = sec_el.attrib.get("name", "")
            if sname in SECTIONS:
                sections[sname] = _coerce_level(sec_el.attrib.get("level", "NA"))
        functions: dict[str, int] = {}
        for fn_el in role_el.findall("function"):
            fname = fn_el.attrib.get("name", "")
            if fname in ALL_FUNCTIONS:
                functions[fname] = _coerce_level(fn_el.attrib.get("level", "NA"))
        roles[rid] = {
            "id": rid,
            "name": name,
            "builtin": False,
            "sections": sections,
            "functions": functions,
        }
    return roles


def _render_roles_xml(roles: dict[str, dict]) -> bytes:
    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<enterprise-roles version="1">']
    for rid in sorted(roles):
        role = roles[rid]
        lines.append(f"  <role id={quoteattr(role['id'])} name={quoteattr(role['name'])}>")
        for sec in SECTIONS:
            lvl = LEVEL_NAMES[role["sections"].get(sec, NA)]
            lines.append(f"    <section name={quoteattr(sec)} level={quoteattr(lvl)}/>")
        for fn in sorted(role.get("functions", {})):
            lvl = LEVEL_NAMES[role["functions"][fn]]
            lines.append(f"    <function name={quoteattr(fn)} level={quoteattr(lvl)}/>")
        lines.append("  </role>")
    lines.append("</enterprise-roles>")
    return ("\n".join(lines) + "\n").encode("utf-8")


def list_roles() -> list[dict]:
    """Return all built-in roles followed by custom roles."""
    path = _roles_path()
    with _locked_file(path):
        custom = _parse_roles_xml(_read_nofollow(path))
    out = [copy.deepcopy(BUILTIN_ROLES[rid]) for rid in BUILTIN_ROLES]
    out.extend(copy.deepcopy(custom[rid]) for rid in sorted(custom))
    return out


def get_role(role_id: str) -> dict | None:
    """Return a role dict by id, or None if not found."""
    if role_id in BUILTIN_ROLES:
        return copy.deepcopy(BUILTIN_ROLES[role_id])
    path = _roles_path()
    with _locked_file(path):
        custom = _parse_roles_xml(_read_nofollow(path))
    role = custom.get(role_id)
    return copy.deepcopy(role) if role is not None else None


def create_role(
    role_id: str,
    name: str,
    sections: dict | None = None,
    functions: dict | None = None,
) -> dict:
    """Create and persist a new custom role."""
    rid = (role_id or "").strip().lower()
    if not _ROLE_ID_RE.match(rid):
        raise RoleError("Invalid role_id: must match ^[a-z0-9][a-z0-9_-]{0,63}$")
    if rid in BUILTIN_ROLES:
        raise RoleError(f"Cannot create role with built-in id {rid!r}")
    clean_name = (name or "").strip() or rid
    norm_sec = _normalize_sections(sections)
    norm_fn = _normalize_functions(functions)

    path = _roles_path()
    with _locked_file(path):
        custom = _parse_roles_xml(_read_nofollow(path))
        if rid in custom:
            raise RoleError(f"Role {rid!r} already exists")
        role = {
            "id": rid,
            "name": clean_name,
            "builtin": False,
            "sections": norm_sec,
            "functions": norm_fn,
        }
        custom[rid] = role
        _write_atomic(path, _render_roles_xml(custom), 0o600)
        return copy.deepcopy(role)


def update_role(
    role_id: str,
    name: str | None = None,
    sections: dict | None = None,
    functions: dict | None = None,
) -> dict:
    """Update an existing custom role."""
    if role_id in BUILTIN_ROLES:
        raise RoleError(f"Cannot modify built-in role {role_id!r}")
    path = _roles_path()
    with _locked_file(path):
        custom = _parse_roles_xml(_read_nofollow(path))
        if role_id not in custom:
            raise RoleError(f"Role {role_id!r} not found")
        role = custom[role_id]
        if name is not None:
            clean_name = name.strip()
            if not clean_name:
                raise RoleError("Role name cannot be empty")
            role["name"] = clean_name
        if sections is not None:
            merged_sec = dict(role["sections"])
            for sec, lvl in sections.items():
                if sec not in SECTIONS:
                    raise RoleError(f"Unknown section: {sec!r}")
                merged_sec[sec] = _coerce_level(lvl)
            role["sections"] = merged_sec
        if functions is not None:
            role["functions"] = _normalize_functions(functions)
        _write_atomic(path, _render_roles_xml(custom), 0o600)
        return copy.deepcopy(role)


def delete_role(role_id: str, in_use_roles: set[str] | list[str] | tuple[str, ...] = ()) -> None:
    """Delete a custom role, rejecting built-in or in-use roles."""
    if role_id in BUILTIN_ROLES:
        raise RoleError(f"Cannot delete built-in role {role_id!r}")
    if role_id in set(in_use_roles):
        raise RoleError(f"Cannot delete role {role_id!r}: still assigned to one or more users")
    path = _roles_path()
    with _locked_file(path):
        custom = _parse_roles_xml(_read_nofollow(path))
        if role_id not in custom:
            raise RoleError(f"Role {role_id!r} not found")
        del custom[role_id]
        _write_atomic(path, _render_roles_xml(custom), 0o600)


def effective_permissions(role_id: str) -> dict[str, int]:
    """Compute effective per-function permission levels for `role_id`."""
    role = get_role(role_id)
    if role is None:
        return {fn: NA for fn in ALL_FUNCTIONS}
    sec_levels = role.get("sections", {})
    fn_overrides = role.get("functions", {})
    out: dict[str, int] = {}
    for sec, fns in SECTIONS.items():
        sec_default = sec_levels.get(sec, NA)
        for fn in fns:
            out[fn] = fn_overrides.get(fn, sec_default)
    return out


def allows(role_id: str, fn_key: str, need_level: int) -> bool:
    """Return True if `role_id` grants at least `need_level` for `fn_key`."""
    if fn_key not in ALL_FUNCTIONS:
        return False
    return effective_permissions(role_id).get(fn_key, NA) >= need_level


_EXACT_OR_PREFIX_ROUTES: tuple[tuple[str, str], ...] = (
    ("/setup/api/enterprise/toggles", "enterprise.toggles"),
    ("/setup/api/enterprise/policy", "enterprise.toggles"),
    ("/setup/api/enterprise/users", "enterprise.users"),
    ("/setup/api/enterprise/sessions", "enterprise.users"),
    ("/setup/api/enterprise/roles", "enterprise.roles"),
    ("/setup/api/enterprise/sso", "enterprise.sso"),
    ("/setup/api/enterprise/ldap", "enterprise.sso"),
    ("/setup/api/enterprise/saml", "enterprise.sso"),
    ("/setup/api/enterprise/certs", "enterprise.certs"),
    ("/setup/api/enterprise/cert", "enterprise.certs"),
    ("/setup/api/enterprise/audit", "enterprise.audit"),
    ("/setup/api/modules", "system.features"),
    ("/setup/api/features", "system.features"),
    ("/setup/api/settings", "system.settings"),
    ("/setup/api/config", "system.settings"),
    ("/setup/api/alerts", "system.settings"),
    ("/setup/api/setup-code", "system.settings"),
    ("/setup/api/passphrase", "system.settings"),
    ("/setup/api/backups", "system.backups"),
    ("/setup/api/backup", "system.backups"),
    ("/setup/api/updates", "system.updates"),
    ("/setup/api/update", "system.updates"),
    ("/setup/api/logs", "system.logs"),
    ("/setup/api/log", "system.logs"),
    ("/setup/api/paired", "devices.paired"),
    ("/setup/api/pairing", "devices.paired"),
    ("/setup/api/trust", "devices.paired"),
    ("/setup/api/devices", "devices.paired"),
    ("/setup/api/stations", "devices.stations"),
    ("/setup/api/station", "devices.stations"),
    ("/setup/api/readers", "devices.readers"),
    ("/setup/api/reader", "devices.readers"),
    ("/setup/api/flash", "devices.flash"),
    ("/setup/api/storage", "spools.storage"),
    ("/api/storage", "spools.storage"),
    ("/api/locations", "spools.storage"),
    ("/api/import", "spools.import_export"),
    ("/api/export", "spools.import_export"),
    ("/api/labels", "spools.labels"),
    ("/api/undo", "analytics.undo"),
    ("/api/alerts", "system.settings"),
    ("/api/label-reader/corrections", "recent.corrections"),
    ("/api/station", "devices.stations"),
)


def _match_subpath(rest: str, rules: tuple[tuple[str, str], ...], default: str) -> str:
    for token, fn_key in rules:
        if token in rest:
            return fn_key
    return default


def route_permission(method: str, path: str) -> tuple[str, int] | None:
    """Map an HTTP method and path under `/setup/api/*`, `/api/*`, or `/intake/api/*` to `(fn_key, RO|RW)`."""
    clean_path = (path or "").split("?", 1)[0]
    if not clean_path.startswith(("/setup/api/", "/api/", "/intake/api/")):
        return None
    upper_method = (method or "GET").upper()
    need = RO if upper_method in ("GET", "HEAD", "OPTIONS") else RW

    if clean_path == "/setup/api/enterprise/overview":
        return ("enterprise.toggles", RO)
    if clean_path == "/setup/api/enterprise/activity":
        return ("enterprise.audit", RO)
    if clean_path in ("/api/pair", "/setup/api/pair"):
        return ("devices.paired", RW)
    if clean_path in ("/setup/api/apply", "/setup/api/mode", "/setup/api/job"):
        return ("system.features", RW)
    if clean_path.startswith("/api/events/") and clean_path.endswith("/undo") and len(clean_path) > len("/api/events//undo"):
        return ("analytics.undo", RW)

    for prefix, fn_key in _EXACT_OR_PREFIX_ROUTES:
        if clean_path == prefix or clean_path.startswith(prefix + "/"):
            return (fn_key, need)

    if clean_path.startswith(("/api/printers", "/setup/api/printers")):
        return (
            _match_subpath(
                clean_path,
                (
                    ("/camera", "printers.camera"),
                    ("/slots", "printers.slots"),
                    ("/control", "printers.control"),
                    ("/pause", "printers.control"),
                    ("/resume", "printers.control"),
                    ("/stop", "printers.control"),
                ),
                "printers.manage",
            ),
            need,
        )

    if clean_path.startswith(("/api/spools", "/setup/api/spools")):
        return (
            _match_subpath(
                clean_path,
                (
                    ("/storage", "spools.storage"),
                    ("/locations", "spools.storage"),
                    ("/import", "spools.import_export"),
                    ("/export", "spools.import_export"),
                    ("/labels", "spools.labels"),
                    ("/label", "spools.labels"),
                ),
                "spools.inventory",
            ),
            need,
        )

    if clean_path.startswith(("/api/presets", "/setup/api/presets")):
        return (_match_subpath(clean_path, (("/sync", "presets.sync"),), "presets.manage"), need)

    if clean_path.startswith("/api/analytics"):
        return (_match_subpath(clean_path, (("/undo", "analytics.undo"),), "analytics.view"), need)

    if clean_path.startswith(("/intake/api/", "/api/intake", "/api/recent")):
        return (_match_subpath(clean_path, (("/corrections", "recent.corrections"),), "recent.intake"), need)

    if upper_method in ("POST", "PUT", "PATCH", "DELETE") and clean_path.startswith(("/setup/api/", "/api/")) and not clean_path.startswith("/api/auth/"):
        return ("system.settings", RW)

    return None
