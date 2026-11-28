"""Tests for enterprise LDAP/LDAPS and SAML 2.0 SSO authentication."""
from __future__ import annotations

import base64
import datetime as dt
import ssl
import sys
import types
import urllib.parse
import zlib
from types import SimpleNamespace

import pytest

from filament_kit import demo
from filament_kit.enterprise import ldap_auth, saml_auth, users


# ---------------------------------------------------------------------------
# LDAP / LDAPS helpers & tests
# ---------------------------------------------------------------------------


class _FakeLDAPException(Exception):
    pass


class _FakeAttr:
    def __init__(self, values):
        if isinstance(values, (list, tuple)):
            self.values = list(values)
            self.value = self.values[0] if self.values else None
        else:
            self.values = None
            self.value = values


class _FakeEntry:
    def __init__(self, dn: str, attrs: dict):
        self.entry_dn = dn
        self._attrs = {k: _FakeAttr(v) for k, v in attrs.items()}

    def __getitem__(self, name: str):
        if name not in self._attrs:
            raise KeyError(name)
        return self._attrs[name]


def _install_fake_ldap3(
    monkeypatch,
    *,
    entries=None,
    bind_results=None,
    start_tls_result: bool = True,
    raise_on_search: Exception | None = None,
    raise_on_user_bind: Exception | None = None,
):
    """Install a mock `ldap3` package in `sys.modules` and record all calls."""
    recorded = {
        "tls": [],
        "servers": [],
        "connections": [],
        "searches": [],
    }
    bind_queue = list(bind_results) if bind_results is not None else [True, True]
    entry_list = list(entries) if entries is not None else []

    class FakeTls:
        def __init__(self, validate=None, ca_certs_data=None):
            self.validate = validate
            self.ca_certs_data = ca_certs_data
            recorded["tls"].append({"validate": validate, "ca_certs_data": ca_certs_data})

    class FakeServer:
        def __init__(self, url, use_ssl=False, tls=None, get_info=None):
            self.url = url
            self.use_ssl = use_ssl
            self.tls = tls
            self.get_info = get_info
            recorded["servers"].append(
                {"url": url, "use_ssl": use_ssl, "tls": tls, "get_info": get_info}
            )

    class FakeConnection:
        def __init__(self, server, **kwargs):
            self.server = server
            self.kwargs = kwargs
            self.entries = []
            self.opened = False
            self.unbound = False
            self.started_tls = False
            self._record = {
                "user": kwargs.get("user", ""),
                "password": kwargs.get("password", ""),
                "auto_bind": kwargs.get("auto_bind"),
                "opened": False,
                "started_tls": False,
                "bound": False,
                "unbound": False,
            }
            recorded["connections"].append(self._record)

        def open(self):
            self.opened = True
            self._record["opened"] = True

        def start_tls(self):
            self.started_tls = True
            self._record["started_tls"] = True
            return start_tls_result

        def bind(self):
            if len(recorded["connections"]) > 1 and raise_on_user_bind is not None:
                raise raise_on_user_bind
            ok = bind_queue.pop(0) if bind_queue else True
            self._record["bound"] = ok
            return ok

        def search(self, search_base, search_filter, attributes=None):
            recorded["searches"].append(
                {
                    "search_base": search_base,
                    "search_filter": search_filter,
                    "attributes": attributes,
                }
            )
            if raise_on_search is not None:
                raise raise_on_search
            self.entries = list(entry_list)

        def unbind(self):
            self.unbound = True
            self._record["unbound"] = True

    fake_ldap3 = types.ModuleType("ldap3")
    fake_ldap3.NONE = "NONE"
    fake_ldap3.Tls = FakeTls
    fake_ldap3.Server = FakeServer
    fake_ldap3.Connection = FakeConnection

    fake_core = types.ModuleType("ldap3.core")
    fake_exc = types.ModuleType("ldap3.core.exceptions")
    fake_exc.LDAPException = _FakeLDAPException
    fake_core.exceptions = fake_exc
    fake_ldap3.core = fake_core

    monkeypatch.setitem(sys.modules, "ldap3", fake_ldap3)
    monkeypatch.setitem(sys.modules, "ldap3.core", fake_core)
    monkeypatch.setitem(sys.modules, "ldap3.core.exceptions", fake_exc)
    return recorded


def test_ldap_authenticate_bind_escape_rebind_and_jit_upsert(monkeypatch):
    entry = _FakeEntry(
        "uid=alice,ou=users,dc=example,dc=com",
        {
            "memberOf": ["cn=PrintAdmins,ou=groups,dc=example,dc=com"],
            "displayName": ["Alice Smith"],
            "mail": ["alice@example.com"],
        },
    )
    rec = _install_fake_ldap3(monkeypatch, entries=[entry], bind_results=[True, True])
    cfg = {
        "url": "ldaps://ldap.example.com:636",
        "verify_tls": True,
        "bind_dn": "cn=svc,dc=example,dc=com",
        "base_dn": "ou=users,dc=example,dc=com",
        "user_filter": "(&(objectClass=person)(uid={username}))",
        "group_attr": "memberOf",
        "group_map": {"cn=printadmins,ou=groups,dc=example,dc=com": "admin"},
        "default_role": "viewer",
    }
    ca_pem = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"

    # Include RFC 4515 special characters in username to verify filter escaping.
    injected_user = "alice*(test)\x00\\"
    info = ldap_auth.authenticate_ldap(
        injected_user,
        "s3cr3t",
        cfg,
        bind_pw="svc-pw",
        ca_pem=ca_pem,
    )

    assert len(rec["tls"]) == 1
    assert rec["tls"][0]["validate"] == ssl.CERT_REQUIRED
    assert rec["tls"][0]["ca_certs_data"] == ca_pem.strip()
    assert rec["servers"][0]["use_ssl"] is True
    assert rec["servers"][0]["get_info"] == "NONE"

    # Service bind -> search -> user re-bind
    assert len(rec["connections"]) == 2
    svc_conn, user_conn = rec["connections"]
    assert svc_conn["user"] == "cn=svc,dc=example,dc=com"
    assert svc_conn["password"] == "svc-pw"
    assert svc_conn["bound"] is True
    assert svc_conn["unbound"] is True

    expected_escaped = r"alice\2a\28test\29\00\5c"
    assert rec["searches"][0]["search_base"] == "ou=users,dc=example,dc=com"
    assert rec["searches"][0]["search_filter"] == f"(&(objectClass=person)(uid={expected_escaped}))"

    assert user_conn["user"] == "uid=alice,ou=users,dc=example,dc=com"
    assert user_conn["password"] == "s3cr3t"
    assert user_conn["bound"] is True
    assert user_conn["unbound"] is True

    assert info["external_id"] == "uid=alice,ou=users,dc=example,dc=com"
    assert info["username"] == injected_user
    assert info["display_name"] == "Alice Smith"
    assert info["email"] == "alice@example.com"
    assert info["role"] == "admin"

    # JIT user upsert creates and updates external user
    created = users.upsert_external_user(
        "ldap",
        info["external_id"],
        info["username"],
        info["role"],
        display_name=info["display_name"],
        email=info["email"],
    )
    assert created["source"] == "ldap"
    assert created["role"] == "admin"
    assert created["external_id"] == info["external_id"]

    updated = users.upsert_external_user(
        "ldap",
        info["external_id"],
        info["username"],
        "operator",
        display_name=info["display_name"],
        email=info["email"],
    )
    assert updated["id"] == created["id"]
    assert updated["role"] == "operator"


def test_ldap_starttls_and_bad_credentials(monkeypatch):
    entry = _FakeEntry("uid=bob,dc=example,dc=com", {"cn": ["Bob"]})
    rec = _install_fake_ldap3(
        monkeypatch,
        entries=[entry],
        bind_results=[True, False],
        start_tls_result=True,
    )
    cfg = {
        "url": "ldap://ldap.example.com:389",
        "starttls": True,
        "verify_tls": True,
        "bind_dn": "cn=svc,dc=example,dc=com",
        "base_dn": "dc=example,dc=com",
    }

    with pytest.raises(ldap_auth.LdapError, match="Invalid LDAP credentials"):
        ldap_auth.authenticate_ldap("bob", "wrong-pw", cfg, bind_pw="svc-pw")

    assert rec["servers"][0]["use_ssl"] is False
    assert rec["connections"][0]["started_tls"] is True
    assert rec["connections"][1]["started_tls"] is True


def test_ldap_starttls_failure_raises(monkeypatch):
    _install_fake_ldap3(monkeypatch, entries=[], start_tls_result=False)
    cfg = {
        "url": "ldap://ldap.example.com:389",
        "starttls": True,
        "base_dn": "dc=example,dc=com",
    }
    with pytest.raises(ldap_auth.LdapError, match="StartTLS"):
        ldap_auth.test_connection(cfg)


def test_ldap_test_connection_with_sample_user(monkeypatch):
    entry = _FakeEntry("uid=jdoe,ou=users,dc=example,dc=com", {"cn": ["Jane Doe"]})
    rec = _install_fake_ldap3(monkeypatch, entries=[entry], bind_results=[True])
    cfg = {
        "url": "ldaps://ldap.example.com:636",
        "verify_tls": True,
        "bind_dn": "cn=svc,dc=example,dc=com",
        "base_dn": "ou=users,dc=example,dc=com",
    }
    res = ldap_auth.test_connection(cfg, bind_pw="pw", sample_user="jdoe*")
    assert res == {
        "ok": True,
        "url": "ldaps://ldap.example.com:636",
        "bound_dn": "cn=svc,dc=example,dc=com",
        "sample_dn": "uid=jdoe,ou=users,dc=example,dc=com",
    }
    assert r"uid=jdoe\2a" in rec["searches"][0]["search_filter"]


def test_ldap_stops_when_demo_active(monkeypatch):
    monkeypatch.setattr(demo, "is_on", lambda: True)
    cfg = {"url": "ldaps://ldap.example.com:636", "base_dn": "dc=example,dc=com"}
    with pytest.raises(demo.NotInDemo):
        ldap_auth.authenticate_ldap("alice", "pw", cfg)
    with pytest.raises(demo.NotInDemo):
        ldap_auth.test_connection(cfg)


# ---------------------------------------------------------------------------
# SAML 2.0 helpers & tests
# ---------------------------------------------------------------------------


def _generate_self_signed_idp_keypair():
    cryptography = pytest.importorskip("cryptography")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test SAML IdP")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=30))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    assert cryptography is not None
    return cert_pem, key_pem


def test_saml_ensure_sp_keypair_and_sp_metadata():
    pytest.importorskip("cryptography")
    cert1, key1 = saml_auth.ensure_sp_keypair()
    assert "BEGIN CERTIFICATE" in cert1
    assert "BEGIN PRIVATE KEY" in key1

    cert2, key2 = saml_auth.ensure_sp_keypair()
    assert (cert2, key2) == (cert1, key1)

    cfg = {"sp_base_url": "https://hub.example.com/"}
    md_xml = saml_auth.sp_metadata_xml(cfg, cert1)
    assert 'entityID="https://hub.example.com/api/auth/saml/metadata"' in md_xml
    assert 'Location="https://hub.example.com/api/auth/saml/acs"' in md_xml
    assert 'Location="https://hub.example.com/api/auth/saml/slo"' in md_xml
    assert "<ds:X509Certificate>" in md_xml


def test_saml_parse_idp_metadata():
    b64_cert = base64.b64encode(b"FAKECERTDATA" * 8).decode("ascii")
    xml_text = f"""<?xml version="1.0" encoding="UTF-8"?>
<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"
    xmlns:ds="http://www.w3.org/2000/09/xmldsig#"
    entityID="https://idp.example.com/metadata">
  <md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
    <md:KeyDescriptor use="signing">
      <ds:KeyInfo><ds:X509Data>
        <ds:X509Certificate>{b64_cert}</ds:X509Certificate>
      </ds:X509Data></ds:KeyInfo>
    </md:KeyDescriptor>
    <md:SingleLogoutService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
        Location="https://idp.example.com/slo"/>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"
        Location="https://idp.example.com/sso/post"/>
    <md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
        Location="https://idp.example.com/sso/redirect"/>
  </md:IDPSSODescriptor>
</md:EntityDescriptor>"""

    parsed = saml_auth.parse_idp_metadata(xml_text)
    assert parsed["idp_entity_id"] == "https://idp.example.com/metadata"
    assert parsed["idp_sso_url"] == "https://idp.example.com/sso/redirect"
    assert parsed["idp_slo_url"] == "https://idp.example.com/slo"
    assert parsed["idp_cert_pem"].startswith("-----BEGIN CERTIFICATE-----\n")
    assert b64_cert[:32] in parsed["idp_cert_pem"]


def test_saml_parse_idp_metadata_missing_descriptor():
    with pytest.raises(saml_auth.SamlError, match="IDPSSODescriptor"):
        saml_auth.parse_idp_metadata('<md:EntityDescriptor xmlns:md="urn:oasis:names:tc:SAML:2.0:metadata"/>')


def test_saml_build_authn_request():
    cfg = {
        "sp_base_url": "https://hub.example.com",
        "idp_sso_url": "https://idp.example.com/sso",
    }
    redirect_url, req_id = saml_auth.build_authn_request(cfg)
    assert req_id.startswith("fk_")
    parsed = urllib.parse.urlparse(redirect_url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "idp.example.com"
    qs = urllib.parse.parse_qs(parsed.query)
    deflated = base64.b64decode(qs["SAMLRequest"][0])
    xml_bytes = zlib.decompress(deflated, -zlib.MAX_WBITS)
    xml_str = xml_bytes.decode("utf-8")
    assert f'ID="{req_id}"' in xml_str
    assert 'AssertionConsumerServiceURL="https://hub.example.com/api/auth/saml/acs"' in xml_str
    assert "<saml:Issuer>https://hub.example.com/api/auth/saml/metadata</saml:Issuer>" in xml_str


def _build_signed_saml_response_b64(
    *,
    idp_cert_pem: str,
    idp_key_pem: str,
    in_response_to: str,
    recipient: str,
    audience: str,
    not_before_offset_s: int = -60,
    not_on_or_after_offset_s: int = 300,
    groups: list[str] | None = None,
    tamper_after_sign: bool = False,
) -> str:
    etree = pytest.importorskip("lxml.etree")
    signxml = pytest.importorskip("signxml")

    now = dt.datetime.now(dt.timezone.utc)
    issue_instant = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    nb = (now + dt.timedelta(seconds=not_before_offset_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
    nooa = (now + dt.timedelta(seconds=not_on_or_after_offset_s)).strftime("%Y-%m-%dT%H:%M:%SZ")

    group_els = "".join(
        f"<saml:AttributeValue>{g}</saml:AttributeValue>"
        for g in (groups if groups is not None else ["FleetAdmins", "Operators"])
    )
    xml_str = (
        f'<samlp:Response xmlns:samlp="{saml_auth.SAMLP_NS}" xmlns:saml="{saml_auth.SAML_NS}" '
        f'ID="resp_1" Version="2.0" IssueInstant="{issue_instant}" '
        f'Destination="{recipient}" InResponseTo="{in_response_to}">'
        f'<saml:Issuer>https://idp.example.com/metadata</saml:Issuer>'
        f'<samlp:Status><samlp:StatusCode Value="urn:oasis:names:tc:SAML:2.0:status:Success"/></samlp:Status>'
        f'<saml:Assertion ID="assert_1" Version="2.0" IssueInstant="{issue_instant}">'
        f'<saml:Issuer>https://idp.example.com/metadata</saml:Issuer>'
        f'<saml:Subject>'
        f'<saml:NameID>ext-alice-123</saml:NameID>'
        f'<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">'
        f'<saml:SubjectConfirmationData NotOnOrAfter="{nooa}" Recipient="{recipient}" InResponseTo="{in_response_to}"/>'
        f'</saml:SubjectConfirmation>'
        f'</saml:Subject>'
        f'<saml:Conditions NotBefore="{nb}" NotOnOrAfter="{nooa}">'
        f'<saml:AudienceRestriction><saml:Audience>{audience}</saml:Audience></saml:AudienceRestriction>'
        f'</saml:Conditions>'
        f'<saml:AttributeStatement>'
        f'<saml:Attribute Name="uid"><saml:AttributeValue>alice</saml:AttributeValue></saml:Attribute>'
        f'<saml:Attribute Name="displayName"><saml:AttributeValue>Alice SAML</saml:AttributeValue></saml:Attribute>'
        f'<saml:Attribute Name="email"><saml:AttributeValue>alice@example.com</saml:AttributeValue></saml:Attribute>'
        f'<saml:Attribute Name="groups">{group_els}</saml:Attribute>'
        f'</saml:AttributeStatement>'
        f'</saml:Assertion>'
        f'</samlp:Response>'
    )
    root = etree.fromstring(xml_str.encode("utf-8"))
    signer = signxml.XMLSigner(
        method=signxml.methods.enveloped,
        signature_algorithm="rsa-sha256",
        digest_algorithm="sha256",
        c14n_algorithm="http://www.w3.org/2001/10/xml-exc-c14n#",
    )
    signed_root = signer.sign(root, key=idp_key_pem, cert=idp_cert_pem)
    if tamper_after_sign:
        name_id = signed_root.find(f".//{{{saml_auth.SAML_NS}}}NameID")
        name_id.text = "tampered-user"
    return base64.b64encode(etree.tostring(signed_root, encoding="utf-8")).decode("ascii")


def test_saml_process_acs_response_success_and_replay_rejection():
    pytest.importorskip("signxml")
    idp_cert_pem, idp_key_pem = _generate_self_signed_idp_keypair()
    cfg = {
        "sp_base_url": "https://hub.example.com",
        "idp_sso_url": "https://idp.example.com/sso",
        "idp_cert_pem": idp_cert_pem,
        "role_attr": "groups",
        "group_map": {"fleetadmins": "admin", "operators": "operator"},
        "default_role": "viewer",
    }

    _, req_id = saml_auth.build_authn_request(cfg)
    resp_b64 = _build_signed_saml_response_b64(
        idp_cert_pem=idp_cert_pem,
        idp_key_pem=idp_key_pem,
        in_response_to=req_id,
        recipient="https://hub.example.com/api/auth/saml/acs",
        audience="https://hub.example.com/api/auth/saml/metadata",
        groups=["FleetAdmins"],
    )

    info = saml_auth.process_acs_response(resp_b64, cfg)
    assert info == {
        "external_id": "ext-alice-123",
        "username": "alice",
        "display_name": "Alice SAML",
        "email": "alice@example.com",
        "role": "admin",
        "groups": ["FleetAdmins"],
    }

    # Replay of the same InResponseTo must be rejected.
    with pytest.raises(saml_auth.SamlError, match="replayed"):
        saml_auth.process_acs_response(resp_b64, cfg)


def test_saml_process_acs_response_rejections():
    pytest.importorskip("signxml")
    idp_cert_pem, idp_key_pem = _generate_self_signed_idp_keypair()
    other_cert_pem, other_key_pem = _generate_self_signed_idp_keypair()
    cfg = {
        "sp_base_url": "https://hub.example.com",
        "idp_sso_url": "https://idp.example.com/sso",
        "idp_cert_pem": idp_cert_pem,
    }

    # Expired NotOnOrAfter (beyond 120s clock skew)
    _, req_expired = saml_auth.build_authn_request(cfg)
    expired_b64 = _build_signed_saml_response_b64(
        idp_cert_pem=idp_cert_pem,
        idp_key_pem=idp_key_pem,
        in_response_to=req_expired,
        recipient="https://hub.example.com/api/auth/saml/acs",
        audience="https://hub.example.com/api/auth/saml/metadata",
        not_before_offset_s=-600,
        not_on_or_after_offset_s=-300,
    )
    with pytest.raises(saml_auth.SamlError, match="expired"):
        saml_auth.process_acs_response(expired_b64, cfg)

    # Wrong Audience
    _, req_aud = saml_auth.build_authn_request(cfg)
    wrong_aud_b64 = _build_signed_saml_response_b64(
        idp_cert_pem=idp_cert_pem,
        idp_key_pem=idp_key_pem,
        in_response_to=req_aud,
        recipient="https://hub.example.com/api/auth/saml/acs",
        audience="https://other-sp.example.com/metadata",
    )
    with pytest.raises(saml_auth.SamlError, match="Audience"):
        saml_auth.process_acs_response(wrong_aud_b64, cfg)

    # Wrong Recipient
    _, req_rec = saml_auth.build_authn_request(cfg)
    wrong_rec_b64 = _build_signed_saml_response_b64(
        idp_cert_pem=idp_cert_pem,
        idp_key_pem=idp_key_pem,
        in_response_to=req_rec,
        recipient="https://evil.example.com/acs",
        audience="https://hub.example.com/api/auth/saml/metadata",
    )
    with pytest.raises(saml_auth.SamlError, match="Recipient"):
        saml_auth.process_acs_response(wrong_rec_b64, cfg)

    # Bad signature (signed with wrong key)
    _, req_sig = saml_auth.build_authn_request(cfg)
    wrong_key_b64 = _build_signed_saml_response_b64(
        idp_cert_pem=other_cert_pem,
        idp_key_pem=other_key_pem,
        in_response_to=req_sig,
        recipient="https://hub.example.com/api/auth/saml/acs",
        audience="https://hub.example.com/api/auth/saml/metadata",
    )
    with pytest.raises(saml_auth.SamlError, match="signature"):
        saml_auth.process_acs_response(wrong_key_b64, cfg)

    # Tampered XML after valid signature
    _, req_tamper = saml_auth.build_authn_request(cfg)
    tampered_b64 = _build_signed_saml_response_b64(
        idp_cert_pem=idp_cert_pem,
        idp_key_pem=idp_key_pem,
        in_response_to=req_tamper,
        recipient="https://hub.example.com/api/auth/saml/acs",
        audience="https://hub.example.com/api/auth/saml/metadata",
        tamper_after_sign=True,
    )
    with pytest.raises(saml_auth.SamlError, match="signature"):
        saml_auth.process_acs_response(tampered_b64, cfg)


def test_saml_stops_when_demo_active(monkeypatch):
    monkeypatch.setattr(demo, "is_on", lambda: True)
    with pytest.raises(demo.NotInDemo):
        saml_auth.process_acs_response("ZHVtbXk=", {"sp_base_url": "https://hub.example.com", "idp_cert_pem": "x"})
