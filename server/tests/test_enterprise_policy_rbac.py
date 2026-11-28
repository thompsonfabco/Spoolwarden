from __future__ import annotations

import os
import stat

import pytest

from filament_kit.enterprise import policy, rbac


@pytest.fixture
def enterprise_env(tmp_path, monkeypatch):
    etc = tmp_path / "etc"
    state = tmp_path / "state"
    monkeypatch.setenv("FK_ETC", str(etc))
    monkeypatch.setenv("FK_STATE", str(state))
    return etc, state


def test_default_policy_toggles(enterprise_env):
    expected_toggles = {
        "multiuser_login": False,
        "setup_code_fallback": True,
        "rbac": False,
        "local_accounts": True,
        "ldap_sso": False,
        "saml_sso": False,
        "activity_audit": True,
        "cert_management": True,
    }
    assert policy.DEFAULTS["toggles"] == expected_toggles
    loaded = policy.load_policy()
    assert loaded["toggles"] == expected_toggles
    assert policy.get_toggles() == expected_toggles


def test_policy_save_load_roundtrip_and_mode(enterprise_env):
    etc, _ = enterprise_env
    desired = {
        "toggles": {
            "multiuser_login": True,
            "setup_code_fallback": False,
            "rbac": True,
            "local_accounts": True,
            "ldap_sso": True,
            "saml_sso": False,
            "activity_audit": False,
            "cert_management": True,
        },
        "sessions": {
            "browser_idle_s": 3600,
            "browser_abs_s": 43200,
            "client_sliding_s": 604800,
        },
        "password": {
            "min_length": 16,
            "lock_max": 3,
            "lock_window_s": 300,
        },
        "ldap": {
            "url": "ldaps://ldap.example.com:636",
            "starttls": False,
            "bind_dn": "cn=svc,dc=example,dc=com",
            "base_dn": "ou=users,dc=example,dc=com",
            "user_filter": "(uid={username})",
            "group_attr": "memberOf",
            "group_map": {"cn=admins,dc=example,dc=com": "administrator"},
            "default_role": "operator",
            "jit_provision": False,
            "verify_tls": True,
        },
        "saml": {
            "sp_base_url": "https://hub.example.com",
            "idp_entity_id": "https://idp.example.com/meta",
            "idp_sso_url": "https://idp.example.com/sso",
            "idp_slo_url": "https://idp.example.com/slo",
            "idp_cert_pem": "-----BEGIN CERTIFICATE-----\nTEST\n-----END CERTIFICATE-----",
            "role_attr": "roles",
            "group_map": {"hub-viewers": "viewer"},
            "default_role": "viewer",
            "jit_provision": True,
        },
    }

    saved = policy.save_policy(desired)
    loaded = policy.load_policy()
    assert loaded == saved
    assert loaded["toggles"] == desired["toggles"]
    assert loaded["sessions"] == desired["sessions"]
    assert loaded["password"] == desired["password"]
    assert loaded["ldap"] == desired["ldap"]
    assert loaded["saml"] == desired["saml"]
    assert policy.get_toggles() == desired["toggles"]

    xml_path = etc / "enterprise" / "enterprise.xml"
    assert xml_path.exists()
    if os.name != "nt":
        assert stat.S_IMODE(xml_path.stat().st_mode) == 0o600


def test_validate_policy_change_anti_lockout_guards():
    active_admin = [{"username": "alice", "role": "administrator", "active": True}]
    inactive_admin = [{"username": "alice", "role": "administrator", "active": False}]
    active_operator = [{"username": "bob", "role": "operator", "active": True}]

    # Enabling multiuser_login with 0 active admins fails
    for users in ([], inactive_admin, active_operator):
        with pytest.raises(policy.PolicyError):
            policy.validate_policy_change({"toggles": {"multiuser_login": True}}, users)

    # Disabling all auth methods while multiuser_login=True fails even with active admin
    with pytest.raises(policy.PolicyError):
        policy.validate_policy_change(
            {
                "toggles": {
                    "multiuser_login": True,
                    "local_accounts": False,
                    "ldap_sso": False,
                    "saml_sso": False,
                }
            },
            active_admin,
        )

    # Disabling setup_code_fallback without >=1 active admin fails
    for users in ([], inactive_admin, active_operator):
        with pytest.raises(policy.PolicyError):
            policy.validate_policy_change({"toggles": {"setup_code_fallback": False}}, users)

    # Valid combinations succeed
    policy.validate_policy_change(
        {
            "toggles": {
                "multiuser_login": True,
                "setup_code_fallback": False,
                "local_accounts": True,
                "ldap_sso": False,
                "saml_sso": False,
            }
        },
        active_admin,
    )
    policy.validate_policy_change(
        {
            "toggles": {
                "multiuser_login": True,
                "local_accounts": False,
                "ldap_sso": True,
                "saml_sso": False,
            }
        },
        active_admin,
    )


def test_builtin_roles_and_hierarchy(enterprise_env):
    assert (rbac.NA, rbac.RO, rbac.RW) == (0, 1, 2)

    roles = rbac.list_roles()
    assert [r["id"] for r in roles] == ["administrator", "operator", "viewer", "sync"]
    assert all(r["builtin"] is True for r in roles)
    assert rbac.get_role("nonexistent") is None

    # Administrator: RW everywhere
    for fn in rbac.ALL_FUNCTIONS:
        assert rbac.allows("administrator", fn, rbac.RW) is True

    # Operator: RW on spools/printers/presets/analytics/recent, RO on devices/system, NA on enterprise
    assert rbac.allows("operator", "spools.inventory", rbac.RW) is True
    assert rbac.allows("operator", "printers.control", rbac.RW) is True
    assert rbac.allows("operator", "devices.paired", rbac.RO) is True
    assert rbac.allows("operator", "devices.paired", rbac.RW) is False
    assert rbac.allows("operator", "system.settings", rbac.RO) is True
    assert rbac.allows("operator", "system.settings", rbac.RW) is False
    assert rbac.allows("operator", "enterprise.users", rbac.RO) is False
    assert rbac.allows("operator", "enterprise.users", rbac.NA) is True

    # Viewer: RO on spools..devices, NA on system & enterprise
    assert rbac.allows("viewer", "spools.inventory", rbac.RO) is True
    assert rbac.allows("viewer", "spools.inventory", rbac.RW) is False
    assert rbac.allows("viewer", "devices.stations", rbac.RO) is True
    assert rbac.allows("viewer", "system.logs", rbac.RO) is False
    assert rbac.allows("viewer", "enterprise.toggles", rbac.RO) is False

    # Sync: NA everywhere except spools.inventory=RO and presets.sync=RW
    assert rbac.allows("sync", "spools.inventory", rbac.RO) is True
    assert rbac.allows("sync", "spools.inventory", rbac.RW) is False
    assert rbac.allows("sync", "presets.sync", rbac.RW) is True
    assert rbac.allows("sync", "presets.manage", rbac.RO) is False
    assert rbac.allows("sync", "printers.manage", rbac.RO) is False

    # Unknown role / unknown function
    assert rbac.allows("unknown-role", "spools.inventory", rbac.RO) is False
    assert rbac.allows("administrator", "unknown.fn", rbac.NA) is False


def test_custom_role_crud_and_protections(enterprise_env):
    etc, _ = enterprise_env

    created = rbac.create_role(
        "floor_lead",
        "Floor Lead",
        sections={"spools": rbac.RW, "printers": "RO"},
        functions={"printers.control": "RW", "spools.import_export": rbac.NA},
    )
    assert created["id"] == "floor_lead"
    assert created["name"] == "Floor Lead"
    assert created["builtin"] is False
    assert created["sections"]["spools"] == rbac.RW
    assert created["sections"]["printers"] == rbac.RO
    assert created["sections"]["enterprise"] == rbac.NA
    assert created["functions"] == {"printers.control": rbac.RW, "spools.import_export": rbac.NA}

    # Section master + per-function override precedence
    assert rbac.allows("floor_lead", "spools.inventory", rbac.RW) is True
    assert rbac.allows("floor_lead", "spools.import_export", rbac.RO) is False
    assert rbac.allows("floor_lead", "printers.manage", rbac.RO) is True
    assert rbac.allows("floor_lead", "printers.manage", rbac.RW) is False
    assert rbac.allows("floor_lead", "printers.control", rbac.RW) is True
    assert rbac.allows("floor_lead", "enterprise.users", rbac.RO) is False

    listed_ids = [r["id"] for r in rbac.list_roles()]
    assert listed_ids == ["administrator", "operator", "viewer", "sync", "floor_lead"]
    assert rbac.get_role("floor_lead") == created

    roles_xml = etc / "enterprise" / "roles.xml"
    assert roles_xml.exists()
    if os.name != "nt":
        assert stat.S_IMODE(roles_xml.stat().st_mode) == 0o600

    # Update custom role
    updated = rbac.update_role(
        "floor_lead",
        name="Shift Lead",
        sections={"devices": "RO"},
        functions={"printers.control": "NA"},
    )
    assert updated["name"] == "Shift Lead"
    assert updated["sections"]["devices"] == rbac.RO
    assert updated["sections"]["spools"] == rbac.RW
    assert rbac.allows("floor_lead", "devices.paired", rbac.RO) is True
    assert rbac.allows("floor_lead", "printers.control", rbac.RO) is False

    # Built-in role protections & validation errors
    with pytest.raises(rbac.RoleError):
        rbac.create_role("administrator", "Dup Admin")
    with pytest.raises(rbac.RoleError):
        rbac.create_role("floor_lead", "Duplicate")
    with pytest.raises(rbac.RoleError):
        rbac.create_role("Bad Role!", "Invalid ID")
    with pytest.raises(rbac.RoleError):
        rbac.update_role("operator", name="Hacked")
    with pytest.raises(rbac.RoleError):
        rbac.delete_role("viewer")

    # Cannot delete custom role when in use
    with pytest.raises(rbac.RoleError):
        rbac.delete_role("floor_lead", in_use_roles={"floor_lead"})
    assert rbac.get_role("floor_lead") is not None

    # Delete succeeds once unused
    rbac.delete_role("floor_lead", in_use_roles={"other_role"})
    assert rbac.get_role("floor_lead") is None
    with pytest.raises(rbac.RoleError):
        rbac.delete_role("floor_lead")


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/setup/api/enterprise/toggles", ("enterprise.toggles", rbac.RO)),
        ("POST", "/setup/api/enterprise/policy", ("enterprise.toggles", rbac.RW)),
        ("GET", "/setup/api/enterprise/users/alice", ("enterprise.users", rbac.RO)),
        ("DELETE", "/setup/api/enterprise/roles/floor_lead", ("enterprise.roles", rbac.RW)),
        ("PUT", "/setup/api/enterprise/ldap", ("enterprise.sso", rbac.RW)),
        ("GET", "/setup/api/enterprise/saml/metadata", ("enterprise.sso", rbac.RO)),
        ("POST", "/setup/api/enterprise/certs/upload", ("enterprise.certs", rbac.RW)),
        ("GET", "/setup/api/enterprise/audit", ("enterprise.audit", rbac.RO)),
        ("POST", "/setup/api/modules", ("system.features", rbac.RW)),
        ("GET", "/setup/api/settings", ("system.settings", rbac.RO)),
        ("POST", "/setup/api/backups/create", ("system.backups", rbac.RW)),
        ("GET", "/setup/api/updates", ("system.updates", rbac.RO)),
        ("GET", "/setup/api/logs", ("system.logs", rbac.RO)),
        ("GET", "/setup/api/paired", ("devices.paired", rbac.RO)),
        ("POST", "/setup/api/stations/1", ("devices.stations", rbac.RW)),
        ("GET", "/setup/api/readers", ("devices.readers", rbac.RO)),
        ("POST", "/setup/api/flash", ("devices.flash", rbac.RW)),
        ("GET", "/api/spools?limit=10", ("spools.inventory", rbac.RO)),
        ("POST", "/api/spools/1/label", ("spools.labels", rbac.RW)),
        ("POST", "/api/spools/import", ("spools.import_export", rbac.RW)),
        ("GET", "/api/storage", ("spools.storage", rbac.RO)),
        ("GET", "/api/printers", ("printers.manage", rbac.RO)),
        ("GET", "/api/printers/1/camera", ("printers.camera", rbac.RO)),
        ("PUT", "/api/printers/1/slots", ("printers.slots", rbac.RW)),
        ("POST", "/api/printers/1/pause", ("printers.control", rbac.RW)),
        ("GET", "/api/presets", ("presets.manage", rbac.RO)),
        ("POST", "/api/presets/sync", ("presets.sync", rbac.RW)),
        ("GET", "/api/analytics", ("analytics.view", rbac.RO)),
        ("POST", "/api/analytics/undo", ("analytics.undo", rbac.RW)),
        ("GET", "/api/recent", ("recent.intake", rbac.RO)),
        ("POST", "/intake/api/scan", ("recent.intake", rbac.RW)),
        ("POST", "/intake/api/corrections", ("recent.corrections", rbac.RW)),
        ("GET", "/health", None),
        ("GET", "/api/nonexistent", None),
    ],
)
def test_route_permission_mappings(method, path, expected):
    assert rbac.route_permission(method, path) == expected
