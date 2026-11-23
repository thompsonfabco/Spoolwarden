"""Prove P2-1: state/config/history writers use atomic temp-file + os.replace, not in-place truncation."""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from filament_kit import config, intake_history
from filament_kit.bridge import manual
from filament_kit.profiles import inapp as inapp_mod, statexml, updates
from filament_kit.station import server_api


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt", "backups"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    monkeypatch.setenv("FK_NO_SYSTEMD", "1")
    return tmp_path


def _record_replaces(monkeypatch):
    calls: list[tuple[str, str]] = []
    real_replace = os.replace

    def spy(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    return calls


def test_p2_1_statexml_write_plain_is_atomic(tmp_path, monkeypatch):
    calls = _record_replaces(monkeypatch)
    target = tmp_path / "marker.json"
    statexml.write_plain(target, {"ok": True}, also_json=True)
    dsts = [Path(d) for _, d in calls]
    assert statexml.xml_path(target) in dsts
    assert target in dsts


def test_p2_1_updates_hold_is_atomic(tmp_path, monkeypatch):
    calls = _record_replaces(monkeypatch)
    src = tmp_path / "src.json"
    dest = tmp_path / "dest.json"
    src.write_text('{"name":"A"}', encoding="utf-8")
    updates._hold(src, dest, {"filament_notes": f"{inapp_mod.MARK}\nmy note"}, {"name": "A", "filament_notes": "base"})
    assert str(dest) in [d for _, d in calls]
    assert "my note" in json.loads(dest.read_text(encoding="utf-8"))["filament_notes"]


def test_p2_1_intake_history_made_marker_is_atomic(env, monkeypatch):
    calls = _record_replaces(monkeypatch)
    rid = intake_history.record_save(
        [{"values": {"brand": "B", "name": "N", "material": "PLA"}, "spool_ids": [1], "filament_id": 1}],
        source="manual",
        tag=None,
        weight=None,
    )
    assert rid is not None
    made = intake_history._dir(rid) / "made"
    assert str(made) in [d for _, d in calls]


def test_p2_1_manual_answer_offer_is_atomic(env, monkeypatch):
    calls = _record_replaces(monkeypatch)
    mgr = manual.ManualMaps(None)
    res = mgr.answer_offer("SER123", True)
    assert res["share"] is True
    offered = config.paths().state / "printers" / "SER123" / manual.OFFERED
    assert str(offered) in [d for _, d in calls]


def test_p2_1_station_server_api_writes_are_atomic(env, monkeypatch):
    calls = _record_replaces(monkeypatch)
    server_api.pairing_dir().mkdir(parents=True, exist_ok=True)
    server_api._write_server({"url": "https://hub.local:8742"}, old=True)
    dsts = [Path(d) for _, d in calls]
    assert server_api.server_file() in dsts
    assert server_api.old_server_file() in dsts
    assert server_api._old_sha_file() in dsts
