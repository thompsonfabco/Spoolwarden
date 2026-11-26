"""Tests for slicer path auto-detection, persistence, ConfigController, config_gui CLI, studio resolution, and re-pairing."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from fksync import __main__ as cli_main
from fksync import client, paths_config, store, studio
from fksync.paths_config import (
    SlicerEntry,
    detect_slicer_paths,
    load_slicer_paths,
    merge_detected,
    normalize_path,
    save_slicer_paths,
)


def _make_bambu_root(base: Path, variant: str = "BambuStudio", user_id: str = "1234567890") -> Path:
    root = base / variant
    (root / "user" / user_id / "filament").mkdir(parents=True, exist_ok=True)
    (root / "system" / "BBL" / "filament").mkdir(parents=True, exist_ok=True)
    return root


def _make_orca_root(root: Path, user_id: str = "default") -> Path:
    (root / "user" / user_id / "filament").mkdir(parents=True, exist_ok=True)
    (root / "system" / "BBL" / "filament").mkdir(parents=True, exist_ok=True)
    return root


def _make_orca_install_win(root: Path) -> Path:
    (root / "resources" / "profiles").mkdir(parents=True, exist_ok=True)
    (root / studio.ORCA_EXE).write_bytes(b"exe")
    (root / "resources" / "profiles" / studio.LIBRARY_OPC).write_bytes(b"opc")
    return root


def test_detect_slicer_paths_windows(tmp_path):
    appdata = tmp_path / "roaming"
    bambu = _make_bambu_root(appdata, "BambuStudio")
    beta = _make_bambu_root(appdata, "BambuStudioBeta")
    orca = _make_orca_root(appdata / "OrcaSlicer")
    pf = tmp_path / "pf"
    orca_inst = _make_orca_install_win(pf / "OrcaSlicer")

    entries = detect_slicer_paths(appdata, program_files=[pf], registry=lambda: [])
    by_kind_label = {(e.kind, e.label): normalize_path(e.path) for e in entries}

    assert by_kind_label[("bambu", "BambuStudio")] == normalize_path(bambu)
    assert by_kind_label[("bambu", "BambuStudioBeta")] == normalize_path(beta)
    assert by_kind_label[("orca", "OrcaSlicer")] == normalize_path(orca)
    assert by_kind_label[("orca_install", "Orca Slicer Install")] == normalize_path(orca_inst)
    assert all(e.enabled and not e.custom for e in entries)


def test_detect_slicer_paths_linux_layout(tmp_path):
    home = tmp_path / "home"
    xdg = home / ".config"
    bambu = _make_bambu_root(xdg, "BambuStudio")
    orca = _make_orca_root(xdg / "OrcaSlicer")
    opt_orca = tmp_path / "opt" / "OrcaSlicer" / "resources"
    (opt_orca / "profiles").mkdir(parents=True, exist_ok=True)
    (opt_orca / "profiles" / studio.LIBRARY_OPC).write_bytes(b"opc")

    layout = studio.LinuxLayout(
        home=home,
        xdg_config=xdg,
        system_flatpak=tmp_path / "sys-flatpak",
        share_dirs=(),
        opt_dirs=(tmp_path / "opt",),
        user_flatpak=tmp_path / "user-flatpak",
    )
    entries = detect_slicer_paths(None, layout=layout)
    paths_by_kind = {(e.kind, normalize_path(e.path)) for e in entries}
    assert ("bambu", normalize_path(bambu)) in paths_by_kind
    assert ("orca", normalize_path(orca)) in paths_by_kind
    assert ("orca_install", normalize_path(opt_orca)) in paths_by_kind


def test_merge_detected_preserves_existing_and_appends_new(tmp_path):
    p_bambu = tmp_path / "BambuStudio"
    p_beta = tmp_path / "BambuStudioBeta"
    p_custom = tmp_path / "CustomStudio"
    p_orca = tmp_path / "OrcaSlicer"

    existing = [
        SlicerEntry(kind="bambu", label="BambuStudio", path=str(p_bambu), enabled=False, custom=False),
        SlicerEntry(kind="bambu", label="EditedBeta", path=str(p_beta), enabled=True, custom=True),
        SlicerEntry(kind="orca", label="Custom", path=str(p_custom), enabled=True, custom=True),
    ]
    detected = [
        SlicerEntry(kind="bambu", label="BambuStudio", path=str(p_bambu), enabled=True, custom=False),
        SlicerEntry(kind="bambu", label="BambuStudioBeta", path=str(p_beta), enabled=True, custom=False),
        SlicerEntry(kind="orca", label="OrcaSlicer", path=str(p_orca), enabled=True, custom=False),
    ]

    merged = merge_detected(existing, detected)
    assert len(merged) == 4
    assert merged[0].enabled is False
    assert merged[1].label == "EditedBeta" and merged[1].custom is True
    assert merged[2].path == str(p_custom)
    assert merged[3].path == str(p_orca)


def test_save_and_load_slicer_paths_round_trip_and_orca_install_sync(tmp_path):
    dirs = store.AppDirs(tmp_path / "local")
    bambu_dir = tmp_path / "BambuStudio"
    orca_inst = tmp_path / "OrcaInstall"

    entries = [
        SlicerEntry(kind="bambu", label="BambuStudio", path=str(bambu_dir), enabled=True, custom=False),
        SlicerEntry(kind="orca_install", label="Orca Slicer Install", path=str(orca_inst), enabled=True, custom=True),
    ]
    cfg = save_slicer_paths(dirs, entries)
    assert cfg["orca_install"] == str(orca_inst)

    loaded = load_slicer_paths(dirs)
    assert loaded == entries

    # Disabling the orca_install entry removes cfg["orca_install"].
    entries[1] = SlicerEntry(kind="orca_install", label="Orca Slicer Install", path=str(orca_inst), enabled=False, custom=True)
    cfg2 = save_slicer_paths(dirs, entries)
    assert "orca_install" not in cfg2
    assert load_slicer_paths(dirs) == entries


def test_config_controller_crud_and_persistence(tmp_path):
    from fksync.config_gui import ConfigController

    dirs = store.AppDirs(tmp_path / "local")
    appdata = tmp_path / "roaming"
    bambu = _make_bambu_root(appdata, "BambuStudio")
    custom_dir = _make_bambu_root(tmp_path / "extra", "CustomBambu")
    mod_dir = _make_bambu_root(tmp_path / "moved", "MovedBambu")

    ctrl = ConfigController(dirs, appdata=appdata, program_files=(), registry=lambda: [])
    ctrl.load()
    assert len(ctrl.entries) == 1
    assert any(normalize_path(e.path) == normalize_path(bambu) for e in ctrl.entries)

    beta = _make_bambu_root(appdata, "BambuStudioBeta")
    added = ctrl.autodetect()
    assert added == 1
    assert any(normalize_path(e.path) == normalize_path(beta) for e in ctrl.entries)
    ctrl.remove(1)
    assert len(ctrl.entries) == 1

    ctrl.set_enabled(0, False)
    assert ctrl.entries[0].enabled is False
    ctrl.toggle(0)
    assert ctrl.entries[0].enabled is True

    ctrl.modify(0, str(mod_dir))
    assert normalize_path(ctrl.entries[0].path) == normalize_path(mod_dir)
    assert ctrl.entries[0].custom is True

    ctrl.add("bambu", str(custom_dir), label="Extra")
    assert len(ctrl.entries) == 2
    assert ctrl.entries[1].custom is True

    ctrl.remove(0)
    assert len(ctrl.entries) == 1
    assert normalize_path(ctrl.entries[0].path) == normalize_path(custom_dir)

    ctrl.save()
    reloaded = load_slicer_paths(dirs)
    assert len(reloaded) == 1
    assert normalize_path(reloaded[0].path) == normalize_path(custom_dir)


def test_config_gui_cli_apply_paths_and_detect_json(tmp_path, capsys):
    from fksync import config_gui

    local = tmp_path / "local"
    appdata = tmp_path / "roaming"
    bambu = _make_bambu_root(appdata, "BambuStudio")
    orca = _make_orca_root(appdata / "OrcaSlicer")

    rc = config_gui.main([
        "--localappdata", str(local),
        "--appdata", str(appdata),
        "--detect-json",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    detected = json.loads(out)
    assert isinstance(detected, list)
    detected_paths = {normalize_path(item["path"]) for item in detected}
    assert normalize_path(bambu) in detected_paths
    assert normalize_path(orca) in detected_paths

    payload_file = tmp_path / "slicer-paths.json"
    payload = [
        {"kind": "bambu", "label": "BambuStudio", "path": str(bambu), "enabled": True, "custom": False},
        {"kind": "orca", "label": "OrcaSlicer", "path": str(orca), "enabled": False, "custom": True},
    ]
    payload_file.write_text(json.dumps(payload), encoding="utf-8")

    rc2 = config_gui.main([
        "--localappdata", str(local),
        "--appdata", str(appdata),
        "--apply-paths", str(payload_file),
    ])
    assert rc2 == 0
    saved = load_slicer_paths(store.AppDirs(local / "filament-kit-sync"))
    assert len(saved) == 2
    assert saved[0].enabled is True
    assert saved[1].enabled is False and saved[1].custom is True


def test_studio_locate_secondaries_and_locate_orca_honor_slicer_paths(tmp_path):
    appdata = tmp_path / "roaming"
    bambu = _make_bambu_root(appdata, "BambuStudio", user_id="1111111111")
    beta = _make_bambu_root(appdata, "BambuStudioBeta", user_id="1111111111")
    custom_bambu = tmp_path / "custom_bambu"
    custom_bambu.mkdir(parents=True)
    orca = _make_orca_root(appdata / "OrcaSlicer")
    pf = tmp_path / "pf"
    orca_inst = _make_orca_install_win(pf / "OrcaSlicer")

    # Legacy behavior without slicer_paths still locates appdata roots.
    legacy_primary = studio.locate(appdata, {})
    assert legacy_primary.root in (bambu, beta)
    legacy_secs = studio.secondaries(appdata, legacy_primary, pin={})
    assert len(legacy_secs) == 1

    # With slicer_paths: disabled entries are skipped, custom directory is honored.
    pin = {
        "slicer_paths": [
            {"kind": "bambu", "label": "BambuStudio", "path": str(bambu), "enabled": False, "custom": False},
            {"kind": "bambu", "label": "BambuStudioBeta", "path": str(beta), "enabled": True, "custom": False},
            {"kind": "bambu", "label": "CustomBambu", "path": str(custom_bambu), "enabled": True, "custom": True},
            {"kind": "orca", "label": "OrcaSlicer", "path": str(orca), "enabled": True, "custom": False},
            {"kind": "orca_install", "label": "Orca Install", "path": str(orca_inst), "enabled": True, "custom": False},
        ]
    }
    primary = studio.locate(appdata, pin)
    assert normalize_path(primary.root) == normalize_path(beta)

    secs = studio.secondaries(appdata, primary, pin=pin)
    assert len(secs) == 1
    assert normalize_path(secs[0].root) == normalize_path(custom_bambu)
    assert secs[0].user_id == "custom"

    orca_folders = studio.locate_orca(appdata, pin)
    assert orca_folders is not None
    assert normalize_path(orca_folders.root) == normalize_path(orca)
    assert normalize_path(orca_folders.install) == normalize_path(orca_inst)

    # Disabling all orca entries returns None from locate_orca.
    pin_no_orca = {
        "slicer_paths": [
            {"kind": "bambu", "label": "BambuStudioBeta", "path": str(beta), "enabled": True, "custom": False},
            {"kind": "orca", "label": "OrcaSlicer", "path": str(orca), "enabled": False, "custom": False},
        ]
    }
    assert studio.locate_orca(appdata, pin_no_orca) is None


def test_finish_pair_preserves_slicer_paths_across_re_pairing(tmp_path, monkeypatch):
    dirs = store.AppDirs(tmp_path / "local")
    dirs.ensure()
    saved_paths = [
        {"kind": "bambu", "label": "BambuStudio", "path": str(tmp_path / "BambuStudio"), "enabled": True, "custom": False}
    ]
    store.save_config(dirs, {"host": "old.local", "port": 443, "slicer_paths": saved_paths})
    store.save_token(dirs, "old-token")

    class FakeClient:
        def __init__(self, cfg, token, timeout=20):
            pass

        def info(self):
            return {"name": "Test Hub"}

        def state(self):
            return {"you": "pc-1"}

    monkeypatch.setattr(client, "Client", FakeClient)

    new_cfg = {"host": "new.local", "port": 8443}
    rc = cli_main._finish_pair(dirs, new_cfg, "new-token", appdata=tmp_path / "roaming")
    assert rc == 0
    final_cfg = store.load_config(dirs)
    assert final_cfg["host"] == "new.local"
    assert final_cfg["slicer_paths"] == saved_paths
