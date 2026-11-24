"""The pretend printers: a Bambu-like and a Klipper-like family that answer the printer-family
interface (`bridge.family.PrinterFamily`) from the sample set, with no network and no real printer.

They move: the Bambu-like "Workshop X1" prints `bracket-v3.3mf` from 42 %; the Klipper-like "Garage Voron" starts a
print 20 s after the demo starts and, at `ALERT_AT` (two minutes), pauses it with its filament sensor saying no
filament: the watcher (`watch.Watcher`, listening through `add_listener` as it does to the real families) writes the
`print.runout` event, the scripted alert "Filament ran out". Pause, resume and stop work; a stopped or finished
print is recorded in the demo's usage log (grams taken from the slot's spool in the demo inventory); slots load and
unload. "Study A1 mini" is offline and "mainsailos" is a found printer nobody added. The camera: none
(`camera_kind` is None; the camera routes say `demo.NO_CAMERA`).

Time comes from `clock` (seconds; a test passes its own) and moves only when `tick()` runs: `start()` runs it every
second on a thread, every read runs it too, so a reader sees the moment it asks. `rearm()` starts the script again (the
demo has just been switched on).
"""
from __future__ import annotations

import logging
import os
import threading
import time
import weakref
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

from ..bridge import loading, registry, status, xmlfile
from .inventory import _children
from ..bridge.connection import PrinterOffline, UnknownPrinter

log = logging.getLogger(__name__)

ALERT_AT = 120.0            # seconds after the demo starts (or restarts): the scripted alert
KLIPPER_PRINT_AT = 20.0     # seconds after the start: the Garage Voron starts its print
IDLE_AFTER_END = 20.0       # seconds a finished or stopped print shows before the printer is idle
RESTART_AFTER = 40.0        # seconds an idle Workshop X1 waits before it starts its print again
RUNOUT_SENSOR = "filament_switch_sensor runout"
USAGE_FILE = "usage.xml"
KEEP_USAGE = 500
ACTIONS = ("pause", "resume", "stop", "light_on", "light_off")


@dataclass
class Slot:
    label: str
    color: str
    material: str


@dataclass
class Pretend:
    """One pretend printer: the sample's facts and what it is doing now."""
    pid: str
    family: str
    name: str
    model: str
    address: str
    port: int
    online: str                       # "connected" | "offline" | "found"
    slots: list[Slot]
    job: str = ""
    layers: int = 0
    total_min: int = 0
    nozzle: float = 0.0
    bed: float = 0.0
    light: str | None = None
    # what it does now
    gcode: str = "IDLE"               # RUNNING | PAUSE | IDLE | FINISH | FAILED (Bambu's words, both families)
    percent: float = 0.0
    since: float = 0.0                # the clock reading of the last change of `gcode`
    runout: bool | None = None        # the filament sensor: False = no filament
    at: float = 0.0                   # the clock reading when time was last moved on
    first: dict = field(default_factory=dict)     # the sample's job and percent
    done: dict = field(default_factory=dict)      # the script's steps already played: ran, alert

    @property
    def configured(self) -> bool:
        return self.online != "found"


def _total_min(remaining: float, percent: float) -> int:
    """The print's whole length in minutes, from the minutes left at `percent` (the sample gives those)."""
    return int(round(remaining / (1 - percent / 100))) if percent < 100 else int(remaining)


def _load_sample(path=None) -> list[Pretend]:
    from . import SAMPLE
    root = xmlfile.fromstring((path or SAMPLE).read_bytes())
    out = []
    for el in _children(root, "printers"):
        a = el.attrib
        out.append(Pretend(
            pid=a["id"], family=a.get("family", "bambu"), name=a.get("name", ""), model=a.get("model", ""),
            address=a.get("ip") or a.get("host", ""), port=int(a.get("port", "7125")), online=a.get("state", "found"),
            slots=[Slot(s.get("label", ""), s.get("color", ""), s.get("material", "")) for s in el.findall("slot")],
            job=a.get("job", ""), layers=int(a.get("total-layers", "0")),
            total_min=_total_min(float(a.get("remaining-min", "0")), float(a.get("percent", "0"))),
            nozzle=float(a.get("nozzle", "0")), bed=float(a.get("bed", "0")), light=a.get("light"),
            first={"percent": float(a.get("percent", "0")), "job": a.get("job", "")}))
    return out


def registry_printers(sample=None) -> dict[str, registry.Printer]:
    """What the demo's printer registry holds (`registry.load()` while the demo is on): the three printers and the one
    found. The two real ones are configured; the found one is not."""
    out = {}
    for p in _load_sample(sample):
        out[p.pid] = registry.Printer(
            serial=p.pid, name=p.name, model=p.model, ip=p.address if p.family == "bambu" else "",
            configured=p.configured, family=p.family, host=p.address if p.family == "klipper" else "", port=p.port)
    return out


_made: "weakref.WeakSet[Family]" = weakref.WeakSet()
_switches: "weakref.WeakSet[Switch]" = weakref.WeakSet()


def switched() -> None:
    """The demo has just been switched on or off: every real family wired behind a `Switch` matches its connections to
    the new state now (on: it closes what it had open, off: it opens what the real registry has), not at its next round."""
    for s in list(_switches):
        s.sync()


def rearm_all() -> None:
    """Start the script again in every pretend family of this process (the demo has just been switched on)."""
    for f in list(_made):
        f.rearm()


def seed_usage(now: datetime | None = None, sample=None) -> None:
    """The sample's two earlier prints in the demo's usage log ("Filament used, newest first")."""
    from . import SAMPLE
    root = xmlfile.fromstring((sample or SAMPLE).read_bytes())
    now = now or datetime.now(timezone.utc)
    for el in reversed(_children(root, "usage")):
        record_usage(el.get("printer", ""), "", float(el.get("grams", "0")), el.get("spool"), el.get("slot", ""),
                     at=now - timedelta(hours=float(el.get("ago-hours", "0"))))


class NoManualMaps:
    """What a pretend Klipper printer's `manual` is (the real one, `bridge.klipper.manual.KlipperManualMaps`, saves a
    slot-map.xml in the printer's state folder and reads the printer's slots): map by loading filament has nothing to
    watch here, so every step is the demo's stop sentence as a manual-map refusal (409), never a 500, and nothing is
    written."""

    def _refuse(self, *a, **k):
        from ..bridge.manual import ManualError
        from . import NOT_IN_DEMO, NOT_IN_DEMO_CODE
        raise ManualError(409, NOT_IN_DEMO_CODE, NOT_IN_DEMO)

    start = view = confirm = skip = stop = answer_offer = _refuse


class Family:
    """One pretend family: the printers of `family` ("bambu" or "klipper") of the sample set."""

    def __init__(self, family: str, spoolman=None, clock=time.monotonic, sample=None):
        self.family, self.spoolman, self.clock = family, spoolman, clock
        self._lock = threading.RLock()
        self._listeners: list = []
        self._sample = sample
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.t0 = 0.0
        self.p: dict[str, Pretend] = {}
        self.manual = NoManualMaps()          # the Klipper family's `manual` (api.py manual_call); a Bambu one uses the mapper's
        self.rearm()
        _made.add(self)

    # --- the script -----------------------------------------------------------------------------------------------
    def rearm(self) -> None:
        """Start the script again: the printers are as the sample has them and the alert is two minutes away."""
        with self._lock:
            self.t0 = self.clock()
            self.p = {p.pid: p for p in _load_sample(self._sample) if p.family == self.family}
            for p in self.p.values():
                p.since = p.at = self.t0
                if p.online == "connected" and p.first.get("job") and self.family == "bambu":
                    p.gcode, p.percent = "RUNNING", p.first["percent"]

    def _set(self, p: Pretend, gcode: str, now: float) -> None:
        p.gcode, p.since = gcode, now

    def tick(self) -> None:
        """Move time on: progress, the Voron's print and its runout, the end of a print. Tells the listeners when a
        printer's state changed. Does nothing while the demo is off (the pretend printers tell nobody then)."""
        from . import is_on
        if not is_on():
            return
        with self._lock:
            now = self.clock()
            changed = []
            for p in self.p.values():
                if p.online != "connected":
                    continue
                before = (p.gcode, int(p.percent), p.runout)
                self._move(p, now)
                if (p.gcode, int(p.percent), p.runout) != before:
                    changed.append(p)
        for p in changed:
            self._tell(p)

    def _move(self, p: Pretend, now: float) -> None:
        if p.family == "klipper":
            if p.gcode == "IDLE" and now - self.t0 >= KLIPPER_PRINT_AT and not p.done.get("ran"):
                p.done["ran"], p.job, p.total_min, p.percent, p.runout = True, "gear-housing.gcode", 90, 0.0, True
                p.nozzle, p.bed = 250.0, 100.0
                self._set(p, "RUNNING", now)
            if p.gcode == "RUNNING" and now - self.t0 >= ALERT_AT and not p.done.get("alert"):
                p.done["alert"] = True
                p.runout = False                       # the sensor says no filament: the watcher reads a runout
                self._set(p, "PAUSE", now)
        last, p.at = p.at, now
        if p.gcode == "RUNNING" and p.total_min:
            p.percent = min(100.0, p.percent + (now - last) * 100 / (p.total_min * 60))
            if p.percent >= 100.0:
                self._finish(p, "FINISH", now)
        elif p.gcode in ("FINISH", "FAILED") and now - p.since >= IDLE_AFTER_END:
            self._set(p, "IDLE", now)
        elif p.gcode == "IDLE" and p.family == "bambu" and p.first.get("job") and now - p.since >= RESTART_AFTER:
            p.job, p.percent = p.first["job"], 0.0
            self._set(p, "RUNNING", now)

    # --- listeners (the watcher) ---------------------------------------------------------------------------------
    def add_listener(self, fn) -> None:
        self._listeners.append(fn)

    def _tell(self, p: Pretend) -> None:
        for fn in list(self._listeners):
            try:
                if self.family == "klipper":
                    fn(p.pid, "status", {})
                else:
                    fn(p.pid, self.report(p.pid), False)
            except Exception:  # noqa: BLE001 - a listener must not stop the printers
                import logging
                logging.getLogger(__name__).exception("demo: a listener failed")

    def start(self) -> None:
        self._stop.clear()

        def run():
            while not self._stop.wait(1.0):
                self.tick()
        self._thread = threading.Thread(target=run, name="demo-printers", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)
            self._thread = None

    def sync(self) -> None:
        pass

    def scan(self, subnet=None) -> list[dict]:
        """"Find Klipper printers": the found printer of the sample set, without looking at any network."""
        return [{"id": p.pid, "host": p.address, "port": p.port, "name": p.name, "configured": False}
                for p in self.p.values() if p.online == "found"]

    # --- reads ----------------------------------------------------------------------------------------------------
    def _get(self, pid: str) -> Pretend | None:
        self.tick()
        return self.p.get(pid)

    def state(self, pid: str) -> str:
        p = self._get(pid)
        if p is None or not p.configured:
            return "not_configured"
        return "connected" if p.online == "connected" else "offline"

    def wait_settled(self, pid: str, timeout: float) -> str:
        return self.state(pid)

    def model(self, pid: str):
        """As the real families: the Bambu Manager's is the model text, the Klipper family's `(model, system, adapter)`
        (here a model that has only a name and a key: nothing is read from klipper_hardware.xml)."""
        p = self.p.get(pid)
        if self.family == "klipper":
            return (SimpleNamespace(name=p.model if p is not None else "", key="demo"), None, None)
        return (p.model or None) if p is not None else None

    def verified(self, pid: str) -> bool:
        return True

    def camera_kind(self, pid: str) -> str | None:
        return None

    def camera_url(self, pid: str) -> str | None:
        return None

    def summary(self, pid: str) -> dict | None:
        p = self._get(pid)
        if p is None:
            return None
        live = p.online == "connected"
        return {"gcode_state": p.gcode if live else "UNKNOWN", "percent": int(p.percent) if live else 0,
                "remaining_min": self._remaining(p), "job": (p.job or None) if live else None}

    @staticmethod
    def _remaining(p: Pretend) -> int:
        if p.gcode not in ("RUNNING", "PAUSE") or not p.total_min:
            return 0
        return max(0, int(round(p.total_min * (100 - p.percent) / 100)))

    def _spools(self) -> list[dict]:
        sm = self.spoolman
        if sm is None:
            from ..spoolman import Spoolman
            sm = Spoolman("http://demo.invalid")
        return sm.get("spool") or []

    def _slot_spool(self, p: Pretend, label: str, by_loc: dict) -> dict | None:
        return by_loc.get(status.location(p.name, label, p.family))

    def status(self, pid: str) -> dict | None:
        p = self._get(pid)
        if p is None or not p.configured:
            return None
        live = p.online == "connected"
        by_loc = {s.get("location"): s for s in sorted(self._spools(), key=lambda s: s["id"])}
        slots = []
        for i, sl in enumerate(p.slots):
            sp = self._slot_spool(p, sl.label, by_loc)
            slot = {"slot": i, "label": sl.label, "say": [], "type": sl.material, "color_hex": sl.color,
                    "preset_id": "", "active": live and p.gcode in ("RUNNING", "PAUSE") and i == 0,
                    "present": bool(sp) and not (p.runout is False and p.family == "klipper" and i == 0), "reading": None,
                    "spool": status._spool_view(sp) if sp else None}
            if p.family == "bambu":
                slot["rfid"] = False
            else:
                slot["spool_id"] = sp["id"] if sp else None
            slots.append(slot)
        unit = ({"unit": 0, "label": "A", "kind": "ams", "type_name": "AMS", "say": [], "slots": slots}
                if p.family == "bambu" else
                {"unit": 0, "label": "Tools", "kind": "toolchanger", "type_name": "Toolchanger", "say": [],
                 "slots": slots})
        running = live and p.gcode in ("RUNNING", "PAUSE")
        eta = None
        if running and self._remaining(p):
            eta = datetime.fromtimestamp(time.time() + self._remaining(p) * 60).astimezone().isoformat(timespec="minutes")
        out = {"gcode_state": p.gcode if live else "UNKNOWN", "stage": "", "job": p.job if live else "",
               "percent": int(p.percent) if live else 0,
               "layer": int(p.layers * p.percent / 100) if running and p.layers else None,
               "total_layers": p.layers if running and p.layers else None,
               "remaining_min": self._remaining(p), "eta": eta,
               "temps": {"nozzle": [{"current": p.nozzle if running else 25.0, "target": p.nozzle if running else 0}],
                         "bed": {"current": p.bed if running else 24.0, "target": p.bed if running else 0},
                         "chamber": None},
               "ams": [unit] if live else [], "presence": "bits" if p.family == "bambu" else "printer",
               "notes": [], "hms": [], "print_error": "", "light": p.light if live else None,
               "updated": int(time.time()) if live else None}
        if p.family == "klipper":
            out.update(family="klipper", state="connected" if live else "offline", spoolman_warning=None)
        return out

    def report(self, pid: str) -> dict:
        """The Bambu report the watcher reads (`print` object) for a Bambu-like printer."""
        p = self.p[pid]
        return {"gcode_state": p.gcode, "subtask_name": p.job, "mc_percent": int(p.percent),
                "mc_remaining_time": self._remaining(p), "layer_num": int(p.layers * p.percent / 100),
                "total_layer_num": p.layers, "stg_cur": 255 if p.gcode != "PAUSE" else 16, "print_error": 0}

    def wait_report(self, pid: str, predicate, timeout: float):
        return self.report(pid) if pid in self.p and self.p[pid].online == "connected" else None

    def request_pushall(self, pid: str) -> None:
        pass

    def forget_report_key(self, pid: str, key: str) -> None:
        pass

    def live(self, pid: str, slot: bool = True) -> dict | None:
        """What the watcher reads from a Klipper-like printer (as `KlipperFamily.live`)."""
        p = self._get(pid)
        if p is None or not p.configured:
            return None
        state = "connected" if p.online == "connected" else "offline"
        ps = {"RUNNING": "printing", "PAUSE": "paused", "FINISH": "complete", "FAILED": "cancelled",
              "IDLE": "standby"}[p.gcode]
        sensors = {RUNOUT_SENSOR: p.runout} if p.runout is not None else {}
        active = None
        if slot and p.slots and p.runout is not None:
            active = {"label": p.slots[0].label, "present": p.runout}
        return {"state": state, "print": ps, "job": p.job, "percent": int(p.percent), "message": "", "sensors": sensors,
                "slot": active}

    # --- commands ------------------------------------------------------------------------------------------------
    def command(self, pid: str, action: str) -> dict:
        if action not in ACTIONS:
            raise ValueError(f"unknown action {action!r}")
        p = self._get(pid)
        if p is None:
            raise UnknownPrinter(pid)
        if p.online != "connected":
            raise PrinterOffline(pid)
        with self._lock:
            now = self.clock()
            if action == "pause" and p.gcode == "RUNNING":
                self._set(p, "PAUSE", now)
            elif action == "resume" and p.gcode == "PAUSE":
                p.runout = True if p.runout is not None else None
                self._set(p, "RUNNING", now)
            elif action == "stop" and p.gcode in ("RUNNING", "PAUSE"):
                self._finish(p, "FAILED", now)
            elif action in ("light_on", "light_off") and p.light is not None:
                p.light = "on" if action == "light_on" else "off"
            elif action in ("pause", "resume", "stop"):
                return {"ok": False, "reply": {"reason": "refused", "message": f"the printer cannot {action} now"}}
        self._tell(p)
        return {"ok": True, "reply": {"result": "success"}}

    def _finish(self, p: Pretend, gcode: str, now: float) -> None:
        grams = round(18.0 * (p.percent if gcode == "FAILED" else 100.0) / 100, 1)
        slot = p.slots[0].label if p.slots else ""
        self._set(p, gcode, now)
        if p.runout is False:
            p.runout = True
        try:
            sp = self._slot_spool(p, slot, {s.get("location"): s for s in self._spools()})
            if sp and grams > 0:
                from ..spoolman import Spoolman
                (self.spoolman or Spoolman("http://demo.invalid")).use(sp["id"], grams)
            record_usage(p.pid, p.job, grams, sp["id"] if sp else None, slot)
        except Exception:  # noqa: BLE001 - recording must not break the print's end
            import logging
            logging.getLogger(__name__).exception("demo: cannot record the filament used")

    # --- loads ----------------------------------------------------------------------------------------------------
    def _slot(self, p: Pretend, label: str) -> Pretend:
        if not any(s.label == label for s in p.slots):
            raise loading.NotFound(f"{label}: no such slot on {p.name}")
        return p

    def load(self, sm, pid: str, label: str, spool_id: int, dry: bool, *, sealed_location: str = loading.SEALED) -> dict:
        p = self._get(pid)
        if p is None or not p.configured:
            raise loading.NotFound(f"no printer {pid}")
        if p.online != "connected":
            raise PrinterOffline(pid)
        self._slot(p, label)
        spool = loading._spool(sm, spool_id)
        loc = status.location(p.name, label, p.family)
        if p.gcode == "RUNNING" and p.slots and p.slots[0].label == label:
            raise loading.SlotInUse(f"{p.name} is printing; load {label} when it is paused or done")
        patch = loading.target_patch(spool, loc)
        moved = loading.send_back(sm, loading.occupants(sm, loc, spool["id"]), dry)
        final = spool
        if not dry:
            if patch:
                final = sm.patch(f"spool/{spool['id']}", patch)
            loading.report_loaded(spool, patch, label)
            if p.runout is False and p.slots[0].label == label:
                p.runout = True
        else:
            final = {**spool, **({"location": patch["location"]} if patch else {})}
        return {"slot": {"label": label, "location": loc}, "preset": None, "dry_run": bool(dry), "problems": [],
                "nozzle_mm": 0.4, "nozzle_assumed": True, "spool": final, "confirmed": None if dry else True,
                "moved_back": moved}

    def unload(self, sm, pid: str, label: str, dry: bool, *, sealed_location: str = loading.SEALED) -> dict:
        p = self._get(pid)
        if p is None or not p.configured:
            raise loading.NotFound(f"no printer {pid}")
        self._slot(p, label)
        loc = status.location(p.name, label, p.family)
        if p.gcode == "RUNNING" and p.slots[0].label == label:
            raise loading.SlotInUse(f"{p.name} is printing; unload {label} once it is done")
        here = loading.occupants(sm, loc)
        if not here:
            raise loading.NotFound(f"no spool is in {p.name} slot {label}")
        moved = loading.send_back(sm, here, dry)
        first = min(here, key=lambda s: s["id"])
        spool = {**first, "location": moved[0]["location"]} if dry else sm.get(f"spool/{first['id']}")
        return {"spool": spool, "slot": {"label": label, "location": loc}, "preset": None, "dry_run": bool(dry),
                "moved_back": moved, "problems": []}

    def clear(self, sm, pid: str, label: str, dry: bool, *, sealed_location: str = loading.SEALED) -> dict:
        """Clear slot `label`: its spool goes back as `unload` moves it; none recorded is not an error. The answer is the kit
        route's (`loading.clear_view`); the pretend printer has no setting to clear (`printer_cleared` False)."""
        p = self._get(pid)
        if p is None or not p.configured:
            raise loading.NotFound(f"no printer {pid}")
        self._slot(p, label)
        loc = status.location(p.name, label, p.family)
        if p.gcode == "RUNNING" and p.slots[0].label == label:
            raise loading.SlotInUse(f"{p.name} is printing; clear {label} once it is done")
        here = loading.occupants(sm, loc)
        moved = loading.send_back(sm, here, dry) if here else []
        first = min(here, key=lambda s: s["id"]) if here else None
        spool = None if first is None else ({**first, "location": moved[0]["location"]} if dry else sm.get(f"spool/{first['id']}"))
        return loading.clear_view({"slot": {"label": label, "location": loc}, "spool": spool, "moved_back": moved,
                                   "printer_cleared": False, "dry_run": bool(dry)}, False)

    def return_spool(self, sm, pid: str, spool_id, *, grams: float | None = None, rules=None, dry: bool = False,
                     gross: float | None = None) -> dict:
        """A spool taken out of the pretend printer by hand goes home (`inventory.return_spool`); there is no printer to tell."""
        from .. import inventory
        if self._get(pid) is None:
            raise loading.NotFound(f"no printer {pid}")
        return inventory.return_spool(sm, spool_id, grams=grams, rules=rules, dry=dry, gross=gross)

    def usage(self, pid: str, days: float) -> list[dict]:
        return usage_entries(pid, days)


class Switch:
    """One object in the Hub's wiring: the real family (a Bambu `Manager`, a `KlipperFamily`) while the demo is off, the
    pretend one while it is on, decided at every call, so the demo can be switched on and off without a restart. The
    watcher's listener goes to both; start and stop reach both. `real` is the family as it is wired (a family that is
    off, `None`, is not wrapped)."""

    def __init__(self, real, pretend: Family):
        self._real, self._pretend = real, pretend
        _switches.add(self)

    def sync(self) -> None:
        """The real family reconciles its connections with the demo's state (a family without `sync` has none)."""
        try:
            getattr(self._real, "sync", lambda: None)()
        except Exception:  # noqa: BLE001 - the next round of the family tries again
            log.exception("demo: the real printer family could not be matched to the demo's state")

    def _now(self):
        from . import is_on
        return self._pretend if is_on() else self._real

    def add_listener(self, fn) -> None:
        self._pretend.add_listener(fn)
        self._real.add_listener(fn)

    def start(self, *a, **k) -> None:
        _switches.add(self)
        self._pretend.start()
        self._real.start(*a, **k)

    def stop(self) -> None:
        _switches.discard(self)
        self._pretend.stop()
        self._real.stop()

    def __getattr__(self, name: str):
        if name.startswith("__") or name in ("_real", "_pretend"):
            raise AttributeError(name)
        return getattr(self._now(), name)


# --- the demo's usage log (`<state>/demo/usage.xml`) ----------------------------------------------------------------
_ulock = threading.Lock()


def _usage_path():
    from . import recorded
    return recorded(USAGE_FILE)


def _read_usage() -> list[dict]:
    try:
        root = xmlfile.fromstring(_usage_path().read_bytes())
    except (OSError, ValueError, ET.ParseError):
        return []
    return [dict(e.attrib) for e in root.findall("entry")]


def record_usage(pid: str, job: str, grams: float, spool_id, slot: str, *, at: datetime | None = None) -> dict:
    """Add one entry to the demo's usage log (newest last; the oldest beyond KEEP_USAGE go)."""
    rec = {"id": f"demo-{int((at or datetime.now(timezone.utc)).timestamp() * 1000)}-{pid}",
           "at": (at or datetime.now(timezone.utc)).astimezone().isoformat(timespec="seconds"), "printer": pid,
           "job": job, "grams": str(grams), "spool-id": "" if spool_id is None else str(spool_id), "slot": slot}
    with _ulock:
        rows = _read_usage()[-(KEEP_USAGE - 1):] + [rec]
        root = ET.Element("usage", version="1")
        for r in rows:
            ET.SubElement(root, "entry", {k: xmlfile.clean(v) for k, v in r.items()})
        ET.indent(root)
        path = _usage_path()
        tmp = path.with_name(path.name + ".tmp")
        ET.ElementTree(root).write(tmp, encoding="utf-8", xml_declaration=True)
        os.replace(tmp, path)
    return rec


def usage_entries(pid: str, days: float, now: float | None = None) -> list[dict]:
    """The printer's entries of the last `days` days, newest first, shaped like the Bambu usage log's."""
    since = (now if now is not None else time.time()) - days * 86400
    out = []
    for r in reversed(_read_usage()):
        if r.get("printer") != pid:
            continue
        try:
            at = datetime.fromisoformat(r["at"]).timestamp()
        except (KeyError, ValueError):
            continue
        if at >= since:
            sid = r.get("spool-id", "")
            out.append({"id": r["id"], "at": r["at"], "job": r.get("job", ""), "grams": float(r.get("grams", 0)),
                        "spool_id": int(sid) if sid.isdigit() else None, "slot": r.get("slot", ""),
                        "observe": False, "undone": False})
    return out
