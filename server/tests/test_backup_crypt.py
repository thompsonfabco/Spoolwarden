"""Tests for streamed backup encryption (backup_crypt.py), password handling, and backup_tab routes."""
from __future__ import annotations

import io
import os
import sqlite3
import stat
import struct
import zipfile
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from filament_kit import backup, backup_crypt, config, setup_code, setup_helper, setup_page


@pytest.fixture
def kit(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    monkeypatch.setenv("FK_BACKUPS", str(tmp_path / "backups"))
    monkeypatch.setenv("FK_SPOOLMAN_DB", str(tmp_path / "spoolman" / "spoolman.db"))
    monkeypatch.setenv("FK_NO_SYSTEMD", "1")
    monkeypatch.setattr(backup, "host", lambda: "kitbox")
    cfg = config.load()
    cfg["modules"] = {"spoolman": True, "server": True, "printers": True}
    config.save(cfg)
    (tmp_path / "etc" / "cert.pem").write_text("CERT\n")
    (tmp_path / "state" / "devices.json").write_text('[{"id": "d1"}]\n')
    (tmp_path / "spoolman").mkdir()
    db = sqlite3.connect(tmp_path / "spoolman" / "spoolman.db")
    db.execute("create table spool (id integer, name text)")
    db.execute("insert into spool values (1, 'PLA')")
    db.commit()
    db.close()
    return tmp_path


def test_stream_roundtrip_multi_chunk_and_empty():
    for payload in (b"", b"hello", b"A" * 4096, os.urandom(4096 * 3 + 17)):
        enc = io.BytesIO()
        backup_crypt.encrypt_stream(
            io.BytesIO(payload),
            enc,
            "correct-horse-battery",
            chunk_size=4096,
            n_log2=10,
        )
        assert enc.getvalue().startswith(backup_crypt.MAGIC)
        dec = io.BytesIO()
        total = backup_crypt.decrypt_stream(io.BytesIO(enc.getvalue()), dec, "correct-horse-battery")
        assert total == len(payload)
        assert dec.getvalue() == payload


def test_wrong_password_and_tampering_raise_and_leave_dst_untouched(tmp_path):
    src = tmp_path / "plain.bin"
    enc = tmp_path / "plain.bin.enc"
    dst = tmp_path / "out.bin"
    dst.write_bytes(b"keep-me")
    payload = b"0123456789abcdef" * 600
    src.write_bytes(payload)

    backup_crypt.encrypt_file(src, enc, "right-password-123", chunk_size=4096, n_log2=10)
    raw = enc.read_bytes()

    # Wrong password
    with pytest.raises(backup_crypt.WrongPasswordOrDamaged) as exc:
        backup_crypt.decrypt_file(enc, dst, "wrong-password-123")
    assert str(exc.value) == backup_crypt.WRONG_MESSAGE
    assert dst.read_bytes() == b"keep-me"

    # Truncated / cut file (header, mid-chunk, between chunks)
    for cut in (10, 40, len(raw) - 1, len(raw) - 20):
        cut_file = tmp_path / f"cut-{cut}.enc"
        cut_file.write_bytes(raw[:cut])
        with pytest.raises(backup_crypt.WrongPasswordOrDamaged):
            backup_crypt.decrypt_file(cut_file, dst, "right-password-123")
        assert dst.read_bytes() == b"keep-me"

    # Changed byte in ciphertext / tag / header
    for pos in (0, 20, len(raw) - 5):
        mod = bytearray(raw)
        mod[pos] ^= 0x01
        mod_file = tmp_path / f"mod-{pos}.enc"
        mod_file.write_bytes(bytes(mod))
        with pytest.raises(backup_crypt.WrongPasswordOrDamaged):
            backup_crypt.decrypt_file(mod_file, dst, "right-password-123")

    # Trailing byte
    trail = tmp_path / "trail.enc"
    trail.write_bytes(raw + b"\x00")
    with pytest.raises(backup_crypt.WrongPasswordOrDamaged):
        backup_crypt.decrypt_file(trail, dst, "right-password-123")


def test_swapped_chunks_fail(tmp_path):
    src = tmp_path / "chunks.bin"
    enc = tmp_path / "chunks.bin.enc"
    dst = tmp_path / "out.bin"
    src.write_bytes(b"A" * 4096 + b"B" * 4096 + b"C" * 100)
    backup_crypt.encrypt_file(src, enc, "right-password-123", chunk_size=4096, n_log2=10)
    raw = bytearray(enc.read_bytes())

    hdr_len = backup_crypt.HEADER_STRUCT.size
    ct0_len, _ = backup_crypt.FRAME_STRUCT.unpack_from(raw, hdr_len)
    c0_start = hdr_len
    c0_end = c0_start + backup_crypt.FRAME_STRUCT.size + ct0_len
    ct1_len, _ = backup_crypt.FRAME_STRUCT.unpack_from(raw, c0_end)
    c1_end = c0_end + backup_crypt.FRAME_STRUCT.size + ct1_len

    swapped = bytes(raw[:c0_start] + raw[c0_end:c1_end] + raw[c0_start:c0_end] + raw[c1_end:])
    swapped_file = tmp_path / "swapped.enc"
    swapped_file.write_bytes(swapped)
    with pytest.raises(backup_crypt.WrongPasswordOrDamaged):
        backup_crypt.decrypt_file(swapped_file, dst, "right-password-123")


def test_password_file_permissions_and_exclusion(kit):
    with pytest.raises(ValueError):
        backup.write_password("short")
    p = backup.write_password("secret-backup-pw")
    assert p == kit / "etc" / "backup_password"
    if os.name != "nt":
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert backup.password_set()

    enc_path = backup.create()
    assert enc_path.name.endswith(".zip.enc")
    assert backup_crypt.is_encrypted(enc_path)

    rows = backup.listing()
    assert len(rows) == 1
    assert rows[0].encrypted is True

    # Decrypt and verify backup_password is excluded from the zip
    plain = kit / "dec.zip"
    backup_crypt.decrypt_file(enc_path, plain, "secret-backup-pw")
    with zipfile.ZipFile(plain) as zf:
        assert "files/etc/filament-kit/backup_password" not in zf.namelist()
    backup.verify(plain)


def test_restore_encrypted_adopts_password_and_fails_safely(kit):
    backup.write_password("restore-pw-1234")
    enc_path = backup.create()

    # Mutate state and remove password
    (kit / "state" / "devices.json").write_text("[]\n")
    assert backup.remove_password() is True
    assert not backup.password_set()

    # Wrong password fails before changing anything
    out_lines = []
    rc = backup.restore(enc_path, yes=True, password="wrong-password", out=out_lines.append, run=lambda _c: 0)
    assert rc == 1
    assert any("Wrong password, or the file is damaged" in line for line in out_lines)
    assert (kit / "state" / "devices.json").read_text() == "[]\n"

    # Right password restores state and adopts password because none was set
    rc = backup.restore(enc_path, yes=True, password="restore-pw-1234", out=lambda _m: None, run=lambda _c: 0)
    assert rc == 0
    assert (kit / "state" / "devices.json").read_text() == '[{"id": "d1"}]\n'
    assert backup.read_password() == "restore-pw-1234"


def test_backup_tab_routes(kit, monkeypatch):
    from test_setup_page import BASE, H, HOME, HOSTNAME
    setup_code.write("12345678")

    class _FakeHelper:
        def __call__(self, req):
            return setup_helper.handle(req)

    page = setup_page.SetupPage(helper=_FakeHelper(), hosts=lambda: {HOSTNAME})
    client = TestClient(Starlette(routes=page.routes()), base_url=BASE, client=("192.168.1.50", 50000))

    hdr = {**H, "X-Setup-Code": "12345678"}

    # Too short password -> 400 with code="too_short"
    r = client.post("/setup/api/backups/password", json={"code": "12345678", "password": "short"}, headers=hdr)
    assert r.status_code == 400
    assert r.json()["code"] == "too_short"

    # Set password -> 200 {set: true}
    r = client.post("/setup/api/backups/password", json={"code": "12345678", "password": "long-enough-pw"}, headers=hdr)
    assert r.status_code == 200
    assert r.json() == {"set": True}

    # Create encrypted backup and list it
    backup.create()
    r = client.post("/setup/api/backups/list", json={"code": "12345678"}, headers=hdr)
    assert r.status_code == 200
    data = r.json()
    assert data["password_set"] is True
    assert len(data["backups"]) == 1
    assert data["backups"][0]["encrypted"] is True

    # Remove password -> 200 {set: false}
    r = client.post("/setup/api/backups/password/remove", json={"code": "12345678"}, headers=hdr)
    assert r.status_code == 200
    assert r.json() == {"set": False}

    # Upload encrypted backup when no password is set -> needs_password: True
    enc_file = backup.backups_dir() / data["backups"][0]["name"]
    r = client.post(
        "/setup/api/restore/upload",
        content=enc_file.read_bytes(),
        headers=hdr,
    )
    assert r.status_code == 200
    assert r.json()["needs_password"] is True

    # Wrong restore password -> 400 with code="wrong_password"
    r = client.post("/setup/api/restore/password", json={"code": "12345678", "password": "wrong-pw-123"}, headers=hdr)
    assert r.status_code == 400
    assert r.json()["code"] == "wrong_password"
    assert "Wrong password, or the file is damaged" in r.json()["error"]

    # Right restore password -> 200 with summary
    r = client.post("/setup/api/restore/password", json={"code": "12345678", "password": "long-enough-pw"}, headers=hdr)
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert "sha256" in r.json()
