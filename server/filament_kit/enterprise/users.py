"""Enterprise user accounts, scrypt password hashing, per-user lockout, and secrets storage."""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import hmac
import secrets
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import defusedxml.ElementTree as DET
from defusedxml import DefusedXmlException

from .. import config, demo
from .policy import _locked_file, _read_nofollow, _write_atomic, load_policy

SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1, "maxmem": 2 ** 26}
SECRET_KEYS = frozenset({"ldap_bind_pw", "saml_sp_key", "saml_sp_cert", "ca_bundle"})
VALID_SOURCES = frozenset({"local", "ldap", "saml"})


class UserError(ValueError):
    """Raised on invalid user management operations."""


class AuthError(UserError):
    """Raised on authentication or lockout failures."""


def _users_path() -> Path:
    return demo.where(config.paths().etc / "enterprise" / "users.xml", "enterprise/users.xml")


def _secrets_path() -> Path:
    return demo.where(config.paths().etc / "enterprise" / "secrets.xml", "enterprise/secrets.xml")


def _now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _strip_ctrl(val: str | None) -> str:
    """Strip XML-illegal ASCII control characters (ord < 0x20)."""
    return "".join(ch for ch in str(val or "") if ord(ch) >= 0x20)


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, **SCRYPT).hex()
    return f"{salt.hex()} {digest}"


def _verify_password(password: str, stored: str) -> bool:
    if not stored or " " not in stored:
        return False
    try:
        salt_hex, digest_hex = stored.split(" ", 1)
        salt_b = bytes.fromhex(salt_hex)
    except ValueError:
        return False
    got = hashlib.scrypt(password.encode("utf-8"), salt=salt_b, **SCRYPT).hex()
    return hmac.compare_digest(got, digest_hex)


def _public_user(user: dict) -> dict:
    out = copy.deepcopy(user)
    out.pop("password_hash", None)
    out.pop("fails", None)
    return out


def _parse_users_xml(raw: bytes) -> list[dict]:
    if not raw.strip():
        return []
    try:
        root = DET.fromstring(raw)
    except (DefusedXmlException, ET.ParseError) as exc:
        raise UserError(f"Malformed users XML: {exc}") from exc
    if root.tag != "enterprise-users":
        raise UserError("Invalid users root element")

    users: list[dict] = []
    for el in root.findall("user"):
        fails: list[float] = []
        for f_el in el.findall("fail"):
            try:
                fails.append(float(f_el.attrib.get("t", "0")))
            except ValueError:
                continue
        users.append(
            {
                "id": el.attrib.get("id", ""),
                "username": el.attrib.get("username", ""),
                "role": el.attrib.get("role", "viewer"),
                "display_name": el.attrib.get("display_name", ""),
                "email": el.attrib.get("email", ""),
                "source": el.attrib.get("source", "local"),
                "external_id": el.attrib.get("external_id", ""),
                "active": el.attrib.get("active", "true").lower() == "true",
                "must_change_password": el.attrib.get("must_change_password", "false").lower() == "true",
                "password_hash": el.attrib.get("password_hash", ""),
                "created_at": el.attrib.get("created_at", ""),
                "updated_at": el.attrib.get("updated_at", ""),
                "last_login_at": el.attrib.get("last_login_at", ""),
                "fails": fails,
            }
        )
    return users


def _render_users_xml(users: list[dict]) -> bytes:
    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<enterprise-users version="1">']
    for u in users:
        attrs = (
            f"id={quoteattr(_strip_ctrl(u['id']))} "
            f"username={quoteattr(_strip_ctrl(u['username']))} "
            f"role={quoteattr(_strip_ctrl(u['role']))} "
            f"display_name={quoteattr(_strip_ctrl(u.get('display_name', '')))} "
            f"email={quoteattr(_strip_ctrl(u.get('email', '')))} "
            f"source={quoteattr(_strip_ctrl(u.get('source', 'local')))} "
            f"external_id={quoteattr(_strip_ctrl(u.get('external_id', '')))} "
            f"active={quoteattr('true' if u.get('active', True) else 'false')} "
            f"must_change_password={quoteattr('true' if u.get('must_change_password') else 'false')} "
            f"password_hash={quoteattr(_strip_ctrl(u.get('password_hash', '')))} "
            f"created_at={quoteattr(_strip_ctrl(u.get('created_at', '')))} "
            f"updated_at={quoteattr(_strip_ctrl(u.get('updated_at', '')))} "
            f"last_login_at={quoteattr(_strip_ctrl(u.get('last_login_at', '')))}"
        )
        fails = u.get("fails") or []
        if fails:
            lines.append(f"  <user {attrs}>")
            for t in fails:
                lines.append(f"    <fail t={quoteattr(repr(float(t)))}/>")
            lines.append("  </user>")
        else:
            lines.append(f"  <user {attrs}/>")
    lines.append("</enterprise-users>")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _active_admin_count(users: list[dict], *, exclude_id: str | None = None) -> int:
    return sum(
        1
        for u in users
        if u["id"] != exclude_id and u.get("active", True) and u.get("role") == "administrator"
    )


def list_users() -> list[dict]:
    """Return all user accounts (without password hashes)."""
    path = _users_path()
    with _locked_file(path):
        users = _parse_users_xml(_read_nofollow(path))
    return [_public_user(u) for u in users]


def get_user(user_id: str) -> dict | None:
    """Return a user account by id, or None."""
    path = _users_path()
    with _locked_file(path):
        users = _parse_users_xml(_read_nofollow(path))
    for u in users:
        if u["id"] == user_id:
            return _public_user(u)
    return None


def get_by_username(username: str) -> dict | None:
    """Return a user account by case-insensitive username, or None."""
    clean = (username or "").strip().lower()
    if not clean:
        return None
    path = _users_path()
    with _locked_file(path):
        users = _parse_users_xml(_read_nofollow(path))
    for u in users:
        if u["username"].lower() == clean:
            return _public_user(u)
    return None


def create_user(
    username: str,
    role: str,
    password: str | None = None,
    display_name: str = "",
    email: str = "",
    source: str = "local",
    external_id: str = "",
    must_change_password: bool = False,
) -> dict:
    """Create a new local or external user account."""
    clean_user = _strip_ctrl(username).strip()
    if not clean_user:
        raise UserError("Username cannot be empty")
    if source not in VALID_SOURCES:
        raise UserError(f"Invalid user source: {source!r}")
    clean_role = _strip_ctrl(role).strip()
    if not clean_role:
        raise UserError("Role cannot be empty")

    pw_hash = ""
    if password is not None:
        min_len = int(load_policy()["password"].get("min_length", 12))
        if not isinstance(password, str) or len(password) < min_len:
            raise UserError(f"Password must be at least {min_len} characters")
        pw_hash = _hash_password(password)

    now = _now_iso()
    path = _users_path()
    with _locked_file(path):
        users = _parse_users_xml(_read_nofollow(path))
        if any(u["username"].lower() == clean_user.lower() for u in users):
            raise UserError(f"Username {clean_user!r} already exists")
        user = {
            "id": f"usr_{secrets.token_hex(8)}",
            "username": clean_user,
            "role": clean_role,
            "display_name": _strip_ctrl(display_name),
            "email": _strip_ctrl(email),
            "source": source,
            "external_id": _strip_ctrl(external_id),
            "active": True,
            "must_change_password": bool(must_change_password),
            "password_hash": pw_hash,
            "created_at": now,
            "updated_at": now,
            "last_login_at": "",
            "fails": [],
        }
        users.append(user)
        _write_atomic(path, _render_users_xml(users), 0o600)
        return _public_user(user)


def update_user(
    user_id: str,
    *,
    role: str | None = None,
    display_name: str | None = None,
    email: str | None = None,
    active: bool | None = None,
    must_change_password: bool | None = None,
) -> dict:
    """Update user metadata or role; refuses demoting or disabling the last active administrator."""
    path = _users_path()
    with _locked_file(path):
        users = _parse_users_xml(_read_nofollow(path))
        target = next((u for u in users if u["id"] == user_id), None)
        if target is None:
            raise UserError(f"User {user_id!r} not found")

        new_role = str(role).strip() if role is not None else target["role"]
        new_active = bool(active) if active is not None else target["active"]

        was_active_admin = target["active"] and target["role"] == "administrator"
        will_be_active_admin = new_active and new_role == "administrator"
        if was_active_admin and not will_be_active_admin and _active_admin_count(users, exclude_id=user_id) == 0:
            raise UserError("Cannot demote or disable the last active administrator")

        target["role"] = new_role
        target["active"] = new_active
        if display_name is not None:
            target["display_name"] = str(display_name)
        if email is not None:
            target["email"] = str(email)
        if must_change_password is not None:
            target["must_change_password"] = bool(must_change_password)
        target["updated_at"] = _now_iso()

        _write_atomic(path, _render_users_xml(users), 0o600)
    if active is not None and not new_active:
        from . import sessions

        sessions.revoke_user_sessions(user_id)
    return _public_user(target)


def delete_user(user_id: str) -> None:
    """Delete a user account; refuses deleting the last active administrator."""
    path = _users_path()
    with _locked_file(path):
        users = _parse_users_xml(_read_nofollow(path))
        target = next((u for u in users if u["id"] == user_id), None)
        if target is None:
            raise UserError(f"User {user_id!r} not found")
        if target["active"] and target["role"] == "administrator" and _active_admin_count(users, exclude_id=user_id) == 0:
            raise UserError("Cannot delete the last active administrator")
        users = [u for u in users if u["id"] != user_id]
        _write_atomic(path, _render_users_xml(users), 0o600)
    from . import sessions

    sessions.revoke_user_sessions(user_id)


def set_password(user_id: str, password: str, min_length: int = 12) -> None:
    """Set or replace a user's local password."""
    if not isinstance(password, str) or len(password) < int(min_length):
        raise UserError(f"Password must be at least {int(min_length)} characters")
    pw_hash = _hash_password(password)
    path = _users_path()
    with _locked_file(path):
        users = _parse_users_xml(_read_nofollow(path))
        target = next((u for u in users if u["id"] == user_id), None)
        if target is None:
            raise UserError(f"User {user_id!r} not found")
        target["password_hash"] = pw_hash
        target["must_change_password"] = False
        target["fails"] = []
        target["updated_at"] = _now_iso()
        _write_atomic(path, _render_users_xml(users), 0o600)


def authenticate_local(username: str, password: str, policy: dict | None = None) -> dict:
    """Authenticate a local user with per-user lockout enforcement."""
    pol = policy if policy is not None else load_policy()
    pw_cfg = pol.get("password", {})
    lock_max = int(pw_cfg.get("lock_max", 5))
    lock_window = int(pw_cfg.get("lock_window_s", 600))

    clean_user = (username or "").strip().lower()
    if not clean_user or not isinstance(password, str) or not password:
        raise AuthError("Invalid username or password")

    path = _users_path()
    with _locked_file(path):
        users = _parse_users_xml(_read_nofollow(path))
        target = next((u for u in users if u["username"].lower() == clean_user), None)
        if target is None or not target["active"] or target["source"] != "local" or not target["password_hash"]:
            raise AuthError("Invalid username or password")

        now = time.time()
        fails = [t for t in target.get("fails", []) if t > now - lock_window]
        target["fails"] = fails
        if len(fails) >= lock_max:
            _write_atomic(path, _render_users_xml(users), 0o600)
            raise AuthError("Account temporarily locked due to too many failed attempts")

        if not _verify_password(password, target["password_hash"]):
            fails.append(now)
            target["fails"] = fails
            _write_atomic(path, _render_users_xml(users), 0o600)
            raise AuthError("Invalid username or password")

        target["fails"] = []
        target["last_login_at"] = _now_iso()
        _write_atomic(path, _render_users_xml(users), 0o600)
        return _public_user(target)


def upsert_external_user(
    source: str,
    external_id: str,
    username: str,
    role: str,
    display_name: str = "",
    email: str = "",
) -> dict:
    """Create or update a JIT-provisioned external (LDAP/SAML) user."""
    if source not in ("ldap", "saml"):
        raise UserError(f"Invalid external source: {source!r}")
    clean_user = _strip_ctrl(username).strip()
    if not clean_user:
        raise UserError("Username cannot be empty")
    clean_ext_id = _strip_ctrl(external_id)
    clean_role = _strip_ctrl(role).strip()
    clean_display = _strip_ctrl(display_name)
    clean_email = _strip_ctrl(email)

    now = _now_iso()
    path = _users_path()
    with _locked_file(path):
        users = _parse_users_xml(_read_nofollow(path))
        target = next(
            (
                u
                for u in users
                if u["source"] == source
                and ((clean_ext_id and u["external_id"] == clean_ext_id) or u["username"].lower() == clean_user.lower())
            ),
            None,
        )
        if target is not None:
            if not target["active"]:
                raise AuthError("Account is disabled")
            target["username"] = clean_user
            target["external_id"] = clean_ext_id or target["external_id"]
            target["role"] = clean_role or target["role"]
            if clean_display:
                target["display_name"] = clean_display
            if clean_email:
                target["email"] = clean_email
            target["updated_at"] = now
            target["last_login_at"] = now
        else:
            if any(u["username"].lower() == clean_user.lower() for u in users):
                raise UserError(f"Username {clean_user!r} already exists for another account source")
            target = {
                "id": f"usr_{secrets.token_hex(8)}",
                "username": clean_user,
                "role": clean_role or "viewer",
                "display_name": clean_display,
                "email": clean_email,
                "source": source,
                "external_id": clean_ext_id,
                "active": True,
                "must_change_password": False,
                "password_hash": "",
                "created_at": now,
                "updated_at": now,
                "last_login_at": now,
                "fails": [],
            }
            users.append(target)

        _write_atomic(path, _render_users_xml(users), 0o600)
        return _public_user(target)


def _parse_secrets_xml(raw: bytes) -> dict[str, str]:
    if not raw.strip():
        return {}
    try:
        root = DET.fromstring(raw)
    except (DefusedXmlException, ET.ParseError) as exc:
        raise UserError(f"Malformed secrets XML: {exc}") from exc
    if root.tag != "enterprise-secrets":
        raise UserError("Invalid secrets root element")
    out: dict[str, str] = {}
    for el in root.findall("secret"):
        key = el.attrib.get("key", "")
        if key in SECRET_KEYS:
            out[key] = el.text or ""
    return out


def _render_secrets_xml(data: dict[str, str]) -> bytes:
    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<enterprise-secrets version="1">']
    for key in sorted(data):
        lines.append(f"  <secret key={quoteattr(key)}>{escape(data[key])}</secret>")
    lines.append("</enterprise-secrets>")
    return ("\n".join(lines) + "\n").encode("utf-8")


def get_secret(key: str) -> str | None:
    """Return a stored enterprise secret by key, or None."""
    if key not in SECRET_KEYS:
        raise UserError(f"Unknown secret key: {key!r}")
    path = _secrets_path()
    with _locked_file(path):
        data = _parse_secrets_xml(_read_nofollow(path))
    return data.get(key)


def set_secret(key: str, value: str) -> None:
    """Store or overwrite an enterprise secret."""
    if key not in SECRET_KEYS:
        raise UserError(f"Unknown secret key: {key!r}")
    path = _secrets_path()
    with _locked_file(path):
        data = _parse_secrets_xml(_read_nofollow(path))
        data[key] = str(value)
        _write_atomic(path, _render_secrets_xml(data), 0o600)


def delete_secret(key: str) -> None:
    """Delete an enterprise secret if present."""
    if key not in SECRET_KEYS:
        raise UserError(f"Unknown secret key: {key!r}")
    path = _secrets_path()
    with _locked_file(path):
        data = _parse_secrets_xml(_read_nofollow(path))
        if key in data:
            del data[key]
            _write_atomic(path, _render_secrets_xml(data), 0o600)
