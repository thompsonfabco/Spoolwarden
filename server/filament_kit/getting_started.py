"""Getting started (R82 section 4): which steps there are, which are done, and the object `GET start` answers.

What cannot be computed is kept in firstrun.py (hidden, finished, and the name, storage and features questions that were
answered). Everything else is read where it lives at the moment the view is asked: the features, the reader's install
state, the key, printers and their slot maps, devices, the passphrase, backups and Spoolman's spools. A step whose state
cannot be read counts as not done; this module never raises.

`view()` is what `spoolwarden status` prints (its block and `--json` key `getting_started`) and what the Hub page's
route `GET start` serves (step AF6 calls `view()`; the route adds nothing but the HTTP).

    {show, hidden, finished, restore_offer, done, total, steps: [{id, state: done|next|open, line, target}]}

`show` is true while the panel is drawn: the first-run file says shown and not finished (a file that is missing or
unreadable reads as hidden, firstrun.load). The Restore row is not a step: `restore_offer` is true while the panel
shows and no step is done. `target` is where the step's button goes on the Hub page: `#settings/general` and the like
(a `web:` target is the phone web app).
"""
from __future__ import annotations

import logging

from . import config, firstrun, modules
from .address_texts import say

log = logging.getLogger("filament_kit.getting_started")

STEP_IDS = ("name", "storage", "features", "reader", "key", "printer", "slots", "phone", "passphrase", "pc", "spool",
            "backup")

TARGETS = {"name": "#settings/general", "storage": "#settings/storage", "features": "#features",
           "reader": "#settings/label-reader", "key": "#settings/label-reader", "printer": "#printers/add",
           "slots": "#printers//map", "phone": "#devices/pair", "passphrase": "#settings/passphrase", "pc": "#devices/pair",
           "spool": "web:add", "backup": "#settings/backups"}

# The command that does each step (06 9.1), under the line of the Next step in `status`: `{command}` is the program.
COMMANDS = {"name": "sudo {command} config", "storage": "sudo {command} locations", "features": "sudo {command} features",
            "reader": "sudo {command} local-intake install", "key": "sudo {command} config",
            "printer": "sudo {command} printer add", "slots": 'sudo {command} printer map "{printer}"',
            "phone": "sudo {command} pair", "passphrase": "sudo {command} web passphrase",
            "pc": "sudo {command} pair --slicer-sync", "spool": "{command} add", "backup": "sudo {command} backup create"}


def _loopback(url: str) -> bool:
    from urllib.parse import urlparse
    return (urlparse(url).hostname or "") in ("127.0.0.1", "localhost", "::1")


def reader_install_pending(cfg: dict) -> bool:
    """Label Reader: On this Hub is on, its address is this machine's, and the reader is not installed: the case of the
    Checks row "The Label Reader is switched on but not installed." and of the Next step "Install the Label Reader". A
    reader at another machine's address has nothing to install here."""
    from . import local_intake_install, local_reader
    try:
        if "local_intake" not in modules.enabled(cfg) or not _loopback(local_reader.settings(cfg)["url"]):
            return False
        return local_intake_install.installed() is None
    except Exception as e:      # noqa: BLE001 - a check that cannot be made is no row
        log.warning("reader install state not read: %s", e)
        return False


def _printers() -> list:
    from .bridge import registry
    return [p for p in registry.load().values() if p.configured]


def _slot_map_saved(p) -> bool:
    from .bridge import ams
    if p.family == "klipper":       # a Klipper printer keeps no map file: what `printer detect` found is its model
        return bool(p.model)
    return bool(ams.load_map(p.serial).slots)


def _phones() -> int:
    """Paired devices that are phones: not a Weigh Station, not the Hub's own pairings, not the Slicer Sync PC."""
    from . import pairing
    from .profiles import builds
    from .web.slots import CLI_DEVICE, DEVICE_NAME, INTAKE_DEVICE
    owner = builds.owner()
    return sum(1 for d in pairing.devices()
               if not d.get("type") and d["name"] not in (INTAKE_DEVICE, CLI_DEVICE, DEVICE_NAME) and d["id"] != owner)


def _has_spools(cfg: dict) -> bool | None:
    from .spoolman import Spoolman, SpoolmanError
    try:
        return bool(Spoolman(cfg["spoolman_url"], timeout=3).get("/api/v1/spool?limit=1"))
    except (SpoolmanError, OSError, ValueError, KeyError):
        return None


def _step(sid: str, cfg: dict, fr: firstrun.FirstRun) -> tuple[bool, str] | None:
    """(done, the line) for step `sid`, or None when the step is not shown on this Hub."""
    from . import backup, storage
    on = modules.enabled(cfg)
    if sid == "name":
        return "name" in fr.answered, say("start_line_name", name=cfg.get("server_name") or "Hub")
    if sid == "storage":
        n = len(storage.current().locations)
        return "storage" in fr.answered, say("start_line_storage", count=n)
    if sid == "features":
        return "features" in fr.answered, say("start_line_features", count=len(on))
    if sid == "reader":
        if "local_intake" not in on:
            return None
        from . import local_intake_install, local_reader
        url = local_reader.settings(cfg)["url"]
        if not _loopback(url):
            return True, say("start_line_reader_remote", address=url.split("//", 1)[-1])
        have = local_intake_install.installed()
        return (True, say("start_line_reader_done", version=have["version"])) if have else \
            (False, say("start_line_reader_open"))
    if sid == "key":
        if "ai_intake" not in on:
            return None
        from . import cli
        try:
            has = (config.paths().etc / cli.KEY_FILE).stat().st_size > 0
        except OSError:
            has = False
        return has, say("start_line_key_done" if has else "start_line_key_open")
    if sid in ("printer", "slots"):
        if not on & {"printers", "klipper"}:
            return None
        printers = _printers()
        if sid == "printer":
            return (True, say("start_line_printer_done", count=len(printers))) if printers else \
                (False, say("start_line_printer_open"))
        if not printers:
            return False, say("start_line_slots_no_printer")
        mapped = any(_slot_map_saved(p) for p in printers)
        return mapped, say("start_line_slots_done" if mapped else "start_line_slots_open")
    if sid == "phone":
        from . import cli
        if _phones():
            return True, say("start_line_phone_done")
        if (config.paths().etc / cli.WEB_PASSPHRASE).is_file():
            return True, say("start_line_phone_passphrase")
        return False, say("start_line_phone_open")
    if sid == "passphrase":
        if "web" not in on:
            return None
        from . import cli
        has = (config.paths().etc / cli.WEB_PASSPHRASE).is_file()
        return has, say("start_line_passphrase_done" if has else "start_line_passphrase_open")
    if sid == "pc":
        if "presets" not in on:
            return None
        from .profiles import builds
        has = builds.owner() is not None
        return has, say("start_line_pc_done" if has else "start_line_pc_open")
    if sid == "spool":
        has = _has_spools(cfg)
        if has is None:
            return False, say("start_line_spool_unknown")
        return has, say("start_line_spool_done" if has else "start_line_spool_open")
    if sid == "backup":
        has = any(e.kind != "pre-restore" for e in backup.listing())
        return has, say("start_line_backup_done" if has else "start_line_backup_open")
    raise ValueError(sid)


def view(cfg: dict | None = None) -> dict:
    """The object of `GET start`. Never raises."""
    cfg = cfg if cfg is not None else config.load()
    fr = firstrun.load()
    steps = []
    for sid in STEP_IDS:
        try:
            got = _step(sid, cfg, fr)
        except Exception as e:      # noqa: BLE001 - a step that cannot be read is open, the rest still shows
            log.warning("getting started step %s not read: %s", sid, e)
            got = (False, "")
        if got is not None:
            steps.append({"id": sid, "state": "done" if got[0] else "open", "line": got[1], "target": TARGETS[sid]})
    nxt = next((s for s in steps if s["state"] == "open"), None)
    if nxt:
        nxt["state"] = "next"
    done = sum(1 for s in steps if s["state"] == "done")
    return {"show": fr.shows, "hidden": fr.hidden, "finished": fr.finished, "restore_offer": fr.shows and done == 0,
            "done": done, "total": len(steps), "steps": steps}
