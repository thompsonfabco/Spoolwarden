"""Enterprise policy storage and validation (`etc/enterprise/enterprise.xml`)."""
from __future__ import annotations

import contextlib
import copy
import os
import secrets
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import defusedxml.ElementTree as DET
from defusedxml import DefusedXmlException

from .. import config, demo

try:
    import fcntl
except ImportError:  # Windows: msvcrt byte lock fallback
    fcntl = None
    import msvcrt


class PolicyError(ValueError):
    """Raised when an enterprise policy file or change is invalid."""


TOGGLE_KEYS = (
    "multiuser_login",
    "setup_code_fallback",
    "rbac",
    "local_accounts",
    "ldap_sso",
    "saml_sso",
    "activity_audit",
    "cert_management",
)

DEFAULTS: dict = {
    "toggles": {
        "multiuser_login": False,
        "setup_code_fallback": True,
        "rbac": False,
        "local_accounts": True,
        "ldap_sso": False,
        "saml_sso": False,
        "activity_audit": True,
        "cert_management": True,
    },
    "sessions": {
        "browser_idle_s": 28800,
        "browser_abs_s": 86400,
        "client_sliding_s": 2592000,
    },
    "password": {
        "min_length": 12,
        "lock_max": 5,
        "lock_window_s": 600,
    },
    "ldap": {
        "url": "",
        "starttls": False,
        "bind_dn": "",
        "base_dn": "",
        "user_filter": "(&(objectClass=person)(uid={username}))",
        "group_attr": "memberOf",
        "group_map": {},
        "default_role": "viewer",
        "jit_provision": True,
        "verify_tls": True,
    },
    "saml": {
        "sp_base_url": "",
        "idp_entity_id": "",
        "idp_sso_url": "",
        "idp_slo_url": "",
        "idp_cert_pem": "",
        "role_attr": "groups",
        "group_map": {},
        "default_role": "viewer",
        "jit_provision": True,
    },
}

_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[Path, threading.RLock] = {}


def _thread_lock_for(path: Path) -> threading.RLock:
    key = path.resolve()
    with _THREAD_LOCKS_GUARD:
        lock = _THREAD_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _THREAD_LOCKS[key] = lock
        return lock


def _win_lock(lk) -> None:
    deadline = time.monotonic() + 60.0
    while True:
        lk.seek(0)
        try:
            msvcrt.locking(lk.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if time.monotonic() > deadline:
                raise
            time.sleep(0.005)


@contextlib.contextmanager
def _locked_file(path: Path):
    """Acquire an in-process RLock and an OS-level flock/msvcrt lock on `path.with_suffix('.lock')`."""
    tlock = _thread_lock_for(path)
    with tlock:
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = path.with_suffix(".lock")
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        with os.fdopen(fd, "r+b") as lk:
            if fcntl is not None:
                fcntl.flock(lk.fileno(), fcntl.LOCK_EX)
            else:
                _win_lock(lk)
            try:
                yield
            finally:
                if fcntl is None:
                    lk.seek(0)
                    msvcrt.locking(lk.fileno(), msvcrt.LK_UNLCK, 1)


def _read_nofollow(path: Path, max_bytes: int = 1024 * 1024) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return b""
    with os.fdopen(fd, "rb") as fh:
        return fh.read(max_bytes)


def _write_atomic(dest: Path, data: bytes, mode: int = 0o600) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(f"{dest.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    try:
        if os.name == "nt":
            tmp.write_bytes(data)
        else:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, mode)
            with os.fdopen(fd, "wb") as fh:
                os.fchmod(fh.fileno(), mode)
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        for tries in range(50):
            try:
                os.replace(tmp, dest)
                break
            except PermissionError:
                if os.name != "nt" or tries == 49:
                    raise
                time.sleep(0.02)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _policy_path() -> Path:
    return demo.where(config.paths().etc / "enterprise" / "enterprise.xml", "enterprise/enterprise.xml")


def _parse_bool(val: object, default: bool) -> bool:
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        low = val.strip().lower()
        if low in ("true", "1", "yes", "on"):
            return True
        if low in ("false", "0", "no", "off"):
            return False
    return default


def _parse_int(val: object, default: int, minimum: int = 1) -> int:
    try:
        parsed = int(val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default
    return max(minimum, parsed)


def _normalize_policy(data: dict | None) -> dict:
    out = copy.deepcopy(DEFAULTS)
    if not isinstance(data, dict):
        return out

    raw_toggles = data.get("toggles")
    if isinstance(raw_toggles, dict):
        for key in TOGGLE_KEYS:
            if key in raw_toggles:
                out["toggles"][key] = _parse_bool(raw_toggles[key], out["toggles"][key])

    raw_sessions = data.get("sessions")
    if isinstance(raw_sessions, dict):
        for key, default in DEFAULTS["sessions"].items():
            if key in raw_sessions:
                out["sessions"][key] = _parse_int(raw_sessions[key], default, minimum=60)

    raw_pw = data.get("password")
    if isinstance(raw_pw, dict):
        out["password"]["min_length"] = _parse_int(raw_pw.get("min_length"), DEFAULTS["password"]["min_length"], minimum=8)
        out["password"]["lock_max"] = _parse_int(raw_pw.get("lock_max"), DEFAULTS["password"]["lock_max"], minimum=1)
        out["password"]["lock_window_s"] = _parse_int(raw_pw.get("lock_window_s"), DEFAULTS["password"]["lock_window_s"], minimum=10)

    raw_ldap = data.get("ldap")
    if isinstance(raw_ldap, dict):
        for key in ("url", "bind_dn", "base_dn", "user_filter", "group_attr", "default_role"):
            if key in raw_ldap and raw_ldap[key] is not None:
                out["ldap"][key] = str(raw_ldap[key])
        for key in ("starttls", "jit_provision", "verify_tls"):
            if key in raw_ldap:
                out["ldap"][key] = _parse_bool(raw_ldap[key], out["ldap"][key])
        if isinstance(raw_ldap.get("group_map"), dict):
            out["ldap"]["group_map"] = {
                str(k): str(v) for k, v in raw_ldap["group_map"].items() if str(k).strip() and str(v).strip()
            }

    raw_saml = data.get("saml")
    if isinstance(raw_saml, dict):
        for key in ("sp_base_url", "idp_entity_id", "idp_sso_url", "idp_slo_url", "idp_cert_pem", "role_attr", "default_role"):
            if key in raw_saml and raw_saml[key] is not None:
                out["saml"][key] = str(raw_saml[key])
        if "jit_provision" in raw_saml:
            out["saml"]["jit_provision"] = _parse_bool(raw_saml["jit_provision"], out["saml"]["jit_provision"])
        if isinstance(raw_saml.get("group_map"), dict):
            out["saml"]["group_map"] = {
                str(k): str(v) for k, v in raw_saml["group_map"].items() if str(k).strip() and str(v).strip()
            }

    return out


def _parse_xml(raw: bytes) -> dict:
    if not raw.strip():
        return copy.deepcopy(DEFAULTS)
    try:
        root = DET.fromstring(raw)
    except (DefusedXmlException, ET.ParseError) as exc:
        raise PolicyError(f"Malformed enterprise policy XML: {exc}") from exc
    if root.tag != "enterprise-policy":
        raise PolicyError("Invalid enterprise policy root element")

    data: dict = {"toggles": {}, "sessions": {}, "password": {}, "ldap": {}, "saml": {}}

    toggles_el = root.find("toggles")
    if toggles_el is not None:
        for child in toggles_el.findall("toggle"):
            name = child.attrib.get("name", "")
            if name in TOGGLE_KEYS:
                data["toggles"][name] = child.attrib.get("value", "")

    sess_el = root.find("sessions")
    if sess_el is not None:
        for key in DEFAULTS["sessions"]:
            if key in sess_el.attrib:
                data["sessions"][key] = sess_el.attrib[key]

    pw_el = root.find("password")
    if pw_el is not None:
        for key in DEFAULTS["password"]:
            if key in pw_el.attrib:
                data["password"][key] = pw_el.attrib[key]

    ldap_el = root.find("ldap")
    if ldap_el is not None:
        for key in ("url", "starttls", "bind_dn", "base_dn", "user_filter", "group_attr", "default_role", "jit_provision", "verify_tls"):
            if key in ldap_el.attrib:
                data["ldap"][key] = ldap_el.attrib[key]
        gm_el = ldap_el.find("group_map")
        if gm_el is not None:
            data["ldap"]["group_map"] = {
                e.attrib["group"]: e.attrib["role"]
                for e in gm_el.findall("entry")
                if e.attrib.get("group") and e.attrib.get("role")
            }

    saml_el = root.find("saml")
    if saml_el is not None:
        for key in ("sp_base_url", "idp_entity_id", "idp_sso_url", "idp_slo_url", "role_attr", "default_role", "jit_provision"):
            if key in saml_el.attrib:
                data["saml"][key] = saml_el.attrib[key]
        cert_el = saml_el.find("idp_cert_pem")
        if cert_el is not None and cert_el.text is not None:
            data["saml"]["idp_cert_pem"] = cert_el.text
        gm_el = saml_el.find("group_map")
        if gm_el is not None:
            data["saml"]["group_map"] = {
                e.attrib["group"]: e.attrib["role"]
                for e in gm_el.findall("entry")
                if e.attrib.get("group") and e.attrib.get("role")
            }

    return _normalize_policy(data)


def _render_xml(pol: dict) -> bytes:
    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<enterprise-policy version="1">']
    lines.append("  <toggles>")
    for key in TOGGLE_KEYS:
        val = "true" if pol["toggles"].get(key) else "false"
        lines.append(f"    <toggle name={quoteattr(key)} value={quoteattr(val)}/>")
    lines.append("  </toggles>")

    s = pol["sessions"]
    lines.append(
        f'  <sessions browser_idle_s={quoteattr(str(int(s["browser_idle_s"])))} '
        f'browser_abs_s={quoteattr(str(int(s["browser_abs_s"])))} '
        f'client_sliding_s={quoteattr(str(int(s["client_sliding_s"])))}/>'
    )

    p = pol["password"]
    lines.append(
        f'  <password min_length={quoteattr(str(int(p["min_length"])))} '
        f'lock_max={quoteattr(str(int(p["lock_max"])))} '
        f'lock_window_s={quoteattr(str(int(p["lock_window_s"])))}/>'
    )

    ld = pol["ldap"]
    lines.append(
        f'  <ldap url={quoteattr(str(ld["url"]))} '
        f'starttls={quoteattr("true" if ld["starttls"] else "false")} '
        f'bind_dn={quoteattr(str(ld["bind_dn"]))} '
        f'base_dn={quoteattr(str(ld["base_dn"]))} '
        f'user_filter={quoteattr(str(ld["user_filter"]))} '
        f'group_attr={quoteattr(str(ld["group_attr"]))} '
        f'default_role={quoteattr(str(ld["default_role"]))} '
        f'jit_provision={quoteattr("true" if ld["jit_provision"] else "false")} '
        f'verify_tls={quoteattr("true" if ld["verify_tls"] else "false")}>'
    )
    lines.append("    <group_map>")
    for grp, role in ld.get("group_map", {}).items():
        lines.append(f"      <entry group={quoteattr(str(grp))} role={quoteattr(str(role))}/>")
    lines.append("    </group_map>")
    lines.append("  </ldap>")

    sm = pol["saml"]
    lines.append(
        f'  <saml sp_base_url={quoteattr(str(sm["sp_base_url"]))} '
        f'idp_entity_id={quoteattr(str(sm["idp_entity_id"]))} '
        f'idp_sso_url={quoteattr(str(sm["idp_sso_url"]))} '
        f'idp_slo_url={quoteattr(str(sm["idp_slo_url"]))} '
        f'role_attr={quoteattr(str(sm["role_attr"]))} '
        f'default_role={quoteattr(str(sm["default_role"]))} '
        f'jit_provision={quoteattr("true" if sm["jit_provision"] else "false")}>'
    )
    lines.append(f"    <idp_cert_pem>{escape(str(sm.get('idp_cert_pem', '')))}</idp_cert_pem>")
    lines.append("    <group_map>")
    for grp, role in sm.get("group_map", {}).items():
        lines.append(f"      <entry group={quoteattr(str(grp))} role={quoteattr(str(role))}/>")
    lines.append("    </group_map>")
    lines.append("  </saml>")
    lines.append("</enterprise-policy>")
    return ("\n".join(lines) + "\n").encode("utf-8")


def load_policy() -> dict:
    """Load the enterprise policy from XML, returning defaults if not yet created."""
    path = _policy_path()
    with _locked_file(path):
        return _parse_xml(_read_nofollow(path))


def save_policy(data: dict) -> dict:
    """Normalize and atomically persist the enterprise policy."""
    pol = _normalize_policy(data)
    path = _policy_path()
    with _locked_file(path):
        _write_atomic(path, _render_xml(pol), 0o600)
    return pol


def get_toggles() -> dict[str, bool]:
    """Return the current boolean feature toggles."""
    pol = load_policy()
    return {key: bool(pol["toggles"].get(key, DEFAULTS["toggles"][key])) for key in TOGGLE_KEYS}


def validate_policy_change(new_policy: dict, active_users: list[dict]) -> None:
    """Validate that a proposed policy does not lock out all administrators or disable all auth methods."""
    pol = _normalize_policy(new_policy)
    toggles = pol["toggles"]
    active_admins = [
        u for u in active_users
        if u.get("active", True) and u.get("role") == "administrator"
    ]

    if toggles["multiuser_login"]:
        if not active_admins:
            raise PolicyError("Cannot enable multiuser_login without at least one active administrator")
        if not (toggles["local_accounts"] or toggles["ldap_sso"] or toggles["saml_sso"]):
            raise PolicyError(
                "Cannot enable multiuser_login when all authentication methods (local_accounts, ldap_sso, saml_sso) are disabled"
            )

    if not toggles["setup_code_fallback"] and not active_admins:
        raise PolicyError("Cannot disable setup_code_fallback without at least one active administrator")
