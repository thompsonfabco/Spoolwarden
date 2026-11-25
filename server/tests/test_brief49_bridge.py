"""Focused unit tests for brief 49 bridge fixes: FA-110, FA-137, FA-138."""
from __future__ import annotations

import logging
from types import SimpleNamespace

from filament_kit.bridge import ams, calib3mf, mapping, registry
from filament_kit.bridge.ams import SlotMap
from filament_kit.bridge.registry import Printer


SERIAL = "01P00A350100001"


# ---------------------------------------------------------------------- FA-110
def test_scrub_problems_redacts_serial_path_and_other_job(monkeypatch):
    monkeypatch.setattr(registry, "log_label", lambda s: "printer 1 (H2D)")
    raw = [
        f"could not delete /cache/fk-calib-{SERIAL}.gcode.3mf: Connection reset",
        f"{calib3mf.OTHER_JOB} (the printer is running 'Secret_Customer_Part.gcode.3mf'; it was not stopped)",
        f"printer {SERIAL} reported an error",
    ]
    snapshot = list(raw)

    scrubbed = calib3mf.scrub_problems(SERIAL, raw)

    assert raw == snapshot
    assert len(scrubbed) == 3
    for line in scrubbed:
        assert SERIAL not in line
        assert "Secret_Customer_Part" not in line
        assert f"fk-calib-{SERIAL}" not in line
    assert "/cache/fk-calib-<redacted>" in scrubbed[0]
    assert "running <redacted>" in scrubbed[1]
    assert "printer 1 (H2D)" in scrubbed[2]


def test_delete_leftover_job_logs_scrubbed_problems(monkeypatch, caplog):
    monkeypatch.setattr(registry, "log_label", lambda s: "printer 1 (H2D)")
    monkeypatch.setattr(mapping.snapshot, "load", lambda s: {"kind": "calibration"})
    monkeypatch.setattr(
        calib3mf,
        "delete_leftover",
        lambda s: [f"could not delete /cache/fk-calib-{SERIAL}.gcode.3mf: timeout"],
    )

    with caplog.at_level(logging.WARNING, logger=mapping.__name__):
        mapping._delete_leftover_job(SERIAL)

    assert any("/cache/fk-calib-<redacted>" in rec.message for rec in caplog.records)
    assert all(SERIAL not in rec.message for rec in caplog.records)


# ---------------------------------------------------------------------- FA-137
def test_calib3mf_ladder_returns_to_first_file_when_second_also_refuses(monkeypatch):
    monkeypatch.setattr(registry, "log_label", lambda s: "printer 1 (H2D)")
    monkeypatch.setattr(calib3mf, "job_files", lambda mi, proven=None: ["minimal", "project"])

    calls: list[tuple[str, str]] = []

    def fake_attempt(manager, serial, slotmap, printer, should_stop, hw, mi, candidates, key, tried, last_print, file):
        calls.append((key, file))
        if key == "legacy":
            return calib3mf.CalibResult(
                sent_scheme=key,
                job_file=file,
                problems=[calib3mf.REFUSED],
                refused_reason="invalid 3mf",
                retry=True,
                restored=True,
            )
        return calib3mf.CalibResult(
            sent_scheme=key,
            job_file=file,
            scheme=key,
            consistent=True,
            restored=True,
            mapping=[0, 1, 2, 3],
            decoding={"A1": 0, "A2": 1, "A3": 2, "A4": 3},
            answered_file=file,
        )

    monkeypatch.setattr(calib3mf, "_attempt", fake_attempt)
    fake_hw = SimpleNamespace(
        schemes={"legacy": object(), "shift8": object()},
        model=lambda m: SimpleNamespace(numbering=["shift8", "legacy"], known=True, slicer=object(), job_file="minimal"),
    )
    monkeypatch.setattr(calib3mf.hardware, "load", lambda: fake_hw)

    smap = SlotMap(slots={"A1": {"ams_id": 0, "tray_id": 0}})
    printer = Printer(serial=SERIAL, model="H2D")
    res = calib3mf.run(manager=None, serial=SERIAL, slotmap=smap, printer=printer)

    assert calls == [
        ("legacy", "minimal"),
        ("legacy", "project"),
        ("shift8", "minimal"),
    ]
    assert res.consistent is True
    assert res.job_file == "minimal"


# ---------------------------------------------------------------------- FA-138
def test_migrate_clash_labels_rewrites_legacy_hash_suffix():
    m = SlotMap(
        slots={
            "A1": {"ams_id": 0, "tray_id": 0, "global_id": 0},
            "A1#1": {"ams_id": 1, "tray_id": 0, "global_id": 4},
        },
        by_user={"A1#1": {"by": "user", "at": "2026-01-01"}},
        learned={"4": "A1#1", "0": "A1"},
    )
    labels = frozenset({"A1", "A1-1"})

    changed = mapping._migrate_clash_labels(m, labels)

    assert changed is True
    assert "A1#1" not in m.slots
    assert m.slots["A1-1"]["ams_id"] == 1
    assert "A1#1" not in m.by_user
    assert m.by_user["A1-1"] == {"by": "user", "at": "2026-01-01"}
    assert m.learned == {"4": "A1-1", "0": "A1"}


def test_migrate_clash_labels_skips_when_hash_in_layout_or_dash_missing():
    m1 = SlotMap(slots={"A1#1": {"ams_id": 1, "tray_id": 0, "global_id": 4}})
    assert mapping._migrate_clash_labels(m1, frozenset({"A1#1", "A1-1"})) is False
    assert "A1#1" in m1.slots

    m2 = SlotMap(slots={"A1#1": {"ams_id": 1, "tray_id": 0, "global_id": 4}})
    assert mapping._migrate_clash_labels(m2, frozenset({"A1"})) is False
    assert "A1#1" in m2.slots


def test_layout_watch_check_map_migrates_once_without_marking_stale(monkeypatch):
    saved_maps: list[SlotMap] = []
    fp = [[0, "ams", 4], [1, "ams", 4]]
    stored = SlotMap(
        slots={"A1": {"ams_id": 0, "tray_id": 0, "global_id": 0}, "A1#1": {"ams_id": 1, "tray_id": 0, "global_id": 4}},
        calibrated=True,
        observe=False,
        fingerprint=fp,
        stale=False,
    )
    monkeypatch.setattr(ams, "load_map", lambda s: stored)
    monkeypatch.setattr(ams, "save_map", lambda s, m: saved_maps.append(m))

    watch = mapping.LayoutWatch(manager=None, mapper=None)
    due = watch._check_map(SERIAL, fp, labels=frozenset({"A1", "A1-1"}))

    assert due is False
    assert stored.stale is False
    assert "A1-1" in stored.slots
    assert "A1#1" not in stored.slots
    assert len(saved_maps) == 1
