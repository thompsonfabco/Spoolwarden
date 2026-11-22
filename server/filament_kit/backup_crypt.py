"""Streamed Scrypt + AES-256-GCM chunked encryption for Filament Kit backups (.enc).

Format (header + 0 or more framed chunks, at least 1 chunk for 0-byte input):
  Header (35 bytes):
    magic        8 bytes   b"FKENC1\\x00\\x00"
    salt        16 bytes   random scrypt salt
    n_log2       1 byte    log2(N) for scrypt (default 15 -> N=32768)
    r            1 byte    scrypt block size (default 8)
    p            1 byte    scrypt parallelization (default 1)
    chunk_size   4 bytes   uint32 BE plaintext chunk size (default 1 MiB)
    nonce_prefix 4 bytes   random per-file prefix for 12-byte GCM nonces

  Each chunk:
    ct_len       4 bytes   uint32 BE length of ciphertext + 16-byte GCM tag
    is_last      1 byte    0 for non-final chunks, 1 for the final chunk
    ciphertext   ct_len    AES-256-GCM(key, nonce=nonce_prefix||counter_u64_be,
                                       plaintext, aad=header||counter_u64_be||is_last)

Any wrong password, truncated/cut file, trailing garbage, swapped chunk, or modified byte fails
authentication and raises `WrongPasswordOrDamaged` before any output is committed.
"""
from __future__ import annotations

import os
import struct
import tempfile
from pathlib import Path
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"FKENC1\x00\x00"
HEADER_STRUCT = struct.Struct(">8s16sBBBI4s")
FRAME_STRUCT = struct.Struct(">IB")
AAD_SUFFIX_STRUCT = struct.Struct(">QB")
NONCE_COUNTER_STRUCT = struct.Struct(">Q")

DEFAULT_N_LOG2 = 15
DEFAULT_R = 8
DEFAULT_P = 1
DEFAULT_CHUNK_SIZE = 1024 * 1024  # 1 MiB
TAG_LEN = 16
KEY_LEN = 32

MIN_N_LOG2, MAX_N_LOG2 = 10, 20
MIN_R, MAX_R = 1, 32
MIN_P, MAX_P = 1, 16
MIN_CHUNK_SIZE, MAX_CHUNK_SIZE = 4096, 16 * 1024 * 1024

WRONG_MESSAGE = "Wrong password, or the file is damaged. Nothing was changed."


class WrongPasswordOrDamaged(Exception):
    """Raised when a backup password is wrong or the encrypted file is truncated or tampered with."""

    def __init__(self, message: str = WRONG_MESSAGE):
        super().__init__(message)


def _derive_key(password: str, salt: bytes, n_log2: int, r: int, p: int) -> bytes:
    if not isinstance(password, str) or not password:
        raise WrongPasswordOrDamaged()
    kdf = Scrypt(salt=salt, length=KEY_LEN, n=1 << n_log2, r=r, p=p)
    return kdf.derive(password.encode("utf-8"))


def _read_exact(src: BinaryIO, n: int) -> bytes:
    """Read up to `n` bytes, looping on short reads from pipes/buffered streams."""
    parts: list[bytes] = []
    remaining = n
    while remaining > 0:
        chunk = src.read(remaining)
        if not chunk:
            break
        parts.append(chunk)
        remaining -= len(chunk)
    return b"".join(parts)


def is_encrypted(path: Path) -> bool:
    """Return True if `path` starts with the FKENC1 magic header."""
    try:
        with open(path, "rb") as fh:
            return fh.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def encrypt_stream(
    src: BinaryIO,
    dst: BinaryIO,
    password: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    n_log2: int = DEFAULT_N_LOG2,
    r: int = DEFAULT_R,
    p: int = DEFAULT_P,
) -> int:
    """Stream-encrypt `src` into `dst`, returning total encrypted bytes written."""
    if not (MIN_N_LOG2 <= n_log2 <= MAX_N_LOG2 and MIN_R <= r <= MAX_R and MIN_P <= p <= MAX_P):
        raise ValueError("invalid scrypt cost parameters")
    if not (MIN_CHUNK_SIZE <= chunk_size <= MAX_CHUNK_SIZE):
        raise ValueError("invalid chunk size")
    if not isinstance(password, str) or not password:
        raise ValueError("password must be non-empty")

    salt = os.urandom(16)
    nonce_prefix = os.urandom(4)
    header = HEADER_STRUCT.pack(MAGIC, salt, n_log2, r, p, chunk_size, nonce_prefix)
    key = _derive_key(password, salt, n_log2, r, p)
    aes = AESGCM(key)

    dst.write(header)
    written = len(header)

    counter = 0
    cur = _read_exact(src, chunk_size)
    while True:
        nxt = _read_exact(src, chunk_size) if len(cur) == chunk_size else b""
        is_last = 1 if not nxt else 0
        nonce = nonce_prefix + NONCE_COUNTER_STRUCT.pack(counter)
        aad = header + AAD_SUFFIX_STRUCT.pack(counter, is_last)
        ct = aes.encrypt(nonce, cur, aad)
        frame = FRAME_STRUCT.pack(len(ct), is_last)
        dst.write(frame)
        dst.write(ct)
        written += len(frame) + len(ct)
        if is_last:
            break
        counter += 1
        cur = nxt

    return written


def decrypt_stream(src: BinaryIO, dst: BinaryIO, password: str) -> int:
    """Stream-decrypt `src` into `dst`, returning total plaintext bytes written.

    Raises `WrongPasswordOrDamaged` on bad password, invalid header, truncation,
    trailing bytes, swapped chunks, or modified ciphertext.
    """
    header = _read_exact(src, HEADER_STRUCT.size)
    if len(header) != HEADER_STRUCT.size:
        raise WrongPasswordOrDamaged()

    magic, salt, n_log2, r, p, chunk_size, nonce_prefix = HEADER_STRUCT.unpack(header)
    if magic != MAGIC:
        raise WrongPasswordOrDamaged()
    if not (MIN_N_LOG2 <= n_log2 <= MAX_N_LOG2 and MIN_R <= r <= MAX_R and MIN_P <= p <= MAX_P):
        raise WrongPasswordOrDamaged()
    if not (MIN_CHUNK_SIZE <= chunk_size <= MAX_CHUNK_SIZE):
        raise WrongPasswordOrDamaged()

    key = _derive_key(password, salt, n_log2, r, p)
    aes = AESGCM(key)

    total = 0
    counter = 0
    saw_last = False

    while True:
        frame = _read_exact(src, FRAME_STRUCT.size)
        if not frame:
            raise WrongPasswordOrDamaged()
        if len(frame) != FRAME_STRUCT.size:
            raise WrongPasswordOrDamaged()

        ct_len, is_last = FRAME_STRUCT.unpack(frame)
        if is_last not in (0, 1):
            raise WrongPasswordOrDamaged()
        if ct_len < TAG_LEN or ct_len > chunk_size + TAG_LEN:
            raise WrongPasswordOrDamaged()

        ct = _read_exact(src, ct_len)
        if len(ct) != ct_len:
            raise WrongPasswordOrDamaged()

        nonce = nonce_prefix + NONCE_COUNTER_STRUCT.pack(counter)
        aad = header + AAD_SUFFIX_STRUCT.pack(counter, is_last)
        try:
            pt = aes.decrypt(nonce, ct, aad)
        except InvalidTag:
            raise WrongPasswordOrDamaged() from None

        dst.write(pt)
        total += len(pt)
        counter += 1

        if is_last == 1:
            saw_last = True
            if src.read(1):
                raise WrongPasswordOrDamaged()
            break

    if not saw_last:
        raise WrongPasswordOrDamaged()
    return total


def encrypt_file(
    src_path: Path,
    dst_path: Path,
    password: str,
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    n_log2: int = DEFAULT_N_LOG2,
    r: int = DEFAULT_R,
    p: int = DEFAULT_P,
) -> int:
    """Encrypt `src_path` atomically to `dst_path` (0o600)."""
    dst_path = Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dst_path.parent, prefix=f".{dst_path.name}.")
    tmp = Path(tmp_name)
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "wb") as out_fh, open(src_path, "rb") as in_fh:
            written = encrypt_stream(in_fh, out_fh, password, chunk_size=chunk_size, n_log2=n_log2, r=r, p=p)
            out_fh.flush()
            os.fsync(out_fh.fileno())
        os.replace(tmp, dst_path)
        return written
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def decrypt_file(src_path: Path, dst_path: Path, password: str) -> int:
    """Decrypt `src_path` atomically to `dst_path` (0o600), leaving `dst_path` untouched on failure."""
    dst_path = Path(dst_path)
    dst_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dst_path.parent, prefix=f".{dst_path.name}.")
    tmp = Path(tmp_name)
    try:
        os.chmod(tmp, 0o600)
        with os.fdopen(fd, "wb") as out_fh, open(src_path, "rb") as in_fh:
            total = decrypt_stream(in_fh, out_fh, password)
            out_fh.flush()
            os.fsync(out_fh.fileno())
        os.replace(tmp, dst_path)
        return total
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
