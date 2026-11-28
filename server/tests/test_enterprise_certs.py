"""Tests for `filament_kit.enterprise.certs`."""
from __future__ import annotations

import datetime as dt

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from filament_kit import demo, pairing
from filament_kit.enterprise import certs, users


@pytest.fixture
def cert_env(tmp_path, monkeypatch):
    etc_dir = tmp_path / "etc"
    state_dir = tmp_path / "state"
    etc_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("FK_ETC", str(etc_dir))
    monkeypatch.setenv("FK_STATE", str(state_dir))
    pairing.make_cert("workshop-hub", ["127.0.0.1"], etc_dir)
    return etc_dir, state_dir


def _make_ca():
    ca_key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Enterprise Test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(days=1))
        .not_valid_after(now + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    return ca_key, ca_cert


def _sign_leaf(
    ca_key,
    ca_cert,
    pub_key,
    *,
    cn: str = "hub.corp.local",
    days_valid: int = 365,
    eku_oids: list | None = (ExtendedKeyUsageOID.SERVER_AUTH,),
):
    now = dt.datetime.now(dt.timezone.utc)
    if days_valid >= 0:
        nb = now - dt.timedelta(hours=1)
        na = now + dt.timedelta(days=days_valid)
    else:
        nb = now + dt.timedelta(days=days_valid - 10)
        na = now + dt.timedelta(days=days_valid)

    builder = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)]))
        .issuer_name(ca_cert.subject)
        .public_key(pub_key)
        .serial_number(x509.random_serial_number())
        .not_valid_before(nb)
        .not_valid_after(na)
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(cn)]), critical=False)
    )
    if eku_oids is not None:
        builder = builder.add_extension(x509.ExtendedKeyUsage(list(eku_oids)), critical=False)
    return builder.sign(ca_key, hashes.SHA256())


def test_cert_info_parses_initial_self_signed_cert(cert_env):
    info = certs.cert_info()
    assert "workshop-hub" in info["subject"]
    assert info["subject"] == info["issuer"]
    assert "localhost" in info["sans"]
    assert any("workshop-hub" in s for s in info["sans"])
    assert dt.datetime.fromisoformat(info["not_before"]) < dt.datetime.fromisoformat(info["not_after"])
    assert info["days_left"] > 3000
    assert len(info["sha256_fingerprint"]) == 64
    assert info["key_type"]
    assert info["self_signed"] is True
    assert info["chain_length"] == 1


def test_cert_renew_self_backs_up_and_adds_extra_dns(cert_env):
    etc_dir, _ = cert_env
    orig_cert = (etc_dir / "cert.pem").read_bytes()
    orig_key = (etc_dir / "key.pem").read_bytes()
    orig_fp = certs.cert_info()["sha256_fingerprint"]

    renewed = certs.cert_renew_self(extra_dns=["hub.corp.local"])

    assert (etc_dir / "cert.pem.bak").read_bytes() == orig_cert
    assert (etc_dir / "key.pem.bak").read_bytes() == orig_key
    assert "hub.corp.local" in renewed["sans"]
    assert renewed["sha256_fingerprint"] != orig_fp
    assert renewed["self_signed"] is True


def test_cert_csr_creates_staging_key_and_valid_pem_csr(cert_env):
    etc_dir, _ = cert_env
    csr_pem = certs.cert_csr("hub.corp.local", sans=["10.0.0.5"])

    csr_key_path = etc_dir / "enterprise" / "csr_key.pem"
    assert csr_key_path.exists()
    priv = serialization.load_pem_private_key(csr_key_path.read_bytes(), password=None)

    csr = x509.load_pem_x509_csr(csr_pem.encode("ascii"))
    assert csr.is_signature_valid
    assert csr.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "hub.corp.local"
    assert csr.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    ) == priv.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    san_values = [str(n.value) for n in csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value]
    assert "hub.corp.local" in san_values
    assert "10.0.0.5" in san_values


def test_cert_upload_with_csr_staging_key_and_explicit_key(cert_env):
    etc_dir, _ = cert_env
    ca_key, ca_cert = _make_ca()
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM)

    # 1) Upload CA-signed cert matching the CSR staging key (key_pem=None)
    orig_cert = (etc_dir / "cert.pem").read_bytes()
    orig_key = (etc_dir / "key.pem").read_bytes()
    csr_pem = certs.cert_csr("hub.corp.local", sans=["10.0.0.5"])
    csr = x509.load_pem_x509_csr(csr_pem.encode("ascii"))
    leaf_from_csr = _sign_leaf(ca_key, ca_cert, csr.public_key())
    leaf_pem = leaf_from_csr.public_bytes(serialization.Encoding.PEM)

    info = certs.cert_upload(leaf_pem, chain_pem=ca_pem)
    assert info["self_signed"] is False
    assert info["chain_length"] == 2
    assert "hub.corp.local" in info["sans"]
    assert (etc_dir / "cert.pem.bak").read_bytes() == orig_cert
    assert (etc_dir / "key.pem.bak").read_bytes() == orig_key

    # 2) Upload with an explicit key_pem preserves .bak of the previous cert/key
    prev_cert = (etc_dir / "cert.pem").read_bytes()
    prev_key = (etc_dir / "key.pem").read_bytes()
    explicit_key = ec.generate_private_key(ec.SECP256R1())
    explicit_key_pem = explicit_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    leaf2 = _sign_leaf(ca_key, ca_cert, explicit_key.public_key(), cn="hub2.corp.local")
    info2 = certs.cert_upload(leaf2.public_bytes(serialization.Encoding.PEM), key_pem=explicit_key_pem)
    assert "hub2.corp.local" in info2["sans"]
    assert (etc_dir / "cert.pem.bak").read_bytes() == prev_cert
    assert (etc_dir / "key.pem.bak").read_bytes() == prev_key


def test_cert_upload_rejections_preserve_existing_cert_and_bak(cert_env):
    etc_dir, _ = cert_env
    ca_key, ca_cert = _make_ca()
    good_key = ec.generate_private_key(ec.SECP256R1())
    other_key = ec.generate_private_key(ec.SECP256R1())
    other_key_pem = other_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    good_key_pem = good_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

    cert_before = (etc_dir / "cert.pem").read_bytes()

    # Mismatched key
    valid_leaf = _sign_leaf(ca_key, ca_cert, good_key.public_key())
    with pytest.raises(certs.CertError, match="does not match"):
        certs.cert_upload(valid_leaf.public_bytes(serialization.Encoding.PEM), key_pem=other_key_pem)

    # Expired cert
    expired_leaf = _sign_leaf(ca_key, ca_cert, good_key.public_key(), days_valid=-5)
    with pytest.raises(certs.CertError, match="expired"):
        certs.cert_upload(expired_leaf.public_bytes(serialization.Encoding.PEM), key_pem=good_key_pem)

    # Non-serverAuth EKU
    client_only_leaf = _sign_leaf(
        ca_key, ca_cert, good_key.public_key(), eku_oids=[ExtendedKeyUsageOID.CLIENT_AUTH]
    )
    with pytest.raises(certs.CertError, match="serverAuth"):
        certs.cert_upload(client_only_leaf.public_bytes(serialization.Encoding.PEM), key_pem=good_key_pem)

    assert (etc_dir / "cert.pem").read_bytes() == cert_before
    assert not (etc_dir / "cert.pem.bak").exists()


def test_cert_ca_bundle_validates_stores_and_deletes(cert_env):
    _, ca_cert = _make_ca()
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM).decode("ascii")

    with pytest.raises(certs.CertError):
        certs.cert_ca_bundle("not a valid certificate")

    certs.cert_ca_bundle(ca_pem)
    stored = users.get_secret("ca_bundle")
    assert stored is not None
    assert "BEGIN CERTIFICATE" in stored

    certs.cert_ca_bundle(None)
    assert users.get_secret("ca_bundle") is None

    certs.cert_ca_bundle(ca_pem)
    assert users.get_secret("ca_bundle") is not None
    certs.cert_ca_bundle("   ")
    assert users.get_secret("ca_bundle") is None


def test_demo_mode_blocks_cert_mutations(cert_env, mountable):
    ca_key, ca_cert = _make_ca()
    key = ec.generate_private_key(ec.SECP256R1())
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    leaf_pem = _sign_leaf(ca_key, ca_cert, key.public_key()).public_bytes(serialization.Encoding.PEM)

    demo.start(spoolman=type("E", (), {"get": lambda self, p: []})(), registry_load=lambda: {})
    try:
        with pytest.raises(demo.NotInDemo):
            certs.cert_renew_self()
        with pytest.raises(demo.NotInDemo):
            certs.cert_csr("hub.corp.local")
        with pytest.raises(demo.NotInDemo):
            certs.cert_upload(leaf_pem, key_pem=key_pem)
    finally:
        demo.leave()
