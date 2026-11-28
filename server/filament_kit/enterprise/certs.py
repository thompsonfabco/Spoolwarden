"""TLS certificate inspection, self-signed renewal, CSR generation, upload, and CA bundle management."""
from __future__ import annotations

import datetime as dt
import ipaddress
import re
from pathlib import Path

from .. import config, demo, pairing
from . import users
from .policy import _read_nofollow, _write_atomic

_PEM_CERT_RE = re.compile(rb"-----BEGIN CERTIFICATE-----\s+.*?\s+-----END CERTIFICATE-----", re.DOTALL)


class CertError(ValueError):
    """Raised on invalid TLS certificates, keys, or CSR requests."""


def _cert_path() -> Path:
    return config.paths().etc / "cert.pem"


def _key_path() -> Path:
    return config.paths().etc / "key.pem"


def _csr_key_path() -> Path:
    return config.paths().etc / "enterprise" / "csr_key.pem"


def _to_bytes(val: str | bytes) -> bytes:
    if isinstance(val, bytes):
        return val
    if isinstance(val, str):
        return val.encode("utf-8")
    raise CertError("Expected PEM string or bytes")


def _load_pem_certs(pem_bytes: bytes) -> list:
    from cryptography import x509

    blocks = _PEM_CERT_RE.findall(pem_bytes)
    if not blocks:
        raise CertError("No PEM certificates found")
    certs = []
    for block in blocks:
        try:
            certs.append(x509.load_pem_x509_certificate(block))
        except Exception as exc:
            raise CertError(f"Invalid X.509 certificate: {exc}") from exc
    return certs


def _not_before_utc(cert) -> dt.datetime:
    val = getattr(cert, "not_valid_before_utc", None)
    if val is not None:
        return val
    return cert.not_valid_before.replace(tzinfo=dt.timezone.utc)


def _not_after_utc(cert) -> dt.datetime:
    val = getattr(cert, "not_valid_after_utc", None)
    if val is not None:
        return val
    return cert.not_valid_after.replace(tzinfo=dt.timezone.utc)


def _key_type_label(pub) -> str:
    from cryptography.hazmat.primitives.asymmetric import ec, ed25519, ed448, rsa

    if isinstance(pub, ec.EllipticCurvePublicKey):
        return f"EC {pub.curve.name}"
    if isinstance(pub, rsa.RSAPublicKey):
        return f"RSA {pub.key_size}"
    if isinstance(pub, ed25519.Ed25519PublicKey):
        return "Ed25519"
    if isinstance(pub, ed448.Ed448PublicKey):
        return "Ed448"
    return type(pub).__name__


def _backup_existing(path: Path) -> None:
    data = _read_nofollow(path, max_bytes=4 * 1024 * 1024)
    if data:
        mode = 0o600 if path.name == "key.pem" else 0o644
        _write_atomic(path.with_name(path.name + ".bak"), data, mode)


def cert_info() -> dict:
    """Inspect `etc/cert.pem` and return certificate metadata."""
    if demo.is_on():
        now = dt.datetime.now(dt.timezone.utc)
        nb = now - dt.timedelta(days=30)
        na = now + dt.timedelta(days=335)
        return {
            "subject": "CN=filament-kit-demo.local",
            "issuer": "CN=filament-kit-demo.local",
            "sans": ["filament-kit-demo.local", "127.0.0.1"],
            "not_before": nb.isoformat(),
            "not_after": na.isoformat(),
            "days_left": (na - now).days,
            "sha256_fingerprint": "0" * 64,
            "key_type": "EC secp256r1",
            "self_signed": True,
            "chain_length": 1,
        }

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes

    raw = _read_nofollow(_cert_path(), max_bytes=4 * 1024 * 1024)
    if not raw:
        raise CertError("Certificate file not found")
    certs = _load_pem_certs(raw)
    leaf = certs[0]

    sans: list[str] = []
    try:
        san_ext = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        for name in san_ext:
            sans.append(str(name.value))
    except x509.ExtensionNotFound:
        pass

    nb = _not_before_utc(leaf)
    na = _not_after_utc(leaf)
    now = dt.datetime.now(dt.timezone.utc)
    days_left = (na - now).days

    return {
        "subject": leaf.subject.rfc4514_string(),
        "issuer": leaf.issuer.rfc4514_string(),
        "sans": sans,
        "not_before": nb.isoformat(),
        "not_after": na.isoformat(),
        "days_left": days_left,
        "sha256_fingerprint": leaf.fingerprint(hashes.SHA256()).hex(),
        "key_type": _key_type_label(leaf.public_key()),
        "self_signed": leaf.subject == leaf.issuer,
        "chain_length": len(certs),
    }


def cert_renew_self(extra_dns: tuple[str, ...] | list[str] = (), days: int = 3650) -> dict:
    """Back up current cert/key and generate a fresh self-signed certificate."""
    demo.stop()
    _ = days  # pairing.make_cert uses 3650-day lifetime
    _backup_existing(_cert_path())
    _backup_existing(_key_path())
    pairing.make_cert(
        pairing.short_hostname(),
        pairing.local_ips(),
        config.paths().etc,
        extra_dns=list(extra_dns),
    )
    return cert_info()


def cert_csr(cn: str, sans: tuple[str, ...] | list[str] = (), key_type: str = "ec256") -> str:
    """Generate a private key in `etc/enterprise/csr_key.pem` (0600) and return a PEM CSR."""
    demo.stop()
    clean_cn = (cn or "").strip()
    if not clean_cn:
        raise CertError("Common Name (cn) is required")
    if not isinstance(sans, (list, tuple)) or not all(isinstance(s, str) for s in sans):
        raise CertError("sans must be a list or tuple of strings")

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec, rsa
    from cryptography.x509.oid import NameOID

    kt = (key_type or "ec256").lower()
    if kt == "ec256":
        key = ec.generate_private_key(ec.SECP256R1())
    elif kt == "ec384":
        key = ec.generate_private_key(ec.SECP384R1())
    elif kt == "rsa2048":
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    elif kt == "rsa4096":
        key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    else:
        raise CertError(f"Unsupported key_type: {key_type!r}")

    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    _write_atomic(_csr_key_path(), key_pem, 0o600)

    builder = x509.CertificateSigningRequestBuilder().subject_name(
        x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, clean_cn)])
    )
    san_list: list[x509.GeneralName] = []
    for raw_san in dict.fromkeys([clean_cn, *(str(s).strip() for s in sans if str(s).strip())]):
        try:
            san_list.append(x509.IPAddress(ipaddress.ip_address(raw_san)))
        except ValueError:
            san_list.append(x509.DNSName(raw_san))
    if san_list:
        builder = builder.add_extension(x509.SubjectAlternativeName(san_list), critical=False)

    csr = builder.sign(key, hashes.SHA256())
    return csr.public_bytes(serialization.Encoding.PEM).decode("ascii")


def cert_upload(
    cert_pem: str | bytes,
    key_pem: str | bytes | None = None,
    chain_pem: str | bytes | None = None,
) -> dict:
    """Validate and atomically install an uploaded certificate (and optional key/chain)."""
    demo.stop()
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization
    from cryptography.x509.oid import ExtendedKeyUsageOID

    cert_bytes = _to_bytes(cert_pem)
    certs = _load_pem_certs(cert_bytes)
    if chain_pem is not None:
        if not isinstance(chain_pem, (str, bytes)):
            raise CertError("chain_pem must be a PEM string, bytes, or None")
        chain_text = chain_pem if isinstance(chain_pem, str) else chain_pem.decode("utf-8", "ignore")
        if chain_text.strip():
            certs.extend(_load_pem_certs(_to_bytes(chain_pem)))

    leaf = certs[0]
    now = dt.datetime.now(dt.timezone.utc)
    if _not_before_utc(leaf) > now:
        raise CertError("Certificate is not yet valid")
    if _not_after_utc(leaf) < now:
        raise CertError("Certificate is expired")

    try:
        eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        if ExtendedKeyUsageOID.SERVER_AUTH not in eku:
            raise CertError("Certificate ExtendedKeyUsage does not include serverAuth")
    except x509.ExtensionNotFound:
        pass

    used_csr_key = False
    if key_pem is not None and key_pem != "" and key_pem != b"":
        raw_key = _to_bytes(key_pem)
    else:
        raw_key = _read_nofollow(_csr_key_path(), max_bytes=1024 * 1024)
        if not raw_key:
            raise CertError("No key_pem provided and no pending csr_key.pem found")
        used_csr_key = True

    try:
        priv = serialization.load_pem_private_key(raw_key, password=None)
    except Exception as exc:
        raise CertError(f"Invalid private key PEM: {exc}") from exc

    cert_pub = leaf.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    key_pub = priv.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    if cert_pub != key_pub:
        raise CertError("Certificate public key does not match private key")

    combined_cert = b"".join(c.public_bytes(serialization.Encoding.PEM) for c in certs)
    normalized_key = priv.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )

    _backup_existing(_cert_path())
    _backup_existing(_key_path())
    _write_atomic(_cert_path(), combined_cert, 0o644)
    _write_atomic(_key_path(), normalized_key, 0o600)
    if used_csr_key:
        _csr_key_path().unlink(missing_ok=True)
    return cert_info()


def cert_ca_bundle(pem_or_none: str | bytes | None) -> None:
    """Validate and store or delete the enterprise CA bundle secret."""
    if pem_or_none is None:
        users.delete_secret("ca_bundle")
        return
    raw = _to_bytes(pem_or_none)
    text = raw.decode("utf-8", errors="strict").strip()
    if not text:
        users.delete_secret("ca_bundle")
        return
    _load_pem_certs(raw)
    users.set_secret("ca_bundle", text + "\n")
