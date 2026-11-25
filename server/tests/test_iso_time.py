"""Tests for filament_kit.iso_time."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from filament_kit import iso_time


def test_utc_now_handles_none_naive_and_aware():
    cur = iso_time.utc_now()
    assert cur.tzinfo == timezone.utc

    naive = datetime(2026, 11, 25, 12, 30, 0)
    assert iso_time.utc_now(naive) == datetime(2026, 11, 25, 12, 30, 0, tzinfo=timezone.utc)

    plus2 = timezone(timedelta(hours=2))
    aware = datetime(2026, 11, 25, 14, 30, 0, tzinfo=plus2)
    assert iso_time.utc_now(aware) == datetime(2026, 11, 25, 12, 30, 0, tzinfo=timezone.utc)


def test_parse_utc_normalizes_z_offsets_naive_and_rejects_bad():
    assert iso_time.parse_utc(" 2026-11-25T12:00:00Z ") == datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
    assert iso_time.parse_utc("2026-11-25T14:00:00+02:00") == datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
    assert iso_time.parse_utc("2026-11-25T12:00:00") == datetime(2026, 11, 25, 12, 0, 0, tzinfo=timezone.utc)
    for bad in (None, "", "   ", 123, "not-a-date"):
        assert iso_time.parse_utc(bad) is None
