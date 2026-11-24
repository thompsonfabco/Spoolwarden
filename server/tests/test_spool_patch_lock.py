"""Prove P4-1: concurrent PATCH /api/spools/{id} (inventory.update_spool) serializes per-spool extra RMW."""
from __future__ import annotations

import concurrent.futures
import json
import threading
import time

import pytest

from filament_kit import config, inventory


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt", "backups"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    monkeypatch.setenv("FK_NO_SYSTEMD", "1")
    config.save({"placement": {"storage": ["Box 1", "Box 2"]}})
    return tmp_path


class SlowFakeSpoolman:
    def __init__(self):
        self._lock = threading.Lock()
        self.spool = {
            "id": 7,
            "location": "AMS A1",
            "remaining_weight": 500.0,
            "spool_weight": 200.0,
            "filament": {"material": "PLA", "spool_weight": 200.0, "weight": 1000.0},
            "extra": {
                "prev_location": json.dumps("Box 1"),
                "prev_since": json.dumps("2026-01-01T00:00:00Z"),
                "custom_keep": json.dumps("preserve_me"),
            },
        }
        self.in_flight = 0
        self.max_in_flight = 0

    def get(self, path: str):
        if path == "spool":
            return [dict(self.spool)]
        assert path == "spool/7"
        with self._lock:
            self.in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self.in_flight)
            snap = json.loads(json.dumps(self.spool))
        time.sleep(0.03)
        return snap

    def patch(self, path: str, body: dict):
        assert path == "spool/7"
        with self._lock:
            self.spool.update(body)
            self.in_flight -= 1
            return json.loads(json.dumps(self.spool))


def test_p4_1_concurrent_update_spool_serializes_extra_rmw(env):
    sm = SlowFakeSpoolman()

    def call_home():
        return inventory.update_spool(sm, 7, home="Box 2", dry=False, locations=["Box 1", "Box 2"])

    def call_weight():
        return inventory.update_spool(sm, 7, remaining=420.0, dry=False, locations=["Box 1", "Box 2"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(call_home)
        f2 = pool.submit(call_weight)
        r1 = f1.result(timeout=5)
        r2 = f2.result(timeout=5)

    assert sm.max_in_flight == 1, f"Concurrent update_spool overlapped (max_in_flight={sm.max_in_flight})"
    assert sm.spool["remaining_weight"] == 420.0
    assert json.loads(sm.spool["extra"]["prev_location"]) == "Box 2"
    assert json.loads(sm.spool["extra"]["custom_keep"]) == "preserve_me"
    assert r1["id"] == 7 and r2["id"] == 7
