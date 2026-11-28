"""SAML 2.0 Service Provider metadata, AuthnRequest generation, and ACS response verification."""
from __future__ import annotations

import base64
import datetime as dt
import secrets
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import defusedxml.ElementTree as DET
from defusedxml import DefusedXmlException

from .. import config, demo
from . import users
from .policy import _locked_file, _read_nofollow, _write_atomic

SAML_NS = "urn:oasis:names:tc:SAML:2.0:assertion"
SAMLP_NS = "urn:oasis:names:tc:SAML:2.0:protocol"
MD_NS = "urn:oasis:names:tc:SAML:2.0:metadata"
DS_NS = "http://www.w3.org/2000/09/xmldsig#"
BINDING_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
BINDING_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"

REQUEST_TTL_S = 600
CLOCK_SKEW_S = 120


class SamlError(Exception):
    """Raised when SAML metadata, request, or assertion validation fails."""


def _requests_path() -> Path:
    return demo.where(config.paths().state / "enterprise" / "saml_requests.xml", "enterprise/saml_requests.xml")


def _strip_pem_headers(pem_text: str) -> str:
    lines = [
        line.strip()
        for line in (pem_text or "").splitlines()
        if line.strip() and not line.strip().startswith("-----")
    ]
    return "".join(lines)


def _wrap_cert_pem(b64_body: str) -> str:
    clean = "".join(b64_body.split())
    wrapped = "\n".join(clean[i : i + 64] for i in range(0, len(clean), 64))
    return f"-----BEGIN CERTIFICATE-----\n{wrapped}\n-----END CERTIFICATE-----\n"


def _parse_iso_utc(raw: str) -> float:
    text = (raw or "").strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise SamlError(f"Invalid SAML timestamp: {raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.timestamp()


def ensure_sp_keypair() -> tuple[str, str]:
    """Return `(sp_cert_pem, sp_key_pem)`, generating and persisting a 10-year SP keypair if missing."""
    cert_pem = users.get_secret("saml_sp_cert")
    key_pem = users.get_secret("saml_sp_key")
    if cert_pem and key_pem:
        return cert_pem, key_pem

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Filament Kit SAML SP")])
    now = dt.datetime.now(dt.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM).decode("ascii")
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode("ascii")
    users.set_secret("saml_sp_cert", cert_pem)
    users.set_secret("saml_sp_key", key_pem)
    return cert_pem, key_pem


def _sp_urls(cfg: dict) -> tuple[str, str, str]:
    base = (cfg.get("sp_base_url") or "").strip().rstrip("/")
    if not base:
        raise SamlError("SAML sp_base_url is not configured")
    entity_id = f"{base}/api/auth/saml/metadata"
    acs_url = f"{base}/api/auth/saml/acs"
    slo_url = f"{base}/api/auth/saml/slo"
    return entity_id, acs_url, slo_url


def sp_metadata_xml(cfg: dict, sp_cert_pem: str) -> str:
    """Build SAML 2.0 Service Provider metadata XML."""
    entity_id, acs_url, slo_url = _sp_urls(cfg)
    cert_b64 = _strip_pem_headers(sp_cert_pem)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        f'<md:EntityDescriptor xmlns:md={quoteattr(MD_NS)} xmlns:ds={quoteattr(DS_NS)} '
        f'entityID={quoteattr(entity_id)}>\n'
        '  <md:SPSSODescriptor AuthnRequestsSigned="false" WantAssertionsSigned="true" '
        f'protocolSupportEnumeration={quoteattr(SAMLP_NS)}>\n'
        '    <md:KeyDescriptor use="signing">\n'
        "      <ds:KeyInfo><ds:X509Data>"
        f"<ds:X509Certificate>{escape(cert_b64)}</ds:X509Certificate>"
        "</ds:X509Data></ds:KeyInfo>\n"
        "    </md:KeyDescriptor>\n"
        f'    <md:SingleLogoutService Binding={quoteattr(BINDING_REDIRECT)} Location={quoteattr(slo_url)}/>\n'
        f'    <md:AssertionConsumerService Binding={quoteattr(BINDING_POST)} Location={quoteattr(acs_url)} '
        'index="0" isDefault="true"/>\n'
        "  </md:SPSSODescriptor>\n"
        "</md:EntityDescriptor>\n"
    )


def parse_idp_metadata(xml_text: str) -> dict:
    """Parse IdP metadata XML into `{idp_entity_id, idp_sso_url, idp_slo_url, idp_cert_pem}`."""
    try:
        root = DET.fromstring((xml_text or "").encode("utf-8"))
    except (DefusedXmlException, ET.ParseError) as exc:
        raise SamlError(f"Malformed IdP metadata XML: {exc}") from exc

    entity_id = root.attrib.get("entityID", "").strip()
    idp_desc = root.find(f"{{{MD_NS}}}IDPSSODescriptor")
    if idp_desc is None:
        raise SamlError("IDPSSODescriptor not found in IdP metadata")

    sso_url = ""
    for el in idp_desc.findall(f"{{{MD_NS}}}SingleSignOnService"):
        binding = el.attrib.get("Binding", "")
        loc = el.attrib.get("Location", "").strip()
        if binding == BINDING_REDIRECT and loc:
            sso_url = loc
            break
        if not sso_url and loc:
            sso_url = loc

    slo_url = ""
    for el in idp_desc.findall(f"{{{MD_NS}}}SingleLogoutService"):
        loc = el.attrib.get("Location", "").strip()
        if loc:
            slo_url = loc
            break

    cert_pem = ""
    for kd in idp_desc.findall(f"{{{MD_NS}}}KeyDescriptor"):
        use = kd.attrib.get("use", "signing")
        if use not in ("signing", ""):
            continue
        x509_el = kd.find(f".//{{{DS_NS}}}X509Certificate")
        if x509_el is not None and x509_el.text and x509_el.text.strip():
            cert_pem = _wrap_cert_pem(x509_el.text)
            break

    return {
        "idp_entity_id": entity_id,
        "idp_sso_url": sso_url,
        "idp_slo_url": slo_url,
        "idp_cert_pem": cert_pem,
    }


def _parse_requests_xml(raw: bytes) -> dict[str, float]:
    if not raw.strip():
        return {}
    try:
        root = DET.fromstring(raw)
    except (DefusedXmlException, ET.ParseError) as exc:
        raise SamlError(f"Malformed SAML request state XML: {exc}") from exc
    out: dict[str, float] = {}
    for el in root.findall("req"):
        rid = el.attrib.get("id", "")
        if rid:
            out[rid] = float(el.attrib.get("expires_at", "0"))
    return out


def _render_requests_xml(reqs: dict[str, float]) -> bytes:
    lines = ['<?xml version="1.0" encoding="utf-8"?>', '<saml-requests version="1">']
    for rid, exp in sorted(reqs.items()):
        lines.append(f"  <req id={quoteattr(rid)} expires_at={quoteattr(repr(float(exp)))}/>")
    lines.append("</saml-requests>")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _record_request_id(req_id: str, now: float) -> None:
    path = _requests_path()
    with _locked_file(path):
        reqs = _parse_requests_xml(_read_nofollow(path))
        reqs = {k: v for k, v in reqs.items() if now < v}
        reqs[req_id] = now + REQUEST_TTL_S
        _write_atomic(path, _render_requests_xml(reqs), 0o600)


def _consume_request_id(req_id: str, now: float) -> bool:
    if not req_id:
        return False
    path = _requests_path()
    with _locked_file(path):
        reqs = _parse_requests_xml(_read_nofollow(path))
        exp = reqs.pop(req_id, None)
        reqs = {k: v for k, v in reqs.items() if now < v}
        _write_atomic(path, _render_requests_xml(reqs), 0o600)
        return exp is not None and now < exp


def build_authn_request(cfg: dict) -> tuple[str, str]:
    """Generate a SAML AuthnRequest redirect URL and store its `req_id` with a 600s TTL."""
    entity_id, acs_url, _ = _sp_urls(cfg)
    idp_sso_url = (cfg.get("idp_sso_url") or "").strip()
    if not idp_sso_url:
        raise SamlError("SAML idp_sso_url is not configured")

    now = time.time()
    req_id = f"fk_{secrets.token_hex(16)}"
    issue_instant = dt.datetime.fromtimestamp(now, tz=dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    _record_request_id(req_id, now)

    xml = (
        f'<samlp:AuthnRequest xmlns:samlp={quoteattr(SAMLP_NS)} xmlns:saml={quoteattr(SAML_NS)} '
        f'ID={quoteattr(req_id)} Version="2.0" IssueInstant={quoteattr(issue_instant)} '
        f'Destination={quoteattr(idp_sso_url)} ProtocolBinding={quoteattr(BINDING_POST)} '
        f'AssertionConsumerServiceURL={quoteattr(acs_url)}>'
        f"<saml:Issuer>{escape(entity_id)}</saml:Issuer>"
        "</samlp:AuthnRequest>"
    ).encode("utf-8")

    compressor = zlib.compressobj(9, zlib.DEFLATED, -zlib.MAX_WBITS)
    deflated = compressor.compress(xml) + compressor.flush()
    b64 = base64.b64encode(deflated).decode("ascii")
    sep = "&" if "?" in idp_sso_url else "?"
    redirect_url = f"{idp_sso_url}{sep}SAMLRequest={urllib.parse.quote(b64)}"
    return redirect_url, req_id


def _map_role(groups: list[str], cfg: dict) -> str:
    group_map = cfg.get("group_map") or {}
    lower_map = {str(k).lower(): str(v) for k, v in group_map.items()}
    for grp in groups:
        if grp in group_map:
            return str(group_map[grp])
        if grp.lower() in lower_map:
            return lower_map[grp.lower()]
    return str(cfg.get("default_role") or "viewer")


def process_acs_response(saml_response_b64: str, cfg: dict) -> dict:
    """Verify a base64-encoded SAMLResponse (XMLDSig, InResponseTo, Audience, Recipient, time window)."""
    demo.stop()
    try:
        from lxml import etree
        from signxml import XMLVerifier
    except ImportError as exc:
        raise SamlError("signxml and lxml packages are required for SAML") from exc

    idp_cert_pem = (cfg.get("idp_cert_pem") or "").strip()
    if not idp_cert_pem:
        raise SamlError("SAML idp_cert_pem is not configured")
    entity_id, acs_url, _ = _sp_urls(cfg)
    sp_base = (cfg.get("sp_base_url") or "").strip().rstrip("/")

    try:
        raw_xml = base64.b64decode(saml_response_b64, validate=True)
        parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
        root = etree.fromstring(raw_xml, parser=parser)
    except Exception as exc:
        raise SamlError(f"Invalid SAMLResponse payload: {exc}") from exc

    try:
        verified = XMLVerifier().verify(root, x509_cert=idp_cert_pem)
        signed_el = verified.signed_xml
    except Exception as exc:
        raise SamlError(f"SAML XML signature verification failed: {exc}") from exc

    now = time.time()
    is_response_signed = signed_el.tag == f"{{{SAMLP_NS}}}Response"
    if signed_el.tag == f"{{{SAML_NS}}}Assertion":
        assertion = signed_el
    elif is_response_signed:
        status_code_el = signed_el.find(f"{{{SAMLP_NS}}}Status/{{{SAMLP_NS}}}StatusCode")
        status_val = status_code_el.attrib.get("Value", "").strip() if status_code_el is not None else ""
        if status_val != "urn:oasis:names:tc:SAML:2.0:status:Success" and not status_val.endswith(":Success"):
            raise SamlError("SAML Response status is not Success")
        assertion = signed_el.find(f"{{{SAML_NS}}}Assertion")
    else:
        raise SamlError("Signed SAML element must be Assertion or Response")

    if assertion is None:
        raise SamlError("SAML Assertion missing")

    idp_entity_id = (cfg.get("idp_entity_id") or "").strip()
    if idp_entity_id:
        issuer_el = assertion.find(f"{{{SAML_NS}}}Issuer")
        if issuer_el is None and is_response_signed:
            issuer_el = signed_el.find(f"{{{SAML_NS}}}Issuer")
        if issuer_el is None or (issuer_el.text or "").strip() != idp_entity_id:
            raise SamlError("SAML Issuer mismatch")

    subj_conf_data = assertion.find(f".//{{{SAML_NS}}}SubjectConfirmationData")
    if subj_conf_data is None:
        raise SamlError("SAML SubjectConfirmationData missing")
    recipient = subj_conf_data.attrib.get("Recipient", "").strip()
    if recipient != acs_url:
        raise SamlError("SAML Recipient mismatch")
    sc_nooa = subj_conf_data.attrib.get("NotOnOrAfter")
    if not sc_nooa:
        raise SamlError("SAML SubjectConfirmationData NotOnOrAfter missing")
    if now - CLOCK_SKEW_S >= _parse_iso_utc(sc_nooa):
        raise SamlError("SAML SubjectConfirmationData has expired (NotOnOrAfter)")
    sc_irt = subj_conf_data.attrib.get("InResponseTo", "").strip()
    if not sc_irt:
        raise SamlError("SAML SubjectConfirmationData InResponseTo missing")
    if is_response_signed:
        resp_irt = signed_el.attrib.get("InResponseTo", "").strip()
        if resp_irt and resp_irt != sc_irt:
            raise SamlError("SAML SubjectConfirmationData InResponseTo mismatch")
    if not _consume_request_id(sc_irt, now):
        raise SamlError("Invalid, expired, or replayed SAML InResponseTo")

    conditions = assertion.find(f"{{{SAML_NS}}}Conditions")
    if conditions is None:
        raise SamlError("SAML Conditions missing")
    nb = conditions.attrib.get("NotBefore")
    nooa = conditions.attrib.get("NotOnOrAfter")
    if not nb or not nooa:
        raise SamlError("SAML Conditions NotBefore/NotOnOrAfter missing")
    if now + CLOCK_SKEW_S < _parse_iso_utc(nb):
        raise SamlError("SAML Assertion is not yet valid (NotBefore)")
    if now - CLOCK_SKEW_S >= _parse_iso_utc(nooa):
        raise SamlError("SAML Assertion has expired (NotOnOrAfter)")

    audiences = [el.text.strip() for el in conditions.findall(f".//{{{SAML_NS}}}Audience") if el.text and el.text.strip()]
    if entity_id not in audiences:
        raise SamlError("SAML Audience mismatch")

    name_id_el = assertion.find(f".//{{{SAML_NS}}}NameID")
    name_id = name_id_el.text.strip() if name_id_el is not None and name_id_el.text else ""
    if not name_id:
        raise SamlError("SAML NameID missing")

    attrs: dict[str, list[str]] = {}
    for attr_el in assertion.findall(f".//{{{SAML_NS}}}Attribute"):
        aname = attr_el.attrib.get("Name", "").strip()
        if not aname:
            continue
        vals = [
            v.text.strip()
            for v in attr_el.findall(f"{{{SAML_NS}}}AttributeValue")
            if v.text and v.text.strip()
        ]
        attrs[aname] = vals

    role_attr = cfg.get("role_attr") or "groups"
    groups = attrs.get(role_attr, [])
    email = (attrs.get("email") or attrs.get("mail") or [""])[0]
    display_name = (attrs.get("displayName") or attrs.get("cn") or [name_id])[0]
    username = (attrs.get("uid") or attrs.get("username") or [name_id])[0]

    return {
        "external_id": name_id,
        "username": username,
        "display_name": display_name,
        "email": email,
        "role": _map_role(groups, cfg),
        "groups": groups,
    }
