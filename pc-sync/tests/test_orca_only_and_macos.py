"""Tests for macOS layout/running/data_dir and Orca-only Slicer Sync."""
import io
import json
import subprocess
import zipfile
from pathlib import Path

import pytest

import orca_fixture
from fksync import __main__ as cli
from fksync import client, installer, store, studio, sync, treehash
from test_orca_sync import N, OINFO, OPRESET, OrcaClient, _zip
from test_sync import STAMP, T0


# --- macOS support -------------------------------------------------------------------------------


def test_mac_layout_roots_and_installs(tmp_path):
    home = tmp_path / "home"
    apps = tmp_path / "Applications"
    lay = studio.MacLayout.from_env({"HOME": str(home)}, applications=apps)

    assert lay.studio_roots() == [
        ("BambuStudio", home / "Library" / "Application Support" / "BambuStudio"),
        ("BambuStudioBeta", home / "Library" / "Application Support" / "BambuStudioBeta"),
    ]
    assert lay.orca_roots() == [
        ("OrcaSlicer", home / "Library" / "Application Support" / "OrcaSlicer", None),
    ]
    assert lay.orca_installs()[0] == apps / "OrcaSlicer.app" / "Contents" / "Resources"

    # End-to-end locate + locate_orca with MacLayout
    bambu_fil = home / "Library" / "Application Support" / "BambuStudio" / "user" / "default" / "filament"
    bambu_fil.mkdir(parents=True)
    f = studio.locate(None, layout=lay)
    assert f.variant == "BambuStudio" and f.user_filament == bambu_fil

    orca_root = home / "Library" / "Application Support" / "OrcaSlicer"
    orca_fil = orca_root / "user" / "default" / "filament"
    orca_fil.mkdir(parents=True)
    opc = apps / "OrcaSlicer.app" / "Contents" / "Resources" / "profiles" / studio.LIBRARY_OPC
    opc.parent.mkdir(parents=True)
    opc.write_bytes(b"opc")

    o = studio.locate_orca(None, layout=lay)
    assert o is not None
    assert o.user_filament == orca_fil
    assert o.install == apps / "OrcaSlicer.app" / "Contents" / "Resources"
    assert o.library_opc == opc


def test_mac_running_pgrep_and_ps_fallback(monkeypatch):
    monkeypatch.setattr(studio.sys, "platform", "darwin")

    # pgrep exit 0 -> running
    assert studio.running(runner=lambda cmd: subprocess.CompletedProcess(cmd, 0, stdout="123\n")) is True
    # pgrep exit 1 -> not running
    assert studio.running(runner=lambda cmd: subprocess.CompletedProcess(cmd, 1, stdout="")) is False

    # pgrep missing (OSError) -> falls back to ps -Ao comm=
    def ps_only(cmd):
        if cmd[0] == "pgrep":
            raise FileNotFoundError("pgrep")
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout="/sbin/launchd\n/Applications/OrcaSlicer.app/Contents/MacOS/OrcaSlicer\n",
        )

    assert studio.running(runner=ps_only) is False
    assert studio.orca_running(runner=ps_only) is True


def test_mac_data_dir(tmp_path):
    assert store.mac_data_dir({"HOME": str(tmp_path / "u")}) == (
        tmp_path / "u" / "Library" / "Application Support" / "filament-kit-sync"
    )


# --- Orca-only installer & sync ------------------------------------------------------------------


def _write_build(build_dir: Path, orca_presets: dict, stamp: str = STAMP, orca_live_hash: str | None = None):
    build_dir.mkdir(parents=True, exist_ok=True)
    meta = {"stamp": stamp, "stale": []}
    if orca_live_hash is not None:
        meta["orca_live_hash"] = orca_live_hash
    (build_dir / "BUILD.json").write_text(json.dumps(meta), encoding="utf-8")
    (build_dir / "orca").mkdir(exist_ok=True)
    for name, data in orca_presets.items():
        (build_dir / "orca" / f"{name}.json").write_text(json.dumps(data, indent=4), encoding="utf-8")
        (build_dir / "orca" / f"{name}.info").write_text("sync_info = \nsetting_id = \n", encoding="utf-8")


def test_orca_only_installer(tmp_path):
    orca = orca_fixture.make(
        tmp_path / "appdata",
        presets={N: (OPRESET, OINFO)},
        install=tmp_path / "pf" / "OrcaSlicer 2.5.0",
        opc=orca_fixture.opc_blob(orca_fixture.LIBRARY),
    )
    dirs = store.AppDirs(tmp_path / "local" / "filament-kit-sync")
    dirs.ensure()
    # Pre-existing base/ snapshot should be preserved via skip=("base",)
    (dirs.installed / "base").mkdir(parents=True)
    (dirs.installed / "base" / "Keep.json").write_text("{}", encoding="utf-8")

    new_name = "Acme PETG @SV08 MAX 0.4"
    new_preset = dict(OPRESET, name=new_name, nozzle_temperature=["240"])
    build_dir = tmp_path / "build"
    _write_build(build_dir, {N: dict(OPRESET, nozzle_temperature=["225"]), new_name: new_preset})

    # running=lambda: True must be ignored when folders is None (Bambu Studio is not present/synced)
    res = installer.install(
        build_dir,
        None,
        dirs,
        running=lambda: True,
        stamp_now="20261126-010000",
        orca=orca,
        orca_running=lambda: False,
    )
    assert res.backup is None
    assert res.backup_orca is not None and res.backup_orca.is_dir()
    assert res.installed == []
    assert res.installed_orca == [new_name]
    assert res.kept_orca == [N]
    assert (orca.user_filament / f"{new_name}.json").is_file()
    assert (dirs.installed / "base" / "Keep.json").is_file()
    assert (dirs.installed / "orca" / f"{new_name}.json").is_file()


def test_orca_only_sync_end_to_end(tmp_path):
    orca = orca_fixture.make(
        tmp_path / "appdata",
        presets={N: (OPRESET, OINFO)},
        install=tmp_path / "pf" / "OrcaSlicer 2.5.0",
        opc=orca_fixture.opc_blob(orca_fixture.LIBRARY),
    )
    dirs = store.AppDirs(tmp_path / "local" / "filament-kit-sync")
    dirs.ensure()

    olive = treehash.tree_hash(orca.user_filament)
    osys = sync.zips.orca_system(orca)[1]

    # 1. No build yet -> uploads orca_live & orca_system, requests rebuild with live_hash=None
    state1 = {
        "owner": "dev1",
        "you": "dev1",
        "live_hash": None,
        "system_hash": None,
        "orca_live_hash": None,
        "orca_system_hash": None,
        "orca_target": True,
        "build": None,
        "installed": None,
    }
    c1 = OrcaClient(state1)
    assert sync.run_once(c1, None, dirs, running=lambda: True, now=T0, orca=orca, orca_running=lambda: False) == "uploaded"
    assert [x[1] for x in c1.calls if x[0] == "upload"] == ["orca_live", "orca_system"]
    assert c1.rebuild_orca == [olive]

    # 2. Build ready with matching orca_live_hash -> installs Orca tree, reports trees=["orca"]
    new_name = "Acme PETG @SV08 MAX 0.4"
    new_preset = dict(OPRESET, name=new_name, nozzle_temperature=["240"])
    z = _zip(
        stamp=STAMP,
        live_hash=None,
        orca_live_hash=olive,
        base={},
        orca={N: dict(OPRESET, nozzle_temperature=["225"]), new_name: new_preset},
    )
    state2 = {
        "owner": "dev1",
        "you": "dev1",
        "orca_live_hash": olive,
        "orca_system_hash": osys,
        "orca_target": True,
        "build": {"stamp": STAMP, "orca_live_hash": olive},
        "installed": None,
    }
    c2 = OrcaClient(state2, build_zip=z)
    assert sync.run_once(c2, None, dirs, running=lambda: True, now=T0 + 60, orca=orca, orca_running=lambda: False) == "installed"
    assert ("installed", STAMP) in c2.calls
    assert c2.trees == [["orca"]]
    assert (orca.user_filament / f"{new_name}.json").is_file()

    # 3. Edit the newly installed Orca preset -> changed_since_install triggers rebuild_requested
    edited = dict(new_preset, nozzle_temperature=["255"])
    (orca.user_filament / f"{new_name}.json").write_text(json.dumps(edited), encoding="utf-8")
    new_olive = treehash.tree_hash(orca.user_filament)
    state3 = {
        "owner": "dev1",
        "you": "dev1",
        "orca_live_hash": new_olive,
        "orca_system_hash": osys,
        "orca_target": True,
        "build": {"stamp": STAMP, "orca_live_hash": olive},
        "installed": {"stamp": STAMP},
    }
    c3 = OrcaClient(state3)
    assert sync.run_once(c3, None, dirs, running=lambda: False, now=T0 + 120, orca=orca, orca_running=lambda: False) == "rebuild_requested"
    assert c3.rebuild_orca == [new_olive]


def test_cli_orca_only_run_and_status(tmp_path, monkeypatch, capsys):
    appdata = tmp_path / "appdata"
    local = tmp_path / "local"
    dirs = store.AppDirs(local / "filament-kit-sync")
    dirs.ensure()
    store.save_config(dirs, {"host": "hub.local", "port": 8710, "fp": "0" * 64, "device": "dev1"})
    store.save_token(dirs, "tok-123")

    # No Bambu Studio, no Orca Slicer -> fails with no_studio
    monkeypatch.setattr(cli, "_program_files", lambda: [])
    monkeypatch.setattr(studio, "_registry_entries", lambda: [])
    monkeypatch.setattr(sync, "report_failure", lambda *a, **k: None)
    assert cli.cmd_run(dirs, appdata, quiet=False) == 1
    assert (sync.load_state(dirs).get("last_error") or {}).get("class") == "no_studio"

    # Now create Orca Slicer only (still no Bambu Studio)
    orca_fixture.make(
        appdata,
        presets={N: (OPRESET, OINFO)},
        install=tmp_path / "pf" / "OrcaSlicer 2.5.0",
        opc=orca_fixture.opc_blob(orca_fixture.LIBRARY),
    )

    seen = {}

    def fake_run_once(c, folders, d, **kw):
        seen["folders"] = folders
        seen["orca"] = kw.get("orca")
        seen["others"] = kw.get("others")
        seen["secondaries"] = kw.get("secondaries")
        return "up_to_date"

    monkeypatch.setattr(sync, "run_once", fake_run_once)
    assert cli.cmd_run(dirs, appdata, quiet=False) == 0
    assert seen["folders"] is None
    assert seen["orca"] is not None
    assert seen["others"] == []
    assert seen["secondaries"] is False

    capsys.readouterr()
    assert cli.cmd_status(dirs, appdata) == 0
    out = capsys.readouterr().out
    assert "syncing Orca Slicer only" in out
    assert "Orca Slicer:" in out
