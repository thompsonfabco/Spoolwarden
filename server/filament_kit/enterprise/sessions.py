"""Enterprise browser and client sessions plus single-use S256 PKCE codes (`state/enterprise/sessions.xml`)."""
from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import secrets
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import quoteattr

import defusedxml.ElementTree as DET
from defusedxml import DefusedXmlException

from .. import config, demo
from .policy import DEFAULTS, _locked_file, _read_nofollow, _write_atomic, load_policy

SESSION_COOKIE = "fk_session"
PKCE_TTL_S = 120


class SessionError(ValueError):
    """Raised on invalid session or PKCE operations."""


def _sessions_path() -> Path:
    return demo.where(config.paths().state / "enterprise" / "sessions.xml", "enterprise/sessions.xml")


def _sha256_hex(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _strip_ctrl(val: str | None) -> str:
    """Strip XML-illegal ASCII control characters (ord < 0x20)."""
    return "".join(ch for ch in str(val or "") if ord(ch) >= 0x20)


def _public_session(s: dict) -> dict:
    out = copy.deepcopy(s)
    out.pop("token_hash", None)
    return out


def _is_expired(s: dict, now: float) -> bool:
    if now >= s["expires_at"]:
        return True
    if s["kind"] == "browser" and s["idle_expires_at"] > 0 and now >= s["idle_expires_at"]:
        return True
    return False


def _parse_sessions_xml(raw: bytes) -> tuple[list[dict], list[dict]]:
    if not raw.strip():
        return [], []
    try:
        root = DET.fromstring(raw)
    except (DefusedXmlException, ET.ParseError) as exc:
        raise SessionError(f"Malformed sessions XML: {exc}") from exc
    if root.tag != "enterprise-sessions":
        raise SessionError("Invalid sessions root element")

    sessions: list[dict] = []
    for el in root.findall("session"):
        sessions.append(
            {
                "id": el.attrib.get("id", ""),
                "token_hash": el.attrib.get("token_hash", ""),
                "user_id": el.attrib.get("user_id", ""),
                "username": el.attrib.get("username", ""),
                "role": el.attrib.get("role", "viewer"),
                "kind": el.attrib.get("kind", "browser"),
                "auth_method": el.attrib.get("auth_method", "local"),
                "client_ip": el.attrib.get("client_ip", ""),
                "user_agent": el.attrib.get("user_agent", ""),
                "created_at": float(el.attrib.get("created_at", "0")),
                "last_seen_at": float(el.attrib.get("last_seen_at", "0")),
                "expires_at": float(el.attrib.get("expires_at", "0")),
                "idle_expires_at": float(el.attrib.get("idle_expires_at", "0")),
                "idle_s": int(el.attrib.get("idle_s", str(DEFAULTS["sessions"]["browser_idle_s"]))),
                "sliding_s": int(el.attrib.get("sliding_s", str(DEFAULTS["sessions"]["client_sliding_s"]))),
            }
        )

    pkce_codes: list[dict] = []
    for el in root.findall("pkce"):
        pkce_codes.append(
            {
                "code_hash": el.attrib.get("code_hash", ""),
                "user_id": el.attrib.get("user_id", ""),
                "username": el.attrib.get("username", ""),
                "role": el.attrib.get("role", "viewer"),
                "client_type": el.attrib.get("client_type", ""),
                "code_challenge": el.attrib.get("code_challenge", ""),
                "redirect_uri": el.attrib.get("redirect_uri", ""),
                "expires_at": float(el.attrib.get("expires_at", "0")),
            }
        )

    return sessions, pkce_codes


def _render_sessions_xml(sessions: list[dict], pkce_codes: list[dict]) -> bytes:
    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<enterprise-sessions version="1">']
    for s in sessions:
        lines.append(
            "  <session "
            f"id={quoteattr(s['id'])} "
            f"token_hash={quoteattr(s['token_hash'])} "
            f"user_id={quoteattr(s['user_id'])} "
            f"username={quoteattr(_strip_ctrl(s['username']))} "
            f"role={quoteattr(_strip_ctrl(s['role']))} "
            f"kind={quoteattr(s['kind'])} "
            f"auth_method={quoteattr(s['auth_method'])} "
            f"client_ip={quoteattr(_strip_ctrl(s['client_ip']))} "
            f"user_agent={quoteattr(_strip_ctrl(s['user_agent']))} "
            f"created_at={quoteattr(repr(float(s['created_at'])))} "
            f"last_seen_at={quoteattr(repr(float(s['last_seen_at'])))} "
            f"expires_at={quoteattr(repr(float(s['expires_at'])))} "
            f"idle_expires_at={quoteattr(repr(float(s['idle_expires_at'])))} "
            f"idle_s={quoteattr(str(int(s['idle_s'])))} "
            f"sliding_s={quoteattr(str(int(s['sliding_s'])))}/>"
        )
    for p in pkce_codes:
        lines.append(
            "  <pkce "
            f"code_hash={quoteattr(p['code_hash'])} "
            f"user_id={quoteattr(p['user_id'])} "
            f"username={quoteattr(_strip_ctrl(p['username']))} "
            f"role={quoteattr(_strip_ctrl(p['role']))} "
            f"client_type={quoteattr(_strip_ctrl(p['client_type']))} "
            f"code_challenge={quoteattr(_strip_ctrl(p['code_challenge']))} "
            f"redirect_uri={quoteattr(_strip_ctrl(p['redirect_uri']))} "
            f"expires_at={quoteattr(repr(float(p['expires_at'])))}/>"
        )
    lines.append("</enterprise-sessions>")
    return ("\n".join(lines) + "\n").encode("utf-8")


def create_session(
    user: dict,
    kind: str = "browser",
    auth_method: str = "local",
    client_ip: str = "",
    user_agent: str = "",
    policy: dict | None = None,
) -> tuple[str, dict]:
    """Create a new browser or client session and return `(raw_token, session_dict)`."""
    if kind not in ("browser", "client"):
        raise SessionError(f"Invalid session kind: {kind!r}")
    pol = policy if policy is not None else load_policy()
    scfg = pol.get("sessions", DEFAULTS["sessions"])
    idle_s = int(scfg.get("browser_idle_s", DEFAULTS["sessions"]["browser_idle_s"]))
    abs_s = int(scfg.get("browser_abs_s", DEFAULTS["sessions"]["browser_abs_s"]))
    sliding_s = int(scfg.get("client_sliding_s", DEFAULTS["sessions"]["client_sliding_s"]))

    now = time.time()
    raw_token = f"fks_{secrets.token_urlsafe(32)}"
    if kind == "browser":
        expires_at = now + abs_s
        idle_expires_at = min(expires_at, now + idle_s)
    else:
        expires_at = now + sliding_s
        idle_expires_at = 0.0

    session = {
        "id": f"ses_{secrets.token_hex(8)}",
        "token_hash": _sha256_hex(raw_token),
        "user_id": str(user.get("id") or user.get("user_id") or ""),
        "username": _strip_ctrl(user.get("username", "")),
        "role": _strip_ctrl(user.get("role", "viewer")),
        "kind": kind,
        "auth_method": str(auth_method or "local"),
        "client_ip": _strip_ctrl(client_ip)[:64],
        "user_agent": _strip_ctrl(user_agent)[:256],
        "created_at": now,
        "last_seen_at": now,
        "expires_at": expires_at,
        "idle_expires_at": idle_expires_at,
        "idle_s": idle_s,
        "sliding_s": sliding_s,
    }

    path = _sessions_path()
    with _locked_file(path):
        sessions, pkce_codes = _parse_sessions_xml(_read_nofollow(path))
        sessions = [s for s in sessions if not _is_expired(s, now)]
        pkce_codes = [p for p in pkce_codes if now < p["expires_at"]]
        sessions.append(session)
        _write_atomic(path, _render_sessions_xml(sessions, pkce_codes), 0o600)

    return raw_token, _public_session(session)


def validate_session(token: str, *, touch: bool = True) -> dict | None:
    """Validate a raw session token, optionally renewing idle/sliding expiry."""
    if not isinstance(token, str) or not token:
        return None
    target_hash = _sha256_hex(token)
    now = time.time()
    path = _sessions_path()

    with _locked_file(path):
        sessions, pkce_codes = _parse_sessions_xml(_read_nofollow(path))
        before_count = len(sessions)
        active: list[dict] = []
        matched: dict | None = None
        for s in sessions:
            if _is_expired(s, now):
                continue
            if hmac.compare_digest(s["token_hash"], target_hash):
                matched = s
            active.append(s)

        changed = len(active) != before_count
        if matched is not None and touch:
            matched["last_seen_at"] = now
            if matched["kind"] == "browser":
                matched["idle_expires_at"] = min(matched["expires_at"], now + matched["idle_s"])
            else:
                matched["expires_at"] = now + matched["sliding_s"]
            changed = True

        if changed:
            pkce_codes = [p for p in pkce_codes if now < p["expires_at"]]
            _write_atomic(path, _render_sessions_xml(active, pkce_codes), 0o600)

        return _public_session(matched) if matched is not None else None


def revoke_session(session_id: str) -> bool:
    """Revoke a single session by its `id`."""
    path = _sessions_path()
    with _locked_file(path):
        sessions, pkce_codes = _parse_sessions_xml(_read_nofollow(path))
        kept = [s for s in sessions if s["id"] != session_id]
        if len(kept) == len(sessions):
            return False
        _write_atomic(path, _render_sessions_xml(kept, pkce_codes), 0o600)
        return True


def revoke_user_sessions(user_id: str) -> int:
    """Revoke all sessions belonging to `user_id` and return the count removed."""
    path = _sessions_path()
    with _locked_file(path):
        sessions, pkce_codes = _parse_sessions_xml(_read_nofollow(path))
        kept = [s for s in sessions if s["user_id"] != user_id]
        removed = len(sessions) - len(kept)
        if removed:
            _write_atomic(path, _render_sessions_xml(kept, pkce_codes), 0o600)
        return removed


def revoke_all_sessions() -> int:
    """Revoke all active sessions atomically and return the count removed."""
    path = _sessions_path()
    with _locked_file(path):
        sessions, pkce_codes = _parse_sessions_xml(_read_nofollow(path))
        removed = len(sessions)
        if removed:
            _write_atomic(path, _render_sessions_xml([], pkce_codes), 0o600)
        return removed


def list_sessions(user_id: str | None = None) -> list[dict]:
    """Return active (non-expired) sessions, optionally filtered by `user_id`."""
    now = time.time()
    path = _sessions_path()
    with _locked_file(path):
        sessions, _ = _parse_sessions_xml(_read_nofollow(path))
    return [
        _public_session(s)
        for s in sessions
        if not _is_expired(s, now) and (user_id is None or s["user_id"] == user_id)
    ]


def create_pkce_code(user: dict, client_type: str, code_challenge: str, redirect_uri: str) -> str:
    """Create a 120-second single-use PKCE authorization code bound to `code_challenge` (S256)."""
    if not code_challenge or not isinstance(code_challenge, str):
        raise SessionError("code_challenge is required")
    if not redirect_uri or not isinstance(redirect_uri, str):
        raise SessionError("redirect_uri is required")

    raw_code = secrets.token_urlsafe(32)
    now = time.time()
    entry = {
        "code_hash": _sha256_hex(raw_code),
        "user_id": str(user.get("id") or user.get("user_id") or ""),
        "username": _strip_ctrl(user.get("username", "")),
        "role": _strip_ctrl(user.get("role", "viewer")),
        "client_type": _strip_ctrl(client_type),
        "code_challenge": _strip_ctrl(code_challenge.strip()),
        "redirect_uri": _strip_ctrl(redirect_uri),
        "expires_at": now + PKCE_TTL_S,
    }

    path = _sessions_path()
    with _locked_file(path):
        sessions, pkce_codes = _parse_sessions_xml(_read_nofollow(path))
        pkce_codes = [p for p in pkce_codes if now < p["expires_at"]]
        pkce_codes.append(entry)
        _write_atomic(path, _render_sessions_xml(sessions, pkce_codes), 0o600)

    return raw_code


def exchange_pkce_code(
    code: str,
    code_verifier: str,
    client_type: str | None = None,
) -> tuple[str, dict]:
    """Consume a single-use PKCE code, verify S256 `code_verifier`, and issue a client session."""
    if not code or not code_verifier:
        raise SessionError("code and code_verifier are required")
    try:
        target_hash = _sha256_hex(code)
    except (UnicodeEncodeError, AttributeError, TypeError) as exc:
        raise SessionError("invalid PKCE code or verifier") from exc
    now = time.time()
    path = _sessions_path()

    matched: dict | None = None
    with _locked_file(path):
        sessions, pkce_codes = _parse_sessions_xml(_read_nofollow(path))
        remaining: list[dict] = []
        for p in pkce_codes:
            if now >= p["expires_at"]:
                continue
            if matched is None and hmac.compare_digest(p["code_hash"], target_hash):
                matched = p
                continue  # single-use: always consume on lookup
            remaining.append(p)
        _write_atomic(path, _render_sessions_xml(sessions, remaining), 0o600)

    if matched is None:
        raise SessionError("Invalid or expired authorization code")
    if client_type is not None and matched["client_type"] != client_type:
        raise SessionError("client_type mismatch")

    try:
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    except (UnicodeEncodeError, AttributeError, TypeError) as exc:
        raise SessionError("invalid PKCE code or verifier") from exc
    if not hmac.compare_digest(expected, matched["code_challenge"]):
        raise SessionError("Invalid code_verifier")

    user_stub = {
        "id": matched["user_id"],
        "username": matched["username"],
        "role": matched["role"],
    }
    return create_session(user_stub, kind="client", auth_method="pkce")
