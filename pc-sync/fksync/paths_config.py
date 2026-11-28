"""Slicer path configuration, auto-detection, and entry resolution for fksync."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from . import store

VALID_KINDS = ("bambu", "orca", "orca_install")

_DEFAULT_LABELS = {
    "bambu": "Bambu Studio",
    "orca": "Orca Slicer",
    "orca_install": "Orca Slicer Install",
}


@dataclass
class SlicerEntry:
    kind: str
    label: str
    path: str
    enabled: bool = True
    custom: bool = False

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "label": self.label,
            "path": self.path,
            "enabled": bool(self.enabled),
            "custom": bool(self.custom),
        }

    @classmethod
    def from_dict(cls, data: Mapping) -> "SlicerEntry":
        if not isinstance(data, Mapping):
            raise ValueError("slicer entry must be a mapping")
        kind = data.get("kind")
        if kind not in VALID_KINDS:
            raise ValueError(f"invalid slicer kind {kind!r}; expected one of {', '.join(VALID_KINDS)}")
        raw_path = data.get("path")
        path = str(raw_path).strip() if raw_path is not None else ""
        if not path:
            raise ValueError("slicer entry path must not be empty")
        raw_label = data.get("label")
        label = str(raw_label).strip() if raw_label is not None else ""
        if not label:
            label = Path(path).name or _DEFAULT_LABELS[kind]
        enabled = bool(data.get("enabled", True))
        custom = bool(data.get("custom", False))
        return cls(kind=kind, label=label, path=path, enabled=enabled, custom=custom)


def normalize_path(p: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(p).strip()))


def default_program_files() -> tuple[Path, ...]:
    candidates: list[Path] = []
    for var, fallback in (("ProgramFiles", "C:/Program Files"), ("ProgramFiles(x86)", "C:/Program Files (x86)")):
        val = os.environ.get(var)
        if val:
            candidates.append(Path(val))
        elif sys.platform == "win32":
            candidates.append(Path(fallback))
    out: list[Path] = []
    seen: set[str] = set()
    for p in candidates:
        norm = normalize_path(p)
        if norm not in seen and p.is_dir():
            seen.add(norm)
            out.append(p)
    return tuple(out)


def detect_slicer_paths(
    appdata: Path | None,
    *,
    layout=None,
    program_files: Iterable[Path] | None = None,
    registry=None,
) -> list[SlicerEntry]:
    from . import studio

    detected: list[SlicerEntry] = []

    bambu_roots = (
        layout.studio_roots()
        if layout is not None
        else ([(v, Path(appdata) / v) for v in studio.VARIANTS] if appdata is not None else [])
    )
    for variant, root in bambu_roots:
        if root.is_dir() or studio._user_filament_dirs(root):
            detected.append(SlicerEntry(kind="bambu", label=variant, path=str(root), enabled=True, custom=False))

    orca_roots = (
        layout.orca_roots()
        if layout is not None
        else ([(studio.ORCA, Path(appdata) / studio.ORCA, None)] if appdata is not None else [])
    )
    for name, root, _fid in orca_roots:
        if root.is_dir():
            detected.append(SlicerEntry(kind="orca", label=name, path=str(root), enabled=True, custom=False))

    if layout is None:
        pf = default_program_files() if program_files is None else program_files
        reg = studio._registry_entries if registry is None else registry
        install = studio._orca_install(None, pf, reg)
        if install is not None:
            detected.append(
                SlicerEntry(kind="orca_install", label="Orca Slicer Install", path=str(install), enabled=True, custom=False)
            )
    else:
        for cand in layout.orca_installs(None):
            if (cand / "profiles").is_dir():
                detected.append(
                    SlicerEntry(kind="orca_install", label="Orca Slicer Install", path=str(cand), enabled=True, custom=False)
                )
                break

    out: list[SlicerEntry] = []
    seen: set[str] = set()
    for entry in detected:
        norm = normalize_path(entry.path)
        if norm not in seen:
            seen.add(norm)
            out.append(entry)
    return out


def merge_detected(existing: list[SlicerEntry], detected: list[SlicerEntry]) -> list[SlicerEntry]:
    out = list(existing)
    seen = {normalize_path(e.path) for e in existing}
    for d in detected:
        norm = normalize_path(d.path)
        if norm not in seen:
            seen.add(norm)
            out.append(d)
    return out


def load_slicer_paths(dirs: store.AppDirs) -> list[SlicerEntry]:
    try:
        cfg = store.load_config(dirs)
    except ValueError:
        return []
    raw = cfg.get("slicer_paths")
    if not isinstance(raw, list):
        return []
    entries: list[SlicerEntry] = []
    for item in raw:
        try:
            entries.append(SlicerEntry.from_dict(item))
        except (ValueError, TypeError):
            continue
    return entries


def save_slicer_paths(dirs: store.AppDirs, entries: list[SlicerEntry]) -> dict:
    with store.single_instance(dirs):
        try:
            cfg = store.load_config(dirs)
        except ValueError:
            cfg = {}
        cfg["slicer_paths"] = [e.to_dict() for e in entries]
        enabled_install = next((e for e in entries if e.kind == "orca_install" and e.enabled), None)
        if enabled_install is not None:
            cfg["orca_install"] = enabled_install.path
        elif any(e.kind == "orca_install" for e in entries):
            cfg.pop("orca_install", None)
        store.save_config(dirs, cfg)
        return cfg


def resolve_bambu_entry(entry: SlicerEntry, preferred_user_id: str | None = None):
    from . import studio

    p = Path(entry.path)
    if not p.is_dir():
        return None
    dirs = studio._user_filament_dirs(p)
    if dirs:
        chosen = [d for d in dirs if d[1] == preferred_user_id] if preferred_user_id else []
        _, uid, uf = chosen[0] if chosen else max(dirs)
        return studio.StudioFolders(
            root=p,
            variant=entry.label or p.name,
            user_id=uid,
            user_filament=uf,
            system_filament=p / "system" / "BBL" / "filament",
        )
    if p.name == "filament" and p.parent.parent.name == "user" and studio._USER_ID.match(p.parent.name):
        uid = p.parent.name
        root = p.parent.parent.parent
        return studio.StudioFolders(
            root=root,
            variant=entry.label or root.name,
            user_id=uid,
            user_filament=p,
            system_filament=root / "system" / "BBL" / "filament",
        )
    if entry.custom and p.is_dir():
        return studio.StudioFolders(
            root=p,
            variant=entry.label or p.name,
            user_id="custom",
            user_filament=p,
            system_filament=p / "system" / "BBL" / "filament",
        )
    return None


def resolve_orca_entry(
    entry: SlicerEntry,
    install_path: Path | None = None,
    *,
    layout=None,
    program_files: Iterable[Path] = (),
    registry=None,
):
    from . import studio

    p = Path(entry.path)
    if not p.is_dir():
        return None
    dirs = studio._user_filament_dirs(p)
    if dirs:
        folder = studio._conf_preset_folder(p)
        chosen = [d for d in dirs if d[1] == folder]
        _, user_id, user_filament = chosen[0] if chosen else max(dirs)
        root = p
    elif p.name == "filament" and p.parent.parent.name == "user" and studio._USER_ID.match(p.parent.name):
        user_id = p.parent.name
        root = p.parent.parent.parent
        user_filament = p
    elif entry.custom and p.is_dir():
        root = p
        user_id = "custom"
        user_filament = p
    else:
        return None

    install: Path | None = None
    packed: Path | None = None
    if install_path is not None:
        if layout is None:
            if studio._is_install(install_path) or install_path.is_dir():
                install = install_path
                for cand in (
                    install / "resources" / "profiles" / studio.LIBRARY_OPC,
                    install / "profiles" / studio.LIBRARY_OPC,
                ):
                    if cand.is_file():
                        packed = cand
                        break
        else:
            for cand in (install_path, install_path / "resources", install_path / "Contents" / "Resources"):
                if (cand / "profiles").is_dir():
                    install = cand
                    packed = cand / "profiles" / studio.LIBRARY_OPC
                    break
    else:
        if layout is None:
            reg = studio._registry_entries if registry is None else registry
            install = studio._orca_install(None, program_files, reg)
            packed = (install / "resources" / "profiles" / studio.LIBRARY_OPC) if install else None
        else:
            flatpak_id = next(
                (fid for _n, r, fid in layout.orca_roots() if normalize_path(r) == normalize_path(root)),
                None,
            )
            install = studio._orca_install_linux(None, layout, flatpak_id)
            packed = (install / "profiles" / studio.LIBRARY_OPC) if install else None

    opc = None
    for cand in (packed, root / "system" / studio.LIBRARY_OPC):
        if cand is not None and cand.is_file():
            opc = cand
            break

    return studio.OrcaFolders(
        root=root,
        user_id=user_id,
        user_filament=user_filament,
        system_bbl=root / "system" / "BBL" / "filament",
        install=install,
        library_opc=opc,
    )
