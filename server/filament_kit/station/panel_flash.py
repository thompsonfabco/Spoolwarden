"""Which firmware image the station writes to the ESP32-C3 panel, and the esptool call for it.

Two images ship beside each other (station/panel/firmware/, installed to resources/panel/): the WHOLE image
(bootloader, partition table, boot_app0 and program, written at address 0) and the PROGRAM image (the program alone,
written at 0x10000). The whole image also covers the NVS partition at 0x9000, where a wireless panel keeps its Wi-Fi
set-up, so it is only written to a panel that has nothing to lose or whose flash layout changed: one on firmware 13
or older, an empty one (no readable version), or one older than a firmware that changed the layout (LAYOUT_BREAKS).
A panel on firmware 14 or later whose layout is the bundled one gets the program image only, and keeps its Wi-Fi."""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from typing import NamedTuple

from . import page as _station_page
from . import state as st

log = logging.getLogger("intake-station")

# The contract with the panel firmware (its owner keeps these true; build.sh writes both files).
WHOLE_IMAGE = "intake-panel.bin"            # merged image, address 0 (the file STATION_PANEL_FW names)
PROGRAM_IMAGE = "intake-panel-program.bin"  # firmware.bin alone, beside the whole image
WHOLE_ADDRESS = "0x0"
PROGRAM_ADDRESS = "0x10000"                 # app0 in the default partition table
WIFI_FIRST_VERSION = 14                     # the first firmware that stores Wi-Fi settings (13 and older are USB-only)
LAYOUT_BREAKS: tuple[int, ...] = ()         # firmware versions that CHANGED the flash layout (partition table, offsets,
#                                             NVS place); a panel older than one of them needs the whole image

WIFI_LOST_KEY = "station_panel_wifi_lost"   # the catalogue key; the English below is its fallback ({command} is filled in)
WIFI_LOST_NOTICE = ("The panel was flashed from scratch, so its Wi-Fi set-up is gone. "
                    "Run: sudo {command} weigh-station panel-wifi")


class Flash(NamedTuple):
    ok: bool          # esptool exited 0
    whole: bool       # the whole image was written
    notice: str | None  # the wifi-lost sentence when a from-scratch flash wiped a wireless panel's set-up


def _number(v):
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


def program_only(have, want) -> bool:
    """The panel (version have) can take the program image alone: it is on firmware 14 or later and no firmware
    after it, up to the bundled one (want), changed the flash layout. An empty or unreadable version never can."""
    h, w = _number(have), _number(want)
    if h is None or w is None or h < WIFI_FIRST_VERSION:
        return False
    return not any(h < b <= w for b in LAYOUT_BREAKS)


def program_path(whole: str | None = None) -> str:
    """The program image, beside the whole image the settings name."""
    return os.path.join(os.path.dirname(whole or st.settings["panel_fw"]), PROGRAM_IMAGE)


def choice(have, want) -> tuple[bool, str, str]:
    """(whole, image path, address) for a panel reporting version have when the bundled firmware is want. The
    program image is only chosen when it is really there; without it the whole image is the only way."""
    whole = st.settings["panel_fw"]
    if program_only(have, want) and os.path.isfile(program_path(whole)):
        return False, program_path(whole), PROGRAM_ADDRESS
    return True, whole, WHOLE_ADDRESS


def wifi_lost(whole: bool, link=None) -> str | None:
    """The sentence to say after a from-scratch flash, only while the panel's link is Wi-Fi."""
    link = st.settings.get("panel_link") if link is None else link
    return _station_page.catalogue_text(WIFI_LOST_KEY, WIFI_LOST_NOTICE) if whole and link == "wifi" else None


def flash(dev, have, version, link=None) -> Flash:
    """Write the right image to the C3 panel on dev (version have, empty/None for a blank one) with esptool (its
    native USB resets into the ROM bootloader, so a failed write can always be retried). Never touches the reader's
    port. ok is True only when esptool exits 0."""
    if dev == st.ports["nfc"]:
        log.warning("not flashing %s: it is the NFC reader's port", dev)
        return Flash(False, False, None)
    whole, image, address = choice(have, version)
    log.info("flashing button panel on %s with firmware %s (%s image)", dev, version, "whole" if whole else "program")
    cmd = [sys.executable, "-m", "esptool", "--chip", "esp32c3", "--port", dev, "--baud", "460800",
           "--before", "default-reset", "--after", "hard-reset", "write-flash", address, image]
    ok = False
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=180)
        ok = p.returncode == 0
        tail = (p.stdout + p.stderr).decode(errors="replace").strip().splitlines()[-3:]
        (log.info if ok else log.warning)("esptool exit %d: %s", p.returncode, " | ".join(tail))
    except (OSError, subprocess.TimeoutExpired) as e:
        log.warning("esptool: %s", e)
    time.sleep(2)  # let it boot
    return Flash(ok, whole, wifi_lost(whole, link) if ok else None)
