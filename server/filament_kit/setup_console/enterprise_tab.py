"""Enterprise authentication, RBAC, SSO (LDAP / SAML), certificate management, and activity routes.

Setup Console routes (mounted under /setup/api by setup_console/__init__.py):
    GET  /enterprise/overview                 module state, toggles, policy, counts, cert summary, RBAC schema
    POST /enterprise/toggles                  update feature toggles & session/password policy
    GET  /enterprise/users                    list local/enterprise users
    POST /enterprise/users                    create user
    POST /enterprise/users/{user_id}          update user
    POST /enterprise/users/{user_id}/password set user password
    POST /enterprise/users/{user_id}/delete   delete user
    GET  /enterprise/roles                    list custom & built-in roles
    POST /enterprise/roles                    create or update custom role
    POST /enterprise/roles/{role_id}/delete   delete custom role
    GET  /enterprise/sessions                 list active sessions
    POST /enterprise/sessions/{session_id}/revoke  revoke an active session
    GET  /enterprise/sso                      redacted LDAP & SAML configuration
    POST /enterprise/ldap                     save LDAP configuration
    POST /enterprise/ldap/test                test LDAP connection
    POST /enterprise/saml                     save SAML configuration
    POST /enterprise/saml/idp-metadata        import SAML IdP metadata
    POST /enterprise/saml/sp-key              generate SAML SP keypair
    GET  /enterprise/certs                    inspect Hub TLS certificate
    POST /enterprise/certs/renew              renew self-signed Hub TLS certificate
    POST /enterprise/certs/csr                generate CSR
    POST /enterprise/certs/upload             upload PEM cert / key / chain
    POST /enterprise/certs/ca                 install or remove Enterprise CA bundle
    GET  /enterprise/activity                 filtered enterprise/auth activity events

Root auth routes (mounted at app root by setup_page.py via auth_routes(page)):
    GET  /auth/login                          minimal CSP-safe login page
    POST /auth/login                          local or LDAP login (sets fk_session cookie)
    POST /auth/logout                         clear fk_session cookie and revoke session
    GET  /auth/me                             current authenticated session user
    GET  /auth/client-login                   PKCE interactive login for Android / PC-Sync
    GET  /auth/saml/metadata.xml              SP SAML metadata
    GET  /auth/saml/login                     initiate SAML SSO redirect
    POST /auth/saml/acs                       SAML Assertion Consumer Service
    POST /auth/saml/logout                    SAML logout
"""
from __future__ import annotations

import base64
import logging
import re
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse

import anyio
import defusedxml.ElementTree as DET
from defusedxml import DefusedXmlException
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from .. import alerts_api, config, demo, event_sink, events, http_body, modules, redact
from ..enterprise import certs, ldap_auth, policy, rbac, saml_auth, sessions, users

log = logging.getLogger(__name__)

SESSION_COOKIE = sessions.SESSION_COOKIE
_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,64}")
_ACCESS_LEVELS = frozenset({"NA", "RO", "RW"})
_VALID_PROVIDERS = frozenset({"local", "ldap"})
_MAX_PEM_BYTES = 256 * 1024
_MAX_SAML_BYTES = 512 * 1024

_SAML_PKCE_LOCK = threading.Lock()
_SAML_PKCE_STATE: dict[str, tuple[str, str, str, str, float]] = {}


def verify_session(token: str) -> dict | None:
    """Validate a browser session token for `setup_page.py`."""
    if not isinstance(token, str) or not token:
        return None
    return sessions.validate_session(token)


def _rbac_schema() -> dict:
    """Return RBAC sections and built-in roles."""
    return {
        "sections": rbac.SECTIONS,
        "builtin_roles": rbac.BUILTIN_ROLES,
    }


def _emit(kind: str, subject: dict, data: dict | None = None, *, actor: dict | None = None) -> None:
    """Emit an event safely; never raise into a route handler."""
    try:
        event_sink.emit(kind, subject, data, actor=actor)
    except Exception:  # noqa: BLE001
        log.debug("enterprise event %s skipped", kind, exc_info=True)


def _str_field(body: dict, key: str, *, required: bool = False, max_len: int = 256) -> str | None:
    from ..setup_page import Refused
    val = body.get(key)
    if val is None:
        if required:
            raise Refused(400, f"{key} is required", field=key)
        return None
    if not isinstance(val, str):
        raise Refused(400, f"{key} must be a string", field=key)
    cleaned = val.strip()
    if required and not cleaned:
        raise Refused(400, f"{key} must not be empty", field=key)
    if len(cleaned) > max_len:
        raise Refused(400, f"{key} is too long", field=key)
    return cleaned


def _bool_field(body: dict, key: str) -> bool | None:
    from ..setup_page import Refused
    if key not in body or body[key] is None:
        return None
    val = body[key]
    if not isinstance(val, bool):
        raise Refused(400, f"{key} must be true or false", field=key)
    return val


def _int_field(raw, name: str, *, minimum: int | None = None) -> int | None:
    from ..setup_page import Refused
    if raw in (None, ""):
        return None
    if isinstance(raw, bool):
        raise Refused(400, f"{name} must be a number", field=name)
    try:
        val = int(raw)
    except (TypeError, ValueError):
        raise Refused(400, f"{name} must be a number", field=name) from None
    if minimum is not None and val < minimum:
        raise Refused(400, f"{name} must be at least {minimum}", field=name)
    return val


def _valid_id(raw: str, label: str = "id") -> str:
    from ..setup_page import Refused
    if not isinstance(raw, str) or not _ID_RE.fullmatch(raw):
        raise Refused(400, f"invalid {label}", field=label)
    return raw


def _valid_perm_map(raw, field: str) -> dict | None:
    from ..setup_page import Refused
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise Refused(400, f"{field} must be an object", field=field)
    for k, v in raw.items():
        ok_val = (isinstance(v, str) and v.upper() in _ACCESS_LEVELS) or (
            isinstance(v, int) and not isinstance(v, bool) and v in (0, 1, 2)
        )
        if not isinstance(k, str) or not ok_val:
            raise Refused(400, f"{field} must map keys to NA, RO, or RW", field=field)
    return raw


def _set_session_cookie(resp: Response, token: str, max_age: int | None = None) -> None:
    kw: dict = {"httponly": True, "secure": True, "samesite": "lax", "path": "/"}
    if isinstance(max_age, int) and max_age > 0:
        kw["max_age"] = max_age
    resp.set_cookie(SESSION_COOKIE, token, **kw)


def _clear_session_cookie(resp: Response) -> None:
    resp.delete_cookie(SESSION_COOKIE, path="/", httponly=True, secure=True, samesite="lax")


def _safe_redirect_uri(uri: str | None) -> bool:
    """Allow only `spoolwarden://` and `http://127.0.0.1:<port>/...` or `http://localhost:<port>/...`."""
    if not isinstance(uri, str) or not uri.strip():
        return False
    cleaned = uri.strip()
    if any(ch.isspace() or ord(ch) < 0x20 for ch in cleaned):
        return False
    parsed = urlparse(cleaned)
    scheme = (parsed.scheme or "").lower()
    if scheme == "spoolwarden":
        return True
    if scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost"):
        try:
            port = parsed.port
        except ValueError:
            return False
        return port is not None and 1 <= port <= 65535
    return False


def _safe_relay_state(relay_state: object) -> str:
    """Validate SAML RelayState as a strict same-origin relative path."""
    if isinstance(relay_state, str):
        rs = relay_state.strip()
        if rs.startswith("/") and not rs.startswith("//") and not rs.startswith("/\\"):
            return rs
    return "/setup"


def _store_saml_pkce(req_id: str, redirect_uri: str, code_challenge: str, state: str, client_type: str) -> None:
    now = time.time()
    exp = now + saml_auth.REQUEST_TTL_S
    with _SAML_PKCE_LOCK:
        expired = [k for k, v in _SAML_PKCE_STATE.items() if v[4] <= now]
        for k in expired:
            del _SAML_PKCE_STATE[k]
        _SAML_PKCE_STATE[req_id] = (redirect_uri, code_challenge, state, client_type, exp)


def _pop_saml_pkce(req_id: str) -> tuple[str, str, str, str] | None:
    if not req_id:
        return None
    now = time.time()
    with _SAML_PKCE_LOCK:
        entry = _SAML_PKCE_STATE.pop(req_id, None)
        expired = [k for k, v in _SAML_PKCE_STATE.items() if v[4] <= now]
        for k in expired:
            del _SAML_PKCE_STATE[k]
    if entry is None or entry[4] <= now:
        return None
    return entry[0], entry[1], entry[2], entry[3]


def _extract_in_response_to(saml_response_b64: str) -> str:
    try:
        raw_xml = base64.b64decode(saml_response_b64, validate=True)
        root = DET.fromstring(raw_xml)
    except (ValueError, DefusedXmlException):
        return ""
    return root.attrib.get("InResponseTo", "").strip()


def _redacted_sso(pol: dict | None = None) -> dict:
    if pol is None:
        pol = policy.load_policy()
    toggles = pol.get("toggles") if isinstance(pol.get("toggles"), dict) else {}
    ldap_cfg = pol.get("ldap") if isinstance(pol.get("ldap"), dict) else {}
    saml_cfg = pol.get("saml") if isinstance(pol.get("saml"), dict) else {}
    known = redact.known_values()
    return {
        "ldap": {
            "enabled": bool(toggles.get("ldap_sso", False)),
            "url": redact.text(str(ldap_cfg.get("url") or ""), known),
            "starttls": bool(ldap_cfg.get("starttls", False)),
            "bind_dn": str(ldap_cfg.get("bind_dn") or ""),
            "bind_password_set": bool(users.get_secret("ldap_bind_pw")),
            "base_dn": str(ldap_cfg.get("base_dn") or ""),
            "user_filter": str(ldap_cfg.get("user_filter") or ""),
            "group_attr": str(ldap_cfg.get("group_attr") or ""),
            "group_map": dict(ldap_cfg.get("group_map") or {}),
            "default_role": str(ldap_cfg.get("default_role") or "viewer"),
            "jit_provision": bool(ldap_cfg.get("jit_provision", True)),
            "verify_tls": bool(ldap_cfg.get("verify_tls", True)),
        },
        "saml": {
            "enabled": bool(toggles.get("saml_sso", False)),
            "sp_base_url": str(saml_cfg.get("sp_base_url") or ""),
            "idp_entity_id": str(saml_cfg.get("idp_entity_id") or ""),
            "idp_sso_url": redact.text(str(saml_cfg.get("idp_sso_url") or ""), known),
            "idp_slo_url": redact.text(str(saml_cfg.get("idp_slo_url") or ""), known),
            "idp_cert_set": bool(str(saml_cfg.get("idp_cert_pem") or "").strip()),
            "sp_key_set": bool(users.get_secret("saml_sp_key")),
            "role_attr": str(saml_cfg.get("role_attr") or "groups"),
            "group_map": dict(saml_cfg.get("group_map") or {}),
            "default_role": str(saml_cfg.get("default_role") or "viewer"),
            "jit_provision": bool(saml_cfg.get("jit_provision", True)),
        },
    }


def _external_user_login(source: str, ext: dict, cfg: dict) -> dict:
    from ..setup_page import Refused
    if cfg.get("jit_provision", True):
        return users.upsert_external_user(
            source,
            ext["external_id"],
            ext["username"],
            ext["role"],
            display_name=ext.get("display_name", ""),
            email=ext.get("email", ""),
        )
    existing = users.get_by_username(ext["username"])
    if existing is None or not existing.get("active", True):
        raise Refused(401, "Account not provisioned or disabled")
    return existing


def routes(page) -> list[tuple]:
    from ..setup_page import Refused

    g = lambda fn: page.guard(fn, write=False)  # noqa: E731
    w = lambda fn: page.guard(fn, write=True)  # noqa: E731

    # ---------- overview & toggles ----------
    async def overview(request, body):
        cfg = config.load()
        on = modules.enabled(cfg)
        toggles = await anyio.to_thread.run_sync(policy.get_toggles)
        pol = await anyio.to_thread.run_sync(policy.load_policy)
        counts = {"users": 0, "roles": 0, "sessions": 0}
        cert_summary = None
        if not demo.is_on():
            try:
                st = await page._helper({"op": "enterprise_get"})
                counts = {
                    "users": len(st.get("users") or []),
                    "roles": len(st.get("roles") or []),
                    "sessions": len(st.get("sessions") or []),
                }
                if toggles.get("cert_management"):
                    cert_summary = st.get("cert_info") or {"configured": False}
            except Refused:
                if toggles.get("cert_management"):
                    cert_summary = {"configured": False}
        elif toggles.get("cert_management"):
            cert_summary = {"configured": False, "demo": True}
        return {
            "enabled": "enterprise" in on,
            "toggles": toggles,
            "policy": pol,
            "counts": counts,
            "cert_summary": cert_summary,
            "rbac": _rbac_schema(),
        }

    async def set_toggles(request, body):
        demo.stop()
        toggles = body.get("toggles")
        pol_arg = body.get("policy")
        sections_arg = {k: body[k] for k in ("sessions", "password", "ldap", "saml") if k in body and body[k] is not None}
        if toggles is None and pol_arg is None and not sections_arg:
            raise Refused(400, "toggles or policy object required")
        if toggles is not None:
            if not isinstance(toggles, dict) or not all(
                isinstance(k, str) and isinstance(v, bool) for k, v in toggles.items()
            ):
                raise Refused(400, "toggles must map names to true or false", field="toggles")
        if pol_arg is not None and not isinstance(pol_arg, dict):
            raise Refused(400, "policy must be an object", field="policy")
        for k, v in sections_arg.items():
            if not isinstance(v, dict):
                raise Refused(400, f"{k} must be an object", field=k)
        req: dict = {"op": "enterprise_policy_set", "code": body.get("code", "")}
        if toggles is not None:
            req["toggles"] = toggles
        if pol_arg is not None:
            req["policy"] = pol_arg
        req.update(sections_arg)
        r = await page._helper(req)
        saved_pol = r.get("policy") or {}
        _emit("enterprise.policy_changed", {"type": "hub", "id": "enterprise"},
              {"toggles": toggles or {}, "policy_keys": sorted(sections_arg.keys())})
        page.audit(request, "enterprise policy updated")
        return {"ok": True, "toggles": saved_pol.get("toggles", toggles or {}), "policy": saved_pol}

    # ---------- users ----------
    async def users_list(request, body):
        if demo.is_on():
            return {"users": []}
        r = await page._helper({"op": "enterprise_user_op", "action": "list"})
        return {"users": r.get("users", [])}

    async def users_create(request, body):
        demo.stop()
        username = _str_field(body, "username", required=True, max_len=64)
        display_name = _str_field(body, "display_name", max_len=128)
        role = _str_field(body, "role", max_len=64)
        email = _str_field(body, "email", max_len=256)
        active = _bool_field(body, "active")
        if active is None:
            active = _bool_field(body, "enabled")
        password = _str_field(body, "password", max_len=256)
        r = await page._helper({
            "op": "enterprise_user_op",
            "action": "create",
            "code": body.get("code", ""),
            "username": username,
            "display_name": display_name,
            "role": role,
            "email": email,
            "active": True if active is None else active,
            "password": password,
        })
        _emit("enterprise.user_changed", {"type": "hub", "id": username}, {"action": "created", "username": username})
        page.audit(request, f"enterprise user {username} created")
        return {"ok": True, "user": r.get("user")}

    async def users_update(request, body):
        demo.stop()
        uid = _valid_id(request.path_params.get("user_id", ""), "user_id")
        display_name = _str_field(body, "display_name", max_len=128)
        role = _str_field(body, "role", max_len=64)
        email = _str_field(body, "email", max_len=256)
        active = _bool_field(body, "active")
        if active is None:
            active = _bool_field(body, "enabled")
        r = await page._helper({
            "op": "enterprise_user_op",
            "action": "update",
            "code": body.get("code", ""),
            "user_id": uid,
            "display_name": display_name,
            "role": role,
            "email": email,
            "active": active,
        })
        _emit("enterprise.user_changed", {"type": "hub", "id": uid}, {"action": "updated", "user_id": uid})
        page.audit(request, f"enterprise user {uid} updated")
        return {"ok": True, "user": r.get("user")}

    async def users_password(request, body):
        demo.stop()
        uid = _valid_id(request.path_params.get("user_id", ""), "user_id")
        password = _str_field(body, "password", required=True, max_len=256)
        await page._helper({
            "op": "enterprise_user_op",
            "action": "set_password",
            "code": body.get("code", ""),
            "user_id": uid,
            "password": password,
        })
        _emit("enterprise.user_changed", {"type": "hub", "id": uid}, {"action": "password_changed", "user_id": uid})
        page.audit(request, f"enterprise user {uid} password changed")
        return {"ok": True}

    async def users_delete(request, body):
        demo.stop()
        uid = _valid_id(request.path_params.get("user_id", ""), "user_id")
        await page._helper({
            "op": "enterprise_user_op",
            "action": "delete",
            "code": body.get("code", ""),
            "user_id": uid,
        })
        _emit("enterprise.user_changed", {"type": "hub", "id": uid}, {"action": "deleted", "user_id": uid})
        page.audit(request, f"enterprise user {uid} deleted")
        return {"ok": True}

    # ---------- roles ----------
    async def roles_list(request, body):
        schema = _rbac_schema()
        if demo.is_on():
            return {"roles": [], "builtin_roles": schema["builtin_roles"], "sections": schema["sections"]}
        r = await page._helper({"op": "enterprise_role_op", "action": "list"})
        return {
            "roles": r.get("roles", []),
            "builtin_roles": schema["builtin_roles"],
            "sections": schema["sections"],
        }

    async def roles_upsert(request, body):
        demo.stop()
        name = _str_field(body, "name", required=True, max_len=64)
        role_id = _str_field(body, "id", max_len=64) or _str_field(body, "role_id", max_len=64)
        if not role_id:
            role_id = re.sub(r"[^a-z0-9_-]+", "_", name.lower()).strip("_-")[:64]
            if not role_id:
                raise Refused(400, "id or name is required", field="id")
        sections = _valid_perm_map(body.get("sections"), "sections")
        if sections is None:
            raise Refused(400, "sections must map section ids to NA, RO, or RW", field="sections")
        raw_fns = body.get("functions") if "functions" in body else body.get("overrides")
        functions = _valid_perm_map(raw_fns, "functions")
        existing = await anyio.to_thread.run_sync(rbac.get_role, role_id)
        action = "update" if existing is not None and not existing.get("builtin") else "create"
        r = await page._helper({
            "op": "enterprise_role_op",
            "action": action,
            "code": body.get("code", ""),
            "id": role_id,
            "name": name,
            "sections": sections,
            "functions": functions or {},
        })
        page.audit(request, f"enterprise role {name} saved")
        return {"ok": True, "role": r.get("role")}

    async def roles_delete(request, body):
        demo.stop()
        rid = _valid_id(request.path_params.get("role_id", ""), "role_id")
        await page._helper({
            "op": "enterprise_role_op",
            "action": "delete",
            "code": body.get("code", ""),
            "id": rid,
        })
        page.audit(request, f"enterprise role {rid} deleted")
        return {"ok": True}

    # ---------- sessions ----------
    async def sessions_list(request, body):
        if demo.is_on():
            return {"sessions": []}
        sess_list = await anyio.to_thread.run_sync(sessions.list_sessions)
        return {"sessions": sess_list}

    async def sessions_revoke(request, body):
        demo.stop()
        sid = _valid_id(request.path_params.get("session_id", ""), "session_id")
        await anyio.to_thread.run_sync(sessions.revoke_session, sid)
        page.audit(request, f"enterprise session {sid} revoked")
        return {"ok": True}

    # ---------- SSO (LDAP & SAML) ----------
    async def sso_get(request, body):
        return await anyio.to_thread.run_sync(_redacted_sso)

    async def ldap_save(request, body):
        demo.stop()
        enabled = _bool_field(body, "enabled")
        url = _str_field(body, "url", max_len=512)
        starttls = _bool_field(body, "starttls")
        bind_dn = _str_field(body, "bind_dn", max_len=512)
        bind_password = _str_field(body, "bind_password", max_len=512)
        base_dn = _str_field(body, "base_dn", max_len=512)
        user_filter = _str_field(body, "user_filter", max_len=512)
        group_attr = _str_field(body, "group_attr", max_len=128)
        default_role = _str_field(body, "default_role", max_len=64)
        jit_provision = _bool_field(body, "jit_provision")
        verify_tls = _bool_field(body, "verify_tls")
        group_map = body.get("group_map")
        if group_map is not None and (
            not isinstance(group_map, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in group_map.items())
        ):
            raise Refused(400, "group_map must map group names to role ids", field="group_map")
        if enabled is True and not url:
            raise Refused(400, "url is required when LDAP is enabled", field="url")
        ldap_patch: dict = {}
        for k, v in (
            ("url", url), ("starttls", starttls), ("bind_dn", bind_dn),
            ("base_dn", base_dn), ("user_filter", user_filter), ("group_attr", group_attr),
            ("default_role", default_role), ("jit_provision", jit_provision),
            ("verify_tls", verify_tls), ("group_map", group_map),
        ):
            if v is not None:
                ldap_patch[k] = v
        req: dict = {"op": "enterprise_policy_set", "code": body.get("code", "")}
        if ldap_patch:
            req["ldap"] = ldap_patch
        if enabled is not None:
            req["toggles"] = {"ldap_sso": enabled}
        if ldap_patch or enabled is not None:
            await page._helper(req)
        if bind_password is not None:
            secret_req: dict = {"op": "enterprise_secret_set", "code": body.get("code", ""), "name": "ldap_bind_pw"}
            if bind_password == "":
                secret_req["delete"] = True
            else:
                secret_req["value"] = bind_password
            await page._helper(secret_req)
        page.audit(request, "enterprise LDAP configuration saved")
        return {"ok": True}

    async def ldap_test(request, body):
        demo.stop()
        url = _str_field(body, "url", max_len=512)
        starttls = _bool_field(body, "starttls")
        bind_dn = _str_field(body, "bind_dn", max_len=512)
        bind_password = _str_field(body, "bind_password", max_len=512)
        base_dn = _str_field(body, "base_dn", max_len=512)
        user_filter = _str_field(body, "user_filter", max_len=512)
        verify_tls = _bool_field(body, "verify_tls")
        sample_user = _str_field(body, "sample_user", max_len=128) or ""

        def _run_test():
            pol = policy.load_policy()
            cfg = dict(pol.get("ldap") or {})
            if url is not None:
                cfg["url"] = url
            if starttls is not None:
                cfg["starttls"] = starttls
            if bind_dn is not None:
                cfg["bind_dn"] = bind_dn
            if base_dn is not None:
                cfg["base_dn"] = base_dn
            if user_filter is not None:
                cfg["user_filter"] = user_filter
            if verify_tls is not None:
                cfg["verify_tls"] = verify_tls
            pw = bind_password if bind_password is not None else (users.get_secret("ldap_bind_pw") or "")
            ca_pem = users.get_secret("ca_bundle") or ""
            return ldap_auth.test_connection(cfg, bind_pw=pw, sample_user=sample_user, ca_pem=ca_pem)

        try:
            details = await anyio.to_thread.run_sync(_run_test)
        except ldap_auth.LdapError as e:
            raise Refused(400, str(e)) from e
        return {"ok": True, "reachable": True, "details": details}

    async def saml_save(request, body):
        demo.stop()
        enabled = _bool_field(body, "enabled")
        sp_base_url = _str_field(body, "sp_base_url", max_len=512) or _str_field(body, "entity_id", max_len=512)
        idp_entity_id = _str_field(body, "idp_entity_id", max_len=512)
        idp_sso_url = _str_field(body, "idp_sso_url", max_len=512)
        idp_slo_url = _str_field(body, "idp_slo_url", max_len=512)
        idp_cert_pem = _str_field(body, "idp_cert_pem", max_len=_MAX_PEM_BYTES) or _str_field(body, "idp_cert", max_len=_MAX_PEM_BYTES)
        role_attr = _str_field(body, "role_attr", max_len=128)
        default_role = _str_field(body, "default_role", max_len=64)
        jit_provision = _bool_field(body, "jit_provision")
        group_map = body.get("group_map")
        if group_map is not None and (
            not isinstance(group_map, dict)
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in group_map.items())
        ):
            raise Refused(400, "group_map must map group names to role ids", field="group_map")
        if enabled is True and not idp_sso_url:
            raise Refused(400, "idp_sso_url is required when SAML is enabled", field="idp_sso_url")
        saml_patch: dict = {}
        for k, v in (
            ("sp_base_url", sp_base_url), ("idp_entity_id", idp_entity_id),
            ("idp_sso_url", idp_sso_url), ("idp_slo_url", idp_slo_url),
            ("idp_cert_pem", idp_cert_pem), ("role_attr", role_attr),
            ("default_role", default_role), ("jit_provision", jit_provision),
            ("group_map", group_map),
        ):
            if v is not None:
                saml_patch[k] = v
        req: dict = {"op": "enterprise_policy_set", "code": body.get("code", "")}
        if saml_patch:
            req["saml"] = saml_patch
        if enabled is not None:
            req["toggles"] = {"saml_sso": enabled}
        if saml_patch or enabled is not None:
            await page._helper(req)
        page.audit(request, "enterprise SAML configuration saved")
        return {"ok": True}

    async def saml_idp_metadata(request, body):
        demo.stop()
        metadata_xml = _str_field(body, "metadata_xml", max_len=_MAX_SAML_BYTES)
        if not metadata_xml:
            raise Refused(400, "metadata_xml is required", field="metadata_xml")
        try:
            parsed = await anyio.to_thread.run_sync(saml_auth.parse_idp_metadata, metadata_xml)
        except saml_auth.SamlError as e:
            raise Refused(400, str(e), field="metadata_xml") from e
        await page._helper({
            "op": "enterprise_policy_set",
            "code": body.get("code", ""),
            "saml": parsed,
        })
        page.audit(request, "enterprise SAML IdP metadata imported")
        return {"ok": True, "idp_entity_id": parsed.get("idp_entity_id"), "idp_sso_url": parsed.get("idp_sso_url")}

    async def saml_sp_key(request, body):
        demo.stop()
        await anyio.to_thread.run_sync(saml_auth.ensure_sp_keypair)
        page.audit(request, "enterprise SAML SP key generated")
        return {"ok": True, "sp_key_set": True}

    # ---------- certs ----------
    async def certs_get(request, body):
        if demo.is_on():
            return {"cert": None, "ca_installed": False, "demo": True}
        r = await page._helper({"op": "cert_info"})
        ca_installed = await anyio.to_thread.run_sync(lambda: bool(users.get_secret("ca_bundle")))
        return {"cert": r.get("cert"), "ca_installed": ca_installed}

    async def certs_renew(request, body):
        demo.stop()
        days = _int_field(body.get("days"), "days", minimum=1)
        extra_dns = body.get("extra_dns") if body.get("extra_dns") is not None else body.get("hosts")
        if extra_dns is not None and (
            not isinstance(extra_dns, list) or not all(isinstance(x, str) for x in extra_dns)
        ):
            raise Refused(400, "extra_dns must be a list of hostnames", field="extra_dns")
        r = await page._helper({
            "op": "cert_renew_self",
            "code": body.get("code", ""),
            "extra_dns": extra_dns or [],
            "days": days,
        })
        page.audit(request, "Hub TLS certificate renewed")
        return {"ok": True, "cert": r.get("cert")}

    async def certs_csr(request, body):
        demo.stop()
        cn = _str_field(body, "cn", required=True, max_len=253)
        sans = body.get("sans")
        if sans is not None and (
            not isinstance(sans, list) or not all(isinstance(x, str) for x in sans)
        ):
            raise Refused(400, "sans must be a list of hostnames", field="sans")
        key_type = _str_field(body, "key_type", max_len=16) or "ec256"
        r = await page._helper({
            "op": "cert_csr",
            "code": body.get("code", ""),
            "cn": cn,
            "sans": sans or [],
            "key_type": key_type,
        })
        page.audit(request, "Hub TLS CSR generated")
        return {"ok": True, "csr": r.get("csr", "")}

    async def certs_upload(request, body):
        demo.stop()
        cert_pem = _str_field(body, "cert", required=True, max_len=_MAX_PEM_BYTES)
        key_pem = _str_field(body, "key", max_len=_MAX_PEM_BYTES)
        chain_pem = _str_field(body, "chain", max_len=_MAX_PEM_BYTES)
        r = await page._helper({
            "op": "cert_upload",
            "code": body.get("code", ""),
            "cert": cert_pem,
            "key": key_pem,
            "chain": chain_pem,
        })
        page.audit(request, "Hub TLS certificate uploaded")
        return {"ok": True, "cert": r.get("cert")}

    async def certs_ca(request, body):
        demo.stop()
        remove = _bool_field(body, "remove") is True
        ca_pem = _str_field(body, "ca_pem", max_len=_MAX_PEM_BYTES) or _str_field(body, "ca", max_len=_MAX_PEM_BYTES)
        if not remove and not ca_pem:
            raise Refused(400, "ca_pem is required", field="ca_pem")
        await page._helper({
            "op": "cert_ca_bundle",
            "code": body.get("code", ""),
            "delete": remove,
            "bundle": None if remove else ca_pem,
        })
        page.audit(request, "Enterprise CA bundle updated")
        return {"ok": True, "ca_installed": not remove}

    # ---------- activity ----------
    async def activity(request, body):
        q = request.query_params
        since = _int_field(q.get("since"), "since", minimum=0)
        newest = _int_field(q.get("newest"), "newest", minimum=1)
        if newest is not None and since is not None:
            raise Refused(400, "give since or newest, not both", field="newest")
        limit = newest or _int_field(q.get("limit"), "limit", minimum=1) or events.LIMIT_DEFAULT
        user = (q.get("user") or "").strip() or None
        category = (q.get("category") or "").strip() or None
        kinds = f"{category}.*" if category and "." not in category and "*" not in category else category
        window = {"since": since, "limit": limit} if since is not None else {"newest": limit}
        return await anyio.to_thread.run_sync(
            lambda: alerts_api.events_answer(**window, kinds=kinds, activity=True, actor=user)
        )

    return [
        ("/enterprise/overview", g(overview), "GET"),
        ("/enterprise/toggles", w(set_toggles), "POST"),
        ("/enterprise/users", g(users_list), "GET"),
        ("/enterprise/users", w(users_create), "POST"),
        ("/enterprise/users/{user_id}", w(users_update), "POST"),
        ("/enterprise/users/{user_id}/password", w(users_password), "POST"),
        ("/enterprise/users/{user_id}/delete", w(users_delete), "POST"),
        ("/enterprise/roles", g(roles_list), "GET"),
        ("/enterprise/roles", w(roles_upsert), "POST"),
        ("/enterprise/roles/{role_id}/delete", w(roles_delete), "POST"),
        ("/enterprise/sessions", g(sessions_list), "GET"),
        ("/enterprise/sessions/{session_id}/revoke", w(sessions_revoke), "POST"),
        ("/enterprise/sso", g(sso_get), "GET"),
        ("/enterprise/ldap", w(ldap_save), "POST"),
        ("/enterprise/ldap/test", w(ldap_test), "POST"),
        ("/enterprise/saml", w(saml_save), "POST"),
        ("/enterprise/saml/idp-metadata", w(saml_idp_metadata), "POST"),
        ("/enterprise/saml/sp-key", w(saml_sp_key), "POST"),
        ("/enterprise/certs", g(certs_get), "GET"),
        ("/enterprise/certs/renew", w(certs_renew), "POST"),
        ("/enterprise/certs/csr", w(certs_csr), "POST"),
        ("/enterprise/certs/upload", w(certs_upload), "POST"),
        ("/enterprise/certs/ca", w(certs_ca), "POST"),
        ("/enterprise/activity", g(activity), "GET"),
    ]


def auth_routes(page) -> list[Route]:
    """Root-level `/auth/*` routes mounted by setup_page.py, with LAN/Host/Origin guards."""
    from ..setup_page import BAD_HOST, HEADERS, HOME_ONLY, MAX_BODY, Refused

    def guard_auth(fn, *, check_origin: bool = False):
        async def route(request: Request):
            if not page._lan(request):
                return JSONResponse({"error": HOME_ONLY}, 403)
            if not page._host_ok(request):
                return JSONResponse({"error": BAD_HOST}, 403)
            if check_origin and request.method == "POST" and not page._origin_ok(request):
                return JSONResponse({"error": "bad origin"}, 403)
            try:
                return await fn(request)
            except Refused as e:
                return JSONResponse({"error": e.message, **e.extra}, e.status)
            except demo.DemoError as e:
                return JSONResponse({"error": e.message, "code": e.code}, 409)
            except Exception:  # noqa: BLE001
                log.exception("auth %s", request.url.path)
                return JSONResponse({"error": "internal error"}, 500)
        return route

    async def login_page(request: Request):
        body = (
            "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<title>Sign in</title></head>"
            "<body><main><h1>Sign in</h1>"
            "<p><a href='/setup#login'>Continue to Setup Console sign in</a></p>"
            "</main></body></html>"
        )
        return HTMLResponse(body, headers=HEADERS)

    async def login_post(request: Request):
        demo.stop()
        try:
            body = await http_body.read_json_object(request, MAX_BODY, allow_empty=True)
        except http_body.BodyTooLarge:
            raise Refused(413, http_body.too_large_text()) from None
        except http_body.BadBody as e:
            raise Refused(400, str(e)) from None
        username = _str_field(body, "username", required=True, max_len=64)
        password = _str_field(body, "password", required=True, max_len=256)
        provider = _str_field(body, "provider", max_len=16) or "local"
        if provider not in _VALID_PROVIDERS:
            raise Refused(400, "provider must be local or ldap", field="provider")

        if provider == "local":
            try:
                r = await page._helper({
                    "op": "enterprise_auth_local",
                    "username": username,
                    "password": password,
                })
            except Refused as e:
                if e.status in (401, 403):
                    _emit("auth.login_failed", {"type": "hub", "id": username},
                          {"username": username, "provider": provider})
                raise
            user = r.get("user")
            if not isinstance(user, dict):
                raise Refused(401, "invalid username or password")
        else:
            pol = await anyio.to_thread.run_sync(policy.load_policy)
            if not pol.get("toggles", {}).get("ldap_sso"):
                raise Refused(403, "LDAP authentication is disabled")
            bind_pw = await anyio.to_thread.run_sync(users.get_secret, "ldap_bind_pw") or ""
            ca_pem = await anyio.to_thread.run_sync(users.get_secret, "ca_bundle") or ""
            try:
                ext = await anyio.to_thread.run_sync(
                    lambda: ldap_auth.authenticate_ldap(username, password, pol["ldap"], bind_pw=bind_pw, ca_pem=ca_pem)
                )
            except ldap_auth.LdapError as e:
                _emit("auth.login_failed", {"type": "hub", "id": username},
                      {"username": username, "provider": provider})
                raise Refused(401, str(e)) from e
            try:
                user = await anyio.to_thread.run_sync(lambda: _external_user_login("ldap", ext, pol["ldap"]))
            except (users.UserError, users.AuthError) as e:
                raise Refused(401, str(e)) from e

        raw_token, sess = await anyio.to_thread.run_sync(
            lambda: sessions.create_session(
                user,
                kind="browser",
                auth_method=provider,
                client_ip=page._client_key(request),
                user_agent=request.headers.get("user-agent", ""),
            )
        )
        _emit("auth.login", {"type": "hub", "id": username}, {"username": username, "provider": provider})
        page.audit(request, f"user {username} signed in")
        resp = JSONResponse({"ok": True, "user": user}, headers={"Cache-Control": "no-store"})
        _set_session_cookie(resp, raw_token, int(sess.get("idle_s") or 28800))
        return resp

    async def logout_post(request: Request):
        token = request.cookies.get(SESSION_COOKIE)
        if not demo.is_on() and isinstance(token, str) and token:
            sess = await anyio.to_thread.run_sync(lambda: sessions.validate_session(token, touch=False))
            if sess:
                await anyio.to_thread.run_sync(sessions.revoke_session, sess["id"])
        resp = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        _clear_session_cookie(resp)
        return resp

    async def me_get(request: Request):
        token = request.cookies.get(SESSION_COOKIE)
        if demo.is_on() or not isinstance(token, str) or not token:
            return JSONResponse({"authenticated": False, "user": None}, headers={"Cache-Control": "no-store"})
        sess = await anyio.to_thread.run_sync(sessions.validate_session, token)
        if not sess:
            return JSONResponse({"authenticated": False, "user": None}, headers={"Cache-Control": "no-store"})
        return JSONResponse(
            {
                "authenticated": True,
                "user": {
                    "id": sess["user_id"],
                    "username": sess["username"],
                    "role": sess["role"],
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    async def client_login(request: Request):
        demo.stop()
        q = request.query_params
        redirect_uri = q.get("redirect_uri")
        code_challenge = q.get("code_challenge")
        state = q.get("state") or ""
        client_type = (q.get("client_type") or "client").strip()
        if not _safe_redirect_uri(redirect_uri):
            raise Refused(400, "invalid redirect_uri", field="redirect_uri")
        if not isinstance(code_challenge, str) or not code_challenge.strip():
            raise Refused(400, "code_challenge is required", field="code_challenge")
        token = request.cookies.get(SESSION_COOKIE)
        sess = await anyio.to_thread.run_sync(sessions.validate_session, token) if isinstance(token, str) and token else None
        if not sess:
            next_qs = urlencode({k: v for k, v in q.items() if isinstance(v, str)})
            return RedirectResponse(f"/auth/login?{next_qs}" if next_qs else "/auth/login", status_code=302)
        try:
            code = await anyio.to_thread.run_sync(
                lambda: sessions.create_pkce_code(
                    {"id": sess["user_id"], "username": sess["username"], "role": sess["role"]},
                    client_type,
                    code_challenge.strip(),
                    redirect_uri.strip(),
                )
            )
        except sessions.SessionError as e:
            raise Refused(400, str(e)) from e
        sep = "&" if "?" in redirect_uri else "?"
        params = urlencode({"code": code, **({"state": state} if state else {})})
        return RedirectResponse(f"{redirect_uri.strip()}{sep}{params}", status_code=302)

    async def saml_metadata(request: Request):
        demo.stop()
        try:
            pol = await anyio.to_thread.run_sync(policy.load_policy)
            sp_cert, _ = await anyio.to_thread.run_sync(saml_auth.ensure_sp_keypair)
            xml = saml_auth.sp_metadata_xml(pol["saml"], sp_cert)
        except saml_auth.SamlError as e:
            raise Refused(400, str(e)) from e
        return Response(xml, media_type="application/samlmetadata+xml", headers={"Cache-Control": "no-store"})

    async def saml_login(request: Request):
        demo.stop()
        q = request.query_params
        redirect_uri = q.get("redirect_uri")
        code_challenge = q.get("code_challenge")
        state = q.get("state") or ""
        client_type = (q.get("client_type") or "client").strip()
        if redirect_uri is not None or code_challenge is not None:
            if not _safe_redirect_uri(redirect_uri):
                raise Refused(400, "invalid redirect_uri", field="redirect_uri")
            if not isinstance(code_challenge, str) or not code_challenge.strip():
                raise Refused(400, "code_challenge is required", field="code_challenge")
        pol = await anyio.to_thread.run_sync(policy.load_policy)
        try:
            url, req_id = await anyio.to_thread.run_sync(saml_auth.build_authn_request, pol["saml"])
        except saml_auth.SamlError as e:
            raise Refused(400, str(e)) from e
        if redirect_uri and code_challenge:
            _store_saml_pkce(req_id, redirect_uri.strip(), code_challenge.strip(), state, client_type)
        next_path = (q.get("next") or "")[:256]
        if next_path:
            relay_state = _safe_relay_state(next_path)
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urlencode({'RelayState': relay_state})}"
        return RedirectResponse(url, status_code=302)

    async def saml_acs(request: Request):
        demo.stop()
        chunks: list[bytes] = []
        total = 0
        async for chunk in request.stream():
            total += len(chunk)
            if total > _MAX_SAML_BYTES:
                raise Refused(413, "SAML response too large")
            chunks.append(chunk)
        raw_body = b"".join(chunks)
        try:
            form_text = raw_body.decode("utf-8")
            form = parse_qs(form_text, keep_blank_values=True)
        except (UnicodeDecodeError, ValueError):
            raise Refused(400, "malformed form body") from None

        saml_vals = form.get("SAMLResponse") or []
        saml_response = saml_vals[0] if saml_vals else ""
        relay_vals = form.get("RelayState") or []
        relay_state = relay_vals[0] if relay_vals else ""
        if not isinstance(saml_response, str) or not saml_response.strip():
            raise Refused(400, "SAMLResponse is required", field="SAMLResponse")

        pol = await anyio.to_thread.run_sync(policy.load_policy)
        try:
            ext = await anyio.to_thread.run_sync(
                saml_auth.process_acs_response, saml_response.strip(), pol["saml"]
            )
        except saml_auth.SamlError as e:
            raise Refused(401, str(e)) from e

        in_response_to = _extract_in_response_to(saml_response.strip())
        pkce_entry = _pop_saml_pkce(in_response_to)

        try:
            user = await anyio.to_thread.run_sync(lambda: _external_user_login("saml", ext, pol["saml"]))
        except (users.UserError, users.AuthError) as e:
            raise Refused(401, str(e)) from e

        username = str(user.get("username") or "saml")
        _emit("auth.login", {"type": "hub", "id": username}, {"username": username, "provider": "saml"})
        page.audit(request, f"user {username} signed in via SAML")

        if pkce_entry is not None:
            redirect_uri, code_challenge, state, client_type = pkce_entry
            try:
                pkce_code = await anyio.to_thread.run_sync(
                    lambda: sessions.create_pkce_code(user, client_type, code_challenge, redirect_uri)
                )
            except sessions.SessionError as e:
                raise Refused(400, str(e)) from e
            sep = "&" if "?" in redirect_uri else "?"
            params = urlencode({"code": pkce_code, **({"state": state} if state else {})})
            return RedirectResponse(f"{redirect_uri}{sep}{params}", status_code=302)

        raw_token, sess = await anyio.to_thread.run_sync(
            lambda: sessions.create_session(
                user,
                kind="browser",
                auth_method="saml",
                client_ip=page._client_key(request),
                user_agent=request.headers.get("user-agent", ""),
            )
        )
        target = _safe_relay_state(relay_state)
        resp = RedirectResponse(target, status_code=302)
        _set_session_cookie(resp, raw_token, int(sess.get("idle_s") or 28800))
        return resp

    async def saml_logout(request: Request):
        demo.stop()
        token = request.cookies.get(SESSION_COOKIE)
        if isinstance(token, str) and token:
            sess = await anyio.to_thread.run_sync(lambda: sessions.validate_session(token, touch=False))
            if sess:
                await anyio.to_thread.run_sync(sessions.revoke_session, sess["id"])
        resp = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
        _clear_session_cookie(resp)
        return resp

    return [
        Route("/auth/login", guard_auth(login_page), methods=["GET"]),
        Route("/auth/login", guard_auth(login_post, check_origin=True), methods=["POST"]),
        Route("/auth/logout", guard_auth(logout_post, check_origin=True), methods=["POST"]),
        Route("/auth/me", guard_auth(me_get), methods=["GET"]),
        Route("/auth/client-login", guard_auth(client_login), methods=["GET"]),
        Route("/auth/saml/metadata.xml", guard_auth(saml_metadata), methods=["GET"]),
        Route("/auth/saml/login", guard_auth(saml_login), methods=["GET"]),
        Route("/auth/saml/acs", guard_auth(saml_acs, check_origin=False), methods=["POST"]),
        Route("/auth/saml/logout", guard_auth(saml_logout, check_origin=True), methods=["POST"]),
    ]
