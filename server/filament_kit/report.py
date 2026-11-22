"""Redacted plain-text Hub report (HD6, 03 13.5, 06 9.26).

Collects versions, enabled features, Checks rows with fix lines, printers (kind and model), devices by kind and
version, storage use, the last update log, the last 200 events, non-private settings, and the last 500 lines of each
service's log. Addresses, host names, serials, tokens, keys, codes, e-mail addresses and user folder names are replaced
with placeholders (`redact.py`), and every printer, device, location and Hub name becomes `printer-1`, `device-2`,
`location-3` and `hub`. The newest three reports are kept beside the backups.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import os
from pathlib import Path
import platform
import re
import shutil
import socket
import subprocess
import time

from . import __version__, config, redact

KEEP_REPORTS = 3
PREFIX = "hub-report-"
SUFFIX = ".txt"
EVENT_LIMIT = 200
LOG_LINES = 500
_REPORT_NAME = re.compile(r"^hub-report-\d{4}-\d{2}-\d{2}-\d{4}(?:-\d+)?\.txt$")
_PRIVATE_KEY = re.compile(
    r"(?:password|passwd|secret|token|key|code|passphrase|auth|url|host|ip|allow|serial|email|user_id|uid)",
    re.I,
)


def reports_dir(dest: Path | str | None = None) -> Path:
    """Folder where Hub reports live (beside the backups)."""
    return Path(dest) if dest is not None else config.paths().backups


def listing(dest: Path | str | None = None) -> list[Path]:
    """Existing Hub report files in `dest`, newest first."""
    folder = reports_dir(dest)
    if not folder.is_dir():
        return []
    found: list[tuple[int, str, Path]] = []
    for p in folder.iterdir():
        if p.is_symlink() or not p.is_file() or not _REPORT_NAME.match(p.name):
            continue
        try:
            mtime = p.stat().st_mtime_ns
        except OSError:
            continue
        found.append((mtime, p.name, p))
    found.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in found]


def latest(dest: Path | str | None = None) -> Path | None:
    """Newest Hub report in `dest`, or None when none has been collected yet."""
    rows = listing(dest)
    return rows[0] if rows else None


def prune(dest: Path | str | None = None, keep: int = KEEP_REPORTS) -> list[Path]:
    """Keep only the newest `keep` Hub reports in `dest`; return the paths removed."""
    removed: list[Path] = []
    for old in listing(dest)[max(0, keep):]:
        try:
            old.unlink()
            removed.append(old)
        except OSError:
            pass
    return removed


def _name_map(cfg: dict) -> list[tuple[str, str]]:
    """Longest-first `(raw_name, placeholder)` pairs for Hub, printers, devices and locations."""
    mapping: dict[str, str] = {}

    def add(raw: object, placeholder: str) -> None:
        if isinstance(raw, str):
            s = raw.strip()
            if len(s) >= 2 and s not in mapping:
                mapping[s] = placeholder

    add(cfg.get("server_name"), "hub")
    try:
        add(socket.gethostname(), "hub")
    except OSError:
        pass
    add(platform.node(), "hub")

    try:
        from .bridge import registry
        for idx, p in enumerate(registry.load().values(), start=1):
            tag = f"printer-{idx}"
            add(getattr(p, "name", None), tag)
            add(getattr(p, "id", None), tag)
    except Exception:  # noqa: BLE001
        pass

    try:
        from . import pairing
        for idx, d in enumerate(pairing.devices(), start=1):
            tag = f"device-{idx}"
            add(d.get("name"), tag)
            add(d.get("device_hint"), tag)
    except Exception:  # noqa: BLE001
        pass

    try:
        from . import storage
        for idx, loc in enumerate(storage.current().locations, start=1):
            add(loc.name, f"location-{idx}")
    except Exception:  # noqa: BLE001
        pass

    return sorted(mapping.items(), key=lambda item: len(item[0]), reverse=True)


def _scrub(text: str, known: list[str], names: list[tuple[str, str]]) -> str:
    """Replace known names with placeholders and apply `redact.text`."""
    out = text
    for raw, placeholder in names:
        pattern = re.compile(rf"(?<![\w-]){re.escape(raw)}(?![\w-])")
        out = pattern.sub(placeholder, out)
    return redact.text(out, known)


def _versions_section(cfg: dict) -> list[str]:
    lines = [
        f"Spoolwarden: {__version__}",
        f"Python: {platform.python_version()}",
        f"System: {platform.platform()}",
    ]
    try:
        from . import updates
        vtext = updates.version_text()
        if vtext:
            lines.append(f"Version summary: {vtext}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from .spoolman import Spoolman
        url = cfg.get("spoolman_url")
        if isinstance(url, str) and url.strip():
            info = Spoolman(url).info() if hasattr(Spoolman, "info") else None
            if isinstance(info, dict) and info.get("version"):
                lines.append(f"Spoolman: {info['version']}")
    except Exception:  # noqa: BLE001
        pass
    return lines


def _features_section(cfg: dict) -> list[str]:
    mods = cfg.get("modules")
    if isinstance(mods, dict):
        enabled = [k for k, v in sorted(mods.items()) if v]
        return enabled if enabled else ["(none)"]
    try:
        from . import modules
        enabled = [m for m in modules.ALL if modules.is_on(cfg, m)]
        return enabled if enabled else ["(none)"]
    except Exception:  # noqa: BLE001
        return ["(unavailable)"]


def _checks_section(cfg: dict) -> list[str]:
    try:
        from . import status
        rows = status.collect(cfg)
    except Exception:  # noqa: BLE001
        return ["(unavailable)"]
    if not rows:
        return ["(none)"]
    lines: list[str] = []
    for r in rows:
        state = getattr(r, "state", "ok")
        group = getattr(r, "group", "")
        key = getattr(r, "key", "")
        text = getattr(r, "text", "")
        fix = getattr(r, "fix", "")
        line = f"[{state}] {group}/{key}: {text}"
        if fix:
            line += f" | Fix: {fix}"
        lines.append(line)
    return lines


def _printers_section() -> list[str]:
    try:
        from .bridge import registry
        printers = list(registry.load().values())
    except Exception:  # noqa: BLE001
        return ["(unavailable)"]
    if not printers:
        return ["(none)"]
    lines: list[str] = []
    for idx, p in enumerate(printers, start=1):
        kind = getattr(p, "kind", None) or getattr(p, "family", None) or "unknown"
        model = getattr(p, "model", None) or "unknown"
        lines.append(f"printer-{idx}: kind={kind} model={model}")
    return lines


def _devices_section() -> list[str]:
    try:
        from . import pairing
        devs = pairing.devices()
    except Exception:  # noqa: BLE001
        return ["(unavailable)"]
    if not devs:
        return ["(none)"]
    counts: Counter[str] = Counter()
    versions: dict[str, set[str]] = {}
    for d in devs:
        kind = pairing.device_type(d) or "unknown"
        counts[kind] += 1
        ver = d.get("version") or d.get("app_version")
        if ver:
            versions.setdefault(kind, set()).add(str(ver))
    lines: list[str] = []
    for kind in sorted(counts):
        vers = ", ".join(sorted(versions.get(kind, ()))) or "unknown"
        lines.append(f"{kind}: {counts[kind]} (versions: {vers})")
    return lines


def _storage_section() -> list[str]:
    try:
        from . import alerts_api
        view = alerts_api.storage_view()
        fs_rows = view.get("filesystems") or []
        if fs_rows:
            return [
                f"{fs.get('label') or fs.get('mount') or 'fs'}: {fs.get('used_percent', '?')}% used, "
                f"{fs.get('free_bytes', '?')} B free"
                for fs in fs_rows
            ]
    except Exception:  # noqa: BLE001
        pass
    try:
        usage = shutil.disk_usage(config.paths().state)
        pct = round(usage.used * 100 / usage.total, 1) if usage.total else 0.0
        return [f"state: {pct}% used, {usage.free} B free"]
    except Exception:  # noqa: BLE001
        return ["(unavailable)"]


def _last_update_section() -> list[str]:
    lines: list[str] = []
    try:
        from . import updates
        summary = updates.summary()
        if isinstance(summary, dict):
            for k in ("installed", "latest", "status", "checked_at", "updated_at", "error"):
                if summary.get(k) is not None:
                    lines.append(f"{k}: {summary[k]}")
    except Exception:  # noqa: BLE001
        pass
    try:
        jobs_dir = config.paths().state / "jobs"
        if jobs_dir.is_dir():
            logs = sorted(jobs_dir.glob("*update*.log"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
            if logs:
                tail = logs[0].read_text("utf-8", errors="replace").splitlines()[-50:]
                lines.extend(tail)
    except Exception:  # noqa: BLE001
        pass
    return lines or ["(none)"]


def _events_section() -> list[str]:
    try:
        from . import events
        res = events.read(newest=EVENT_LIMIT)            # the newest ones, oldest of them first
        items = res.get("events") or []
    except Exception:  # noqa: BLE001
        return ["(unavailable)"]
    if not items:
        return ["(none)"]
    lines: list[str] = []
    for ev in items:
        at = ev.get("at") or ""
        kind = ev.get("kind") or ""
        subj = ev.get("subject")
        if isinstance(subj, dict):
            subj_str = f"{subj.get('type', '')}:{subj.get('id', '')}".strip(":")
        else:
            subj_str = str(subj or "")
        lines.append(f"{at}  {kind}" + (f"  [{subj_str}]" if subj_str else ""))
    return lines


def _settings_section(cfg: dict) -> list[str]:
    lines: list[str] = []

    def walk(prefix: str, obj: object) -> None:
        if isinstance(obj, dict):
            for k in sorted(obj):
                full = f"{prefix}.{k}" if prefix else str(k)
                if _PRIVATE_KEY.search(str(k)):
                    lines.append(f"{full} = {redact.HIDDEN}")
                else:
                    walk(full, obj[k])
        elif isinstance(obj, list):
            lines.append(f"{prefix} = [{len(obj)} items]")
        else:
            lines.append(f"{prefix} = {obj}")

    walk("", cfg)
    return lines or ["(none)"]


def _default_journal(unit: str, n: int) -> list[str]:
    r = subprocess.run(
        ["journalctl", "-u", unit, "-n", str(n), "-o", "short-iso", "--no-pager"],
        capture_output=True, text=True, timeout=10, check=False,
    )
    if r.returncode != 0:
        return [f"(journalctl exited {r.returncode})"]
    return r.stdout.splitlines()


def _service_logs_section(cfg: dict, journal_fn=None) -> list[str]:
    try:
        from . import setup_helper
        units = setup_helper.journal_units(cfg)
    except Exception:  # noqa: BLE001
        return ["(unavailable)"]
    if not units:
        return ["(no services enabled)"]
    reader = journal_fn or _default_journal
    lines: list[str] = []
    for unit in units:
        lines.append(f"--- {unit} (last {LOG_LINES} lines) ---")
        try:
            unit_lines = reader(unit, LOG_LINES)
            lines.extend(unit_lines[-LOG_LINES:] if unit_lines else ["(empty)"])
        except Exception as e:  # noqa: BLE001
            lines.append(f"(unavailable: {type(e).__name__})")
    return lines


def build(cfg: dict | None = None, *, journal_fn=None) -> str:
    """Build and return the redacted plain-text Hub report."""
    if cfg is None:
        try:
            cfg = config.load()
        except Exception:  # noqa: BLE001
            cfg = {}
    known = redact.known_values(cfg)
    names = _name_map(cfg)
    now = datetime.now().astimezone().isoformat(timespec="seconds")

    sections: list[tuple[str, list[str]]] = [
        ("Versions", _versions_section(cfg)),
        ("Features on", _features_section(cfg)),
        ("Checks", _checks_section(cfg)),
        ("Printers", _printers_section()),
        ("Devices", _devices_section()),
        ("Storage use", _storage_section()),
        ("Last update", _last_update_section()),
        ("Last 200 events", _events_section()),
        ("Settings", _settings_section(cfg)),
        ("Service logs", _service_logs_section(cfg, journal_fn=journal_fn)),
    ]

    out = [
        "Spoolwarden Hub report",
        f"Generated: {now}",
        "What this holds: versions, enabled features, Checks rows with fix lines, printers (kind and model), "
        "devices by kind and version, storage use, last update log, last 200 events, non-private settings, "
        "and last 500 lines of each service log.",
        "What this never holds: addresses, host names, serials, tokens, keys, codes, e-mail addresses, or user folder "
        "names (replaced with <hidden>). Printers, devices, and locations are named printer-1, device-2, and "
        "location-3, and the Hub hub.",
        "",
    ]
    for title, body_lines in sections:
        out.append(f"## {title}")
        for line in body_lines:
            out.append(_scrub(str(line), known, names))
        out.append("")

    return "\n".join(out)


def _unique_path(folder: Path, stamp: str) -> Path:
    candidate = folder / f"{PREFIX}{stamp}{SUFFIX}"
    if not candidate.exists():
        return candidate
    idx = 1
    while True:
        candidate = folder / f"{PREFIX}{stamp}-{idx}{SUFFIX}"
        if not candidate.exists():
            return candidate
        idx += 1


def collect(dest: Path | str | None = None, *, out: Path | str | None = None,
            cfg: dict | None = None, journal_fn=None) -> Path:
    """Write a redacted Hub report into `dest` (default backups folder), keep newest 3, and return its path."""
    folder = reports_dir(dest)
    folder.mkdir(parents=True, exist_ok=True)
    text = build(cfg, journal_fn=journal_fn)
    stamp = time.strftime("%Y-%m-%d-%H%M", time.localtime())
    target = _unique_path(folder, stamp)
    tmp = target.with_name(f".{target.name}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0), 0o640)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    prune(folder, KEEP_REPORTS)
    if out is not None:
        out_path = Path(out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(target, out_path)
    return target
