"""LDAP / Active Directory authentication and connection testing (lazy `ldap3` import)."""
from __future__ import annotations

import ssl
from urllib.parse import urlparse

from .. import demo


class LdapError(Exception):
    """Raised when LDAP configuration, connection, or authentication fails."""


def _escape_filter_chars(value: str) -> str:
    """Escape RFC 4515 LDAP search filter special characters."""
    out: list[str] = []
    for ch in value:
        if ch == "\\":
            out.append(r"\5c")
        elif ch == "*":
            out.append(r"\2a")
        elif ch == "(":
            out.append(r"\28")
        elif ch == ")":
            out.append(r"\29")
        elif ch == "\x00":
            out.append(r"\00")
        else:
            out.append(ch)
    return "".join(out)


def _import_ldap3():
    try:
        import ldap3
        from ldap3.core.exceptions import LDAPException
        return ldap3, LDAPException
    except ImportError as exc:
        raise LdapError("ldap3 package is not installed") from exc


def _build_server(ldap3, cfg: dict, ca_pem: str = ""):
    url = (cfg.get("url") or "").strip()
    if not url:
        raise LdapError("LDAP server URL is not configured")
    parsed = urlparse(url)
    use_ssl = parsed.scheme.lower() == "ldaps"
    verify_tls = bool(cfg.get("verify_tls", True))
    tls = ldap3.Tls(
        validate=ssl.CERT_REQUIRED if verify_tls else ssl.CERT_NONE,
        ca_certs_data=ca_pem.strip() or None,
    )
    return ldap3.Server(url, use_ssl=use_ssl, tls=tls, get_info=ldap3.NONE), use_ssl


def _open_connection(ldap3, server, use_ssl: bool, starttls: bool, bind_dn: str = "", bind_pw: str = ""):
    kwargs: dict = {"auto_bind": False}
    if bind_dn:
        kwargs["user"] = bind_dn
        kwargs["password"] = bind_pw
    conn = ldap3.Connection(server, **kwargs)
    conn.open()
    if starttls and not use_ssl:
        if not conn.start_tls():
            conn.unbind()
            raise LdapError("LDAP StartTLS negotiation failed")
    if not conn.bind():
        conn.unbind()
        raise LdapError("LDAP bind failed")
    return conn


def _entry_attr(entry, name: str) -> list[str]:
    try:
        attr = entry[name]
    except Exception:
        return []
    val = getattr(attr, "values", None)
    if val is None:
        raw = getattr(attr, "value", None)
        return [str(raw)] if raw is not None else []
    return [str(v) for v in val if v is not None]


def _map_role(groups: list[str], cfg: dict) -> str:
    group_map = cfg.get("group_map") or {}
    lower_map = {str(k).lower(): str(v) for k, v in group_map.items()}
    for grp in groups:
        if grp in group_map:
            return str(group_map[grp])
        if grp.lower() in lower_map:
            return lower_map[grp.lower()]
    return str(cfg.get("default_role") or "viewer")


def authenticate_ldap(
    username: str,
    password: str,
    cfg: dict,
    bind_pw: str = "",
    ca_pem: str = "",
) -> dict:
    """Authenticate a user via LDAP service bind -> search -> user re-bind -> group mapping."""
    demo.stop()
    clean_user = (username or "").strip()
    if not clean_user or not password:
        raise LdapError("Username and password are required")

    ldap3, LDAPException = _import_ldap3()
    server, use_ssl = _build_server(ldap3, cfg, ca_pem=ca_pem)
    starttls = bool(cfg.get("starttls", False))
    bind_dn = (cfg.get("bind_dn") or "").strip()
    base_dn = (cfg.get("base_dn") or "").strip()
    if not base_dn:
        raise LdapError("LDAP base_dn is not configured")

    user_filter_tpl = cfg.get("user_filter") or "(&(objectClass=person)(uid={username}))"
    search_filter = user_filter_tpl.replace("{username}", _escape_filter_chars(clean_user))
    group_attr = (cfg.get("group_attr") or "memberOf").strip()
    attrs = list(dict.fromkeys([group_attr, "uid", "cn", "displayName", "mail"]))

    try:
        conn = _open_connection(ldap3, server, use_ssl, starttls, bind_dn=bind_dn, bind_pw=bind_pw)
        try:
            conn.search(search_base=base_dn, search_filter=search_filter, attributes=attrs)
            entries = list(conn.entries)
        finally:
            conn.unbind()
    except LdapError:
        raise
    except LDAPException as exc:
        raise LdapError(f"LDAP search failed: {exc}") from exc

    if not entries:
        raise LdapError("User not found in LDAP directory")
    if len(entries) > 1:
        raise LdapError("Ambiguous LDAP user match")

    entry = entries[0]
    user_dn = str(entry.entry_dn or "").strip()
    if not user_dn:
        raise LdapError("invalid credentials")
    try:
        user_conn = _open_connection(ldap3, server, use_ssl, starttls, bind_dn=user_dn, bind_pw=password)
        user_conn.unbind()
    except LdapError as exc:
        raise LdapError("Invalid LDAP credentials") from exc
    except LDAPException as exc:
        raise LdapError("Invalid LDAP credentials") from exc

    groups = _entry_attr(entry, group_attr)
    display_vals = _entry_attr(entry, "displayName") or _entry_attr(entry, "cn")
    mail_vals = _entry_attr(entry, "mail")

    return {
        "external_id": user_dn,
        "username": clean_user,
        "display_name": display_vals[0] if display_vals else clean_user,
        "email": mail_vals[0] if mail_vals else "",
        "role": _map_role(groups, cfg),
        "groups": groups,
    }


def test_connection(
    cfg: dict,
    bind_pw: str = "",
    sample_user: str = "",
    ca_pem: str = "",
) -> dict:
    """Verify LDAP connectivity, TLS/StartTLS, service bind, and optional sample user search."""
    demo.stop()
    ldap3, LDAPException = _import_ldap3()
    server, use_ssl = _build_server(ldap3, cfg, ca_pem=ca_pem)
    starttls = bool(cfg.get("starttls", False))
    bind_dn = (cfg.get("bind_dn") or "").strip()
    base_dn = (cfg.get("base_dn") or "").strip()

    try:
        conn = _open_connection(ldap3, server, use_ssl, starttls, bind_dn=bind_dn, bind_pw=bind_pw)
        try:
            sample_dn = ""
            if sample_user.strip():
                if not base_dn:
                    raise LdapError("LDAP base_dn is required to search sample_user")
                tpl = cfg.get("user_filter") or "(&(objectClass=person)(uid={username}))"
                flt = tpl.replace("{username}", _escape_filter_chars(sample_user.strip()))
                conn.search(search_base=base_dn, search_filter=flt, attributes=["cn"])
                if conn.entries:
                    sample_dn = str(conn.entries[0].entry_dn)
            return {
                "ok": True,
                "url": cfg.get("url", ""),
                "bound_dn": bind_dn or "anonymous",
                "sample_dn": sample_dn,
            }
        finally:
            conn.unbind()
    except LdapError:
        raise
    except LDAPException as exc:
        raise LdapError(f"LDAP connection test failed: {exc}") from exc
