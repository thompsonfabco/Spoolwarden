"""Focused unit tests for Brief 49 fixes: FA-109, FA-111, FA-130, FA-124, FA-125."""
from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

from filament_kit import (
    backup,
    config,
    intake,
    intake_history,
    local_reader,
    reader_flash,
    reader_setup,
    setup_helper,
    web_access,
)
from filament_kit.web import intake_app


@pytest.fixture(autouse=True)
def fk_env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt", "run"):
        d = tmp_path / name
        d.mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(d))


# ---------- FA-109 ----------

def test_fa109_etc_files_includes_local_intake_token():
    assert "local_intake_token" in web_access.ETC_FILES


def test_fa109_write_token_grants_web_read(monkeypatch):
    seen = []
    monkeypatch.setattr(config, "grant_web_read", lambda p: seen.append(p))
    dest = local_reader.write_token("secret-token-123")
    assert dest.name == local_reader.TOKEN_FILE
    assert dest.read_text().strip() == "secret-token-123"
    assert len(seen) == 1


# ---------- FA-111 ----------

@pytest.mark.parametrize("mods,reader,expected", [
    ({"spoolman": True, "server": True, "ai_intake": True, "local_intake": False}, "local", []),
    ({"spoolman": True, "server": True, "ai_intake": False, "local_intake": True}, "cloud", []),
    ({"spoolman": True, "server": True, "ai_intake": True, "local_intake": False}, "auto", ["cloud"]),
    ({"spoolman": True, "server": True, "ai_intake": True, "local_intake": False}, "", ["cloud"]),
    ({"spoolman": True, "server": True, "ai_intake": True, "local_intake": True}, "auto", ["local", "cloud"]),
])
def test_fa111_reader_order_no_silent_fallback_for_explicit_single_reader(mods, reader, expected):
    assert intake.reader_order({"modules": mods, "intake": {"reader": reader}}) == expected


# ---------- FA-130 ----------

def test_fa130_read_labels_respects_stash_flag(monkeypatch):
    stashed = []
    monkeypatch.setattr(intake, "_sweep_failed", lambda: None)
    monkeypatch.setattr(intake, "_forget", lambda rid: None)
    monkeypatch.setattr(intake, "_call", lambda *a, **kw: {"items": [], "questions": []})
    monkeypatch.setattr(intake_history, "stash_read", lambda *a, **kw: stashed.append((a, kw)))

    intake.read_labels(None, [b"img"], "", None, read_id="r1", stash=False)
    assert stashed == []

    intake.read_labels(None, [b"img"], "", None, read_id="r2")
    assert len(stashed) == 1
    assert stashed[0][0][0] == "r2"


@pytest.mark.anyio
async def test_fa130_intake_app_passes_stash_false_when_history_off(monkeypatch):
    calls = []
    monkeypatch.setattr(intake, "check_photos", lambda photos: [b"img"])
    monkeypatch.setattr(intake, "read_context", lambda *a, **kw: {})

    def fake_read_labels(*args, **kwargs):
        calls.append(kwargs)
        return {"items": [], "questions": [], "read_id": "abc"}

    monkeypatch.setattr(intake, "read_labels", fake_read_labels)

    class _FakeForm:
        def getlist(self, key):
            return []

        def get(self, key):
            return ""

    class _FakeFormCtx:
        async def __aenter__(self):
            return _FakeForm()

        async def __aexit__(self, *exc):
            return False

    class _FakeReq:
        def form(self, **kw):
            return _FakeFormCtx()

    app = object.__new__(intake_app.IntakeApp)
    app.history = False
    app._kit = SimpleNamespace()
    app.claude = None
    app.tools = SimpleNamespace(
        sm=None,
        cfg=lambda: {"modules": {"spoolman": True, "server": True, "local_intake": True}},
    )
    app._station = lambda: None
    app._orders = lambda: []

    out = await app.read(_FakeReq())
    assert out["read_id"] == "abc"
    assert len(calls) == 1
    assert calls[0]["stash"] is False


# ---------- FA-124 ----------

def test_fa124_backup_excludes_reader_setup_json():
    assert "reader-setup.json" in backup.EXCLUDE["etc/filament-kit"]


def test_fa124_setup_helper_sweeps_stale_reader_setup_when_idle(monkeypatch):
    etc = config.paths().etc
    stale_json = etc / reader_flash.OLD_PENDING
    stale_json.write_text('{"ssid": "net", "pass": "secret"}')
    monkeypatch.setattr(setup_helper, "_no_systemd", lambda: True)

    reply = setup_helper.handle({"op": "backup_last"})
    assert reply["ok"] is True
    assert not stale_json.exists()


def test_fa124_setup_helper_keeps_pending_when_reader_setup_running(monkeypatch):
    etc = config.paths().etc
    stale_json = etc / reader_flash.OLD_PENDING
    stale_json.write_text('{"ssid": "net", "pass": "secret"}')
    monkeypatch.setattr(setup_helper, "_no_systemd", lambda: False)
    monkeypatch.setattr(setup_helper, "_running_jobs", lambda: ["reader_setup"])

    setup_helper._sweep_stale_reader_pending()
    assert stale_json.exists()


# ---------- FA-125 ----------

def _setup_reader_usb_mocks(monkeypatch, tmp_path):
    img = tmp_path / "fw.bin"
    img.write_bytes(b"fw")
    monkeypatch.setattr(reader_setup, "check_printer", lambda p: None)
    monkeypatch.setattr(reader_setup, "secrets_h", lambda *a: "")
    monkeypatch.setattr(reader_flash, "bundled_image", lambda: (img, "1.0.0"))
    monkeypatch.setattr(reader_setup, "find_readers", lambda: [SimpleNamespace(port="/dev/ttyACM0")])
    monkeypatch.setattr(setup_helper, "_not_applying", lambda: None)
    monkeypatch.setattr(config, "load", lambda: {"port": 8710})


def test_fa125_reader_usb_validates_reader_base_before_pending_or_job(monkeypatch, tmp_path):
    _setup_reader_usb_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(reader_setup, "reader_base", lambda cfg: (_ for _ in ()).throw(
        reader_setup.SetupError("no .local name on cert")
    ))
    wrote = []
    started = []
    monkeypatch.setattr(reader_flash, "write_pending", lambda data: wrote.append(data))
    monkeypatch.setattr(setup_helper, "_start_job", lambda *a, **kw: started.append((a, kw)))

    with pytest.raises(setup_helper.Refused) as exc:
        setup_helper._reader_usb({"ssid": "MyWiFi", "pass": "secret123", "buzzer": "high"})
    assert 400 <= exc.value.status < 500
    assert "no .local name on cert" in exc.value.message
    assert wrote == []
    assert started == []


def test_fa125_reader_usb_passes_checked_true_to_start_job(monkeypatch, tmp_path):
    _setup_reader_usb_mocks(monkeypatch, tmp_path)
    monkeypatch.setattr(reader_setup, "reader_base", lambda cfg: "https://hub.local:8710/reader")
    wrote = []
    started = []
    monkeypatch.setattr(reader_flash, "write_pending", lambda data: wrote.append(data))
    monkeypatch.setattr(setup_helper, "_start_job", lambda *a, **kw: started.append((a, kw)))

    res = setup_helper._reader_usb({"ssid": "MyWiFi", "pass": "secret123", "buzzer": "high"})
    assert res == {"name": "reader_setup"}
    assert len(wrote) == 1
    assert started == [(("reader_setup",), {"checked": True})]
