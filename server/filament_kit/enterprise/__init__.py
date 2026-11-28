"""Filament Kit Enterprise package: policy, RBAC, users, sessions, certificates, LDAP, and SAML."""
from __future__ import annotations

from .. import config, modules
from . import certs, ldap_auth, policy, rbac, saml_auth, sessions, users

__all__ = [
    "policy",
    "rbac",
    "users",
    "sessions",
    "certs",
    "ldap_auth",
    "saml_auth",
    "is_enabled",
]


def is_enabled(cfg: dict | None = None) -> bool:
    """Return True when the `enterprise` module is enabled in configuration."""
    return "enterprise" in modules.enabled(cfg or config.load())
