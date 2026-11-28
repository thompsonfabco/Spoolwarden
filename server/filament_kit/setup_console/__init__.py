"""The setup console's routes, by tab; setup_page.py keeps the page, its access rules and the routes it had before.

Each module gives `routes(page)`: `(path, handler, method)` entries under /setup/api, wrapped in the page's own guard.
What is viewable without the setup code here never shows the owner's addresses or serials (`redact`): with the code,
given in a POST body because the page never puts it in a URL, the same answer comes whole.
"""
from __future__ import annotations

import time

from . import (
    alerts_tab, analytics_tab, backup_tab, corrections_tab, device_tab, enterprise_tab, flash_tab, general_tab, history_tab,
    intake_tab, jobs, labels_tab, languages_tab, library_tab, logs_tab, modes, overview, presets_tab, printer_live_tab, printers_tab,
    spools_tab, start_tab, station_tab, storage_tab, trust_tab, updates_tab, voice_tab, webapp_tab,
)


class Cached:
    """A short memory for an answer that runs commands or reads big files: anyone on the home network may ask for it.
    `stamp` (the job log's time) drops it at once when a job has changed what it says."""

    def __init__(self, ttl: float = 5.0):
        self.ttl, self.at, self.stamp, self.value = ttl, 0.0, None, None

    def get(self, stamp, fn):
        now = time.monotonic()
        if self.value is None or stamp != self.stamp or now - self.at >= self.ttl:
            self.value, self.at, self.stamp = fn(), now, stamp
        return self.value

    def clear(self) -> None:
        self.value = None


def routes(page) -> list[tuple]:
    page.overview_cache, page.updates_cache = Cached(), Cached()
    return (jobs.routes(page) + modes.routes(page) + overview.routes(page) + updates_tab.routes(page)
            + printers_tab.routes(page) + trust_tab.routes(page) + presets_tab.routes(page) + webapp_tab.routes(page)
            + logs_tab.routes(page) + library_tab.routes(page) + general_tab.routes(page) + backup_tab.routes(page) + station_tab.routes(page)
            + intake_tab.routes(page) + flash_tab.routes(page) + corrections_tab.routes(page) + storage_tab.routes(page)
            + alerts_tab.routes(page) + device_tab.routes(page) + spools_tab.routes(page) + start_tab.routes(page)
            + analytics_tab.routes(page) + history_tab.routes(page) + voice_tab.routes(page)
            + labels_tab.routes(page) + languages_tab.routes(page) + enterprise_tab.routes(page)
            + printer_live_tab.routes(page, sm=page.spoolman))
