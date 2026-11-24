"""An upgrade from the last release named filament-kit (v0.6.8) shows the new name wherever a person reads it:
the Description= of every unit (`systemctl status`, the journal) and the installer's once-only line. Unit FILE names, the
program and the paths stay as they were, so the upgrade keeps working."""
import re
import subprocess
from pathlib import Path

import pytest

from filament_kit import apply
from filament_kit.apply import Action

ROOT = Path(__file__).resolve().parents[2]
UNITS = ROOT / "install" / "systemd"

# The Description= lines of install/systemd at v0.6.8: what an upgraded host has before the new units are installed.
V068 = {
    "filament-kit-backup.service": "filament-kit daily backup (Spoolman database, settings, state)",
    "filament-kit-backup.timer": "Run the filament-kit backup daily",
    "filament-kit-build.path": "Rebuild filament-kit presets when a rebuild is requested",
    "filament-kit-build.service": "filament-kit Bambu Studio preset build (started by filament-kit-build.path)",
    "filament-kit-library.service": "filament-kit filament library update (skips unless config library.schedule says it is due)",
    "filament-kit-library.timer": "Check for filament library updates daily",
    "filament-kit-setup-job@.service": "filament-kit background job %i, started from the setup page",
    "filament-kit-setup.socket": "filament-kit setup page helper socket (the root writes of https://<host>.local:8710/setup)",
    "filament-kit-setup@.service": "filament-kit setup page helper (one request)",
    "filament-kit-station-pair.path": "filament-kit station: a pairing the station received, waiting to be saved as root",
    "filament-kit-station-pair.service": "filament-kit station: save a pairing the station received (reverse pairing)",
    "filament-kit-station.service": "filament-kit station: USB scale and NFC reader, read-only HTTP for the kit server",
    "filament-kit-update.service": "filament-kit auto-update (module updates): install the latest signed release when one is due",
    "filament-kit-update.timer": "Check for a new filament-kit release daily (module updates)",
    "filament-kit-web.service": "filament-kit web UI and MCP endpoint",
    "filament-kit.service": "filament-kit server (phone pairing, label reading, Spoolman writes)",
    "spoolman.service": "Spoolman filament inventory (v0.27.0)",
}
# `filament-kit` used as the product: not a unit name (filament-kit-build.path, filament-kit.service) or a path.
PRODUCT = re.compile(r"(?<![\w/.\-`])filament-kit(?![\w\-]|\.[a-z])")
OLD_WORDS = re.compile(r"\b(?:kit server|setup page|module updates|web UI)\b")


def _description(path: Path) -> str:
    return next(ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.startswith("Description="))[len("Description="):]


def _upgraded_host(tmp_path):
    """A host as v0.6.8 left it, then what the new install does to its units: `modules apply` copies each unit over."""
    root = tmp_path / "root"
    etc = root / "etc" / "systemd" / "system"
    etc.mkdir(parents=True)
    for name, desc in V068.items():
        (etc / name).write_text(f"[Unit]\nDescription={desc}\n", encoding="utf-8")
    apply.execute([Action("install_unit", n) for n in V068], run=lambda cmd: None, resources=ROOT / "install", root=root)
    return etc


def test_no_unit_is_renamed_or_dropped_so_the_upgrade_keeps_working():
    assert sorted(p.name for p in UNITS.iterdir()) == sorted(V068)


def test_the_v068_list_is_what_it_was():
    """The old lines above are the ones the upgrade replaces: a unit that never had the old name is not in the way."""
    assert sum(1 for d in V068.values() if PRODUCT.search(d)) == 15


def test_an_upgraded_host_shows_spoolwarden_in_every_unit_that_named_the_old_product(tmp_path):
    etc = _upgraded_host(tmp_path)
    for name, old in V068.items():
        new = _description(etc / name)
        assert not PRODUCT.search(new), f"{name}: {new}"
        assert not OLD_WORDS.search(new), f"{name}: {new}"
        if PRODUCT.search(old):
            assert "Spoolwarden" in new, f"{name}: {new}"
            assert new != old
        else:
            assert new == old, name


def test_the_installed_unit_is_the_shipped_unit(tmp_path):
    etc = _upgraded_host(tmp_path)
    for name in V068:
        assert (etc / name).read_bytes() == (UNITS / name).read_bytes(), name


# ---------------------------------------------------------------- the installer's once-only line

def _bash():
    from fakes.shells import git_bash
    return git_bash()


def _install_functions():
    """The two small functions of install.sh that decide and say the once-only line, as shell text."""
    text = (ROOT / "install" / "install.sh").read_text(encoding="utf-8")
    return "\n".join(re.findall(r"^(?:from_old_name|renamed_note)\(\) \{.*\}$", text, re.M))


def _says_it(tmp_path, *present):
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    for n in present:
        (bin_ / n).write_text("#!/bin/sh\n", encoding="utf-8")
    script = _install_functions() + '\nif from_old_name bin; then renamed_note; fi\n'
    r = subprocess.run([_bash(), "-c", script], cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return r.stdout


@pytest.mark.skipif(not _bash(), reason="needs bash")
def test_the_installer_says_the_new_name_once_after_an_upgrade_from_the_old_name(tmp_path):
    out = _says_it(tmp_path, "filament-kit")                  # a release before 0.6.1: the old command only
    assert out.strip() == ("filament-kit is now called Spoolwarden. The command is spoolwarden; "
                           "filament-kit still works for now.")


@pytest.mark.skipif(not _bash(), reason="needs bash")
@pytest.mark.parametrize("present", [(), ("spoolwarden",), ("filament-kit", "spoolwarden")])
def test_the_installer_says_nothing_on_a_new_install_or_when_the_new_command_is_already_there(tmp_path, present):
    assert _says_it(tmp_path, *present) == ""                 # a fresh host; or 0.6.1 and later, told once already


def test_the_line_is_printed_once_at_the_end_and_decided_before_the_new_command_is_installed():
    text = (ROOT / "install" / "install.sh").read_text(encoding="utf-8")
    assert text.count("renamed_note\n") + text.count("renamed_note;") == 2  # restore early-exit + normal end path
    assert text.index("if from_old_name /usr/local/bin") < text.index('install -m 755 "$RES/spoolwarden-cli.sh"')
    assert text.index('if [ "$OLD_NAME" -eq 1 ]; then renamed_note; fi') < text.index("exec /usr/local/bin/spoolwarden modules apply")


def test_the_installer_and_backup_messages_use_the_ruled_names():
    """What a person reads at the terminal or on the page: the Hub, the Hub page, features (E1, second part)."""
    for rel in ("install/install.sh", "server/filament_kit/backup.py"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.lstrip().startswith("#") or not re.search(r'\b(?:echo|die|out|BackupError|raise)\b', line):
                continue
            assert not re.search(r"kit server|setup page|single modules|module list|no modules chosen|choose each module|unknown module %s|a kit\b|the kit\b|"
                                 r"filament-kit \{|Start the kit", line), f"{rel}: {line.strip()}"
