"""Auto slot assignment: watch a printer's slots and load a spool wherever it appears.

An Auto assignment binds a spool to a printer with no slot named (at most one per printer, in memory only, 15 min
TTL). `start` records each watchable slot's baseline (`present is False` vs loaded); `tick` watches for a watchable
slot that was seen empty and then stays loaded and read for `SETTLE` s (neither an RFID spool nor feeding an active
print), then applies a real load with `auto="yes"` on the `spool.loaded` event. If a second watchable slot also
qualifies before a load applies, the assignment enters `state="choose"` and waits for `choose` (or `cancel` / TTL).

One spool, one pending placement (D3, D12): starting Auto for a spool ends every earlier wait for it, Auto or named,
on every printer; a named wait or load of it ends its Auto everywhere; a load of it, any way (Auto, named, Klipper's
follow mode), ends the rest (`spool_placed`). A slot that waits for a named spool is never taken by Auto.
"""
from __future__ import annotations

import functools
import json
import logging
import re
import threading
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import event_sink, storage
from . import family, layout, loading, mapping, pending, registry, status
from .connection import PrinterOffline, UnknownPrinter

log = logging.getLogger(__name__)

TTL = pending.TTL            # seconds an Auto assignment waits before expiring (15 min)
SETTLE = pending.SETTLE      # seconds a slot must stay loaded and read before applying
POLL = pending.POLL          # seconds between watcher ticks

BAMBU_WATCHABLE_KINDS = frozenset({"ams", "ams_2pro", "ams_ht", "ams_lite"})
KLIPPER_WATCHABLE_ADAPTERS = frozenset({"afc", "happyhare"})
MAX_VERIFIED_HT_ID = layout.HT_BASE + 1     # HT-A (128) and HT-B (129); >= 130 are unverified in bambu_hardware.xml
ONLINE = ("connected", "needs_developer_mode", "unknown_model")
FINAL = (loading.NoPreset, loading.NotFound, loading.SlotWriteFailed, UnknownPrinter, family.ModuleOff)


@functools.lru_cache(maxsize=1)
def _catalogue() -> dict[str, str]:
    path = Path(__file__).resolve().parent.parent / "shared_static" / "strings.js"
    text = path.read_text(encoding="utf-8")
    return json.loads(re.sub(r",\s*}$", "}", text[text.index("{"):text.rindex("}") + 1]))


def _say(key: str, **fill: str) -> str:
    text = _catalogue()[key]
    return re.sub(r"\{(\w+)\}", lambda m: str(fill.get(m.group(1), m.group(0))), text) if fill else text


AUTO_NOT_POSSIBLE = _say("auto_not_possible")


class AutoError(Exception):
    """A refusal of an Auto assignment request, carrying the API error code and status."""

    def __init__(self, code: str, message: str, status_code: int = 409):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status_code


def is_reserved_label(label) -> bool:
    """`auto` (case-insensitive) is reserved and may never name a real slot."""
    return str(label).strip().lower() == "auto"


def format_spool_name(spool: dict | None, spool_id=None) -> str:
    """Display name of a spool: '<vendor> <name>', else 'Spool {id}' (no '#'), else 'the spool'."""
    fil = (spool or {}).get("filament") or {}
    vendor = status._s((fil.get("vendor") or {}).get("name"))
    fname = status._s(fil.get("name"))
    name = f"{vendor} {fname}".strip()
    if name:
        return name
    sid = (spool or {}).get("id")
    if sid is None:
        sid = spool_id
    if sid is not None and not isinstance(sid, bool):
        return f"Spool {sid}"
    return "the spool"


def _spool_summary(spool: dict | None, spool_id=None) -> dict:
    fil = (spool or {}).get("filament") or {}
    sid = (spool or {}).get("id")
    if sid is None and not isinstance(spool_id, bool):
        sid = spool_id
    return {
        "id": sid,
        "name": format_spool_name(spool, sid),
        "material": status._s(fil.get("material")),
        "color_hex": status._s(fil.get("color_hex")).lstrip("#"),
    }


def _is_watchable_slot(st: dict, unit: dict, slot: dict, *, adapter: str | None = None) -> bool:
    """True when `slot` has a verified filament sensor Auto can watch."""
    if not isinstance(slot, dict) or not isinstance(slot.get("label"), str) or not slot.get("label"):
        return False
    if is_reserved_label(slot["label"]):
        return False
    if not isinstance(slot.get("present"), bool):
        return False
    if st.get("family") == "klipper":
        if adapter is not None and adapter not in KLIPPER_WATCHABLE_ADAPTERS:
            return False
        return unit.get("kind") == "lane"
    if st.get("presence") != "bits":
        return False
    kind = unit.get("kind")
    if kind == layout.EXT_KIND or kind not in BAMBU_WATCHABLE_KINDS:
        return False
    uid = unit.get("unit")
    if uid is not None:
        if isinstance(uid, bool):
            return False
        try:
            uid_int = int(uid)
        except (TypeError, ValueError):
            return False
        if kind == "ams_ht" or uid_int >= layout.HT_BASE:
            return layout.HT_BASE <= uid_int <= MAX_VERIFIED_HT_ID
        return 0 <= uid_int <= 3
    return True


def _watchable_slots(st: dict | None, *, adapter: str | None = None):
    if not isinstance(st, dict):
        return
    for u in st.get("ams") or []:
        if not isinstance(u, dict):
            continue
        for sl in u.get("slots") or []:
            if _is_watchable_slot(st, u, sl, adapter=adapter) and sl.get("watchable") is not False:
                yield sl


def watchable_labels(st: dict | None, *, adapter: str | None = None) -> list[str]:
    """Labels of all watchable slots in status order."""
    return [sl["label"] for sl in _watchable_slots(st, adapter=adapter)]


def _eligible(st: dict, sl: dict) -> bool:
    gs = st.get("gcode_state")
    printing = gs is None or gs in status.ACTIVE
    return (sl.get("present") is True and sl.get("reading") is not True
            and not sl.get("pending") and not sl.get("rfid") and not (printing and sl.get("active")))


def is_auto_possible(st: dict | None, *, adapter: str | None = None) -> bool:
    """True when the printer status reports at least two watchable slots."""
    if not isinstance(st, dict):
        return False
    if not st.get("ams") and "auto_possible" in st:
        return bool(st["auto_possible"])
    return len(watchable_labels(st, adapter=adapter)) >= 2


def _adapter_for(manager, serial: str, p: registry.Printer | None) -> str | None:
    if p is not None and p.family == "klipper":
        kf = manager if hasattr(manager, "slots") else getattr(manager, "klipper", None)
        if kf is not None and hasattr(kf, "model"):
            try:
                return kf.model(serial)[2].name
            except Exception:  # noqa: BLE001 - best-effort adapter check
                return None
    return None


def _sealed(sealed_location: str | None) -> str:
    if sealed_location is not None:
        return sealed_location
    try:
        return storage.current().sealed_location().name
    except Exception:  # noqa: BLE001
        return loading.SEALED


def _load_into_slot(manager, sm, serial: str, label: str, spool_id: int, *,
                    sealed_location: str | None = None, **load_kw) -> dict:
    """Apply a named load into `label` and tag any emitted `spool.loaded` event with `auto='yes'`."""
    p = registry.load().get(serial)
    if p is None:
        raise UnknownPrinter(serial)
    kf = None
    if p.family == "klipper":
        kf = manager if hasattr(manager, "load") else getattr(manager, "klipper", None)
        if kf is None:
            raise family.ModuleOff("klipper")
    elif isinstance(manager, family.Printers) and manager.bambu is None:
        raise family.ModuleOff("printers")
    pending_store = pending.STORE
    pending_label = loading._label(serial, label) if kf is None else label
    slot_lock = pending_store.slot_lock(serial, pending_label) if kf is None else nullcontext()
    with slot_lock, event_sink.spool_data("spool.loaded", auto="yes"):
        st = manager.status(serial)
        adapter = _adapter_for(manager, serial, p)
        if (serial, pending_label) in pending_store.jobs() or not any(
            sl["label"] == label and _eligible(st, sl) for sl in _watchable_slots(st, adapter=adapter)
        ):
            raise AutoError("nothing_to_choose", _say("auto_slot_gone"))
        sealed = _sealed(sealed_location)
        if kf is not None:
            r = kf.load(sm, serial, label, spool_id, False, sealed_location=sealed)
            return {**r, "confirmed": bool(r.get("confirmed"))}
        r = loading.load(manager, sm, serial, label, spool_id, False, sealed_location=sealed, **load_kw)
        pending_store.cancel(serial, pending_label, "Auto loaded the slot")
        return {**r, "confirmed": bool(r.get("confirmed"))}


@dataclass
class AutoJob:
    manager: object
    sm: object
    serial: str
    printer_name: str
    spool_id: int
    spool_name: str
    spool: dict
    at: float
    expires_at: str
    seen_empty: dict[str, bool] = field(default_factory=dict)
    since: dict[str, float] = field(default_factory=dict)
    state: str = "waiting"          # "waiting" | "choose"
    slots: list[str] = field(default_factory=list)
    waiting: str | None = None
    load_kw: dict = field(default_factory=dict)


class Store:
    """Active Auto assignments, at most one per printer serial; in memory only."""

    def __init__(self, clock=time.monotonic, *, start_thread: bool = True):
        self.clock = clock
        self._start_thread = start_thread
        self._jobs: dict[str, AutoJob] = {}
        self._lock = threading.Lock()
        self._send_locks: dict[str, threading.Lock] = {}
        self._spool_locks: dict[int, threading.RLock] = {}
        self._thread: threading.Thread | None = None

    # --- locking & lifecycle ---

    def _send_lock(self, serial: str) -> threading.Lock:
        with self._lock:
            return self._send_locks.setdefault(serial, threading.Lock())

    def spool_lock(self, spool_id):
        """Acquire before an Auto printer send lock or a Pending slot lock, through dispatch and cancellation."""
        try:
            sid = int(spool_id)
        except (TypeError, ValueError) as e:
            raise loading.NotFound(f"spool {spool_id!r} not found") from e
        with self._lock:
            return self._spool_locks.setdefault(sid, threading.RLock())

    def _ensure_thread(self) -> None:
        if not self._start_thread:
            return
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="auto-slot", daemon=True)
                self._thread.start()

    def _run(self) -> None:
        while True:
            time.sleep(POLL)
            self.tick()

    def _active_unlocked(self, serial: str, now: float) -> AutoJob | None:
        job = self._jobs.get(serial)
        if job is not None and now - job.at > TTL:
            self._jobs.pop(serial, None)
            log.info("auto dropped %s spool %s: the filament was not loaded within 15 min", serial, job.spool_id)
            return None
        return job

    def jobs(self) -> dict[str, AutoJob]:
        with self._lock:
            return dict(self._jobs)

    def clear(self) -> None:
        with self._lock:
            self._jobs.clear()

    # --- API ---

    def start(self, manager, sm, serial: str, spool_id, dry: bool = True, **load_kw) -> dict:
        """Preview (`dry=True`) or start (`dry=False`) an Auto assignment on `serial` for `spool_id`."""
        p = registry.load().get(serial)
        if p is None:
            raise UnknownPrinter(serial)
        if p.family == "klipper" and isinstance(manager, family.Printers) and manager.klipper is None:
            raise family.ModuleOff("klipper")
        if p.family != "klipper" and isinstance(manager, family.Printers) and manager.bambu is None:
            raise family.ModuleOff("printers")
        if hasattr(manager, "state"):
            pstate = manager.state(serial)
            if pstate == "cert_changed":
                raise mapping.CertChanged(serial)
            if pstate not in ONLINE:
                raise PrinterOffline(serial)
        st = manager.status(serial)
        adapter = _adapter_for(manager, serial, p)
        slots = list(_watchable_slots(st, adapter=adapter))
        watching = [sl["label"] for sl in slots]
        if len(watching) < 2:
            raise AutoError("auto_not_possible", AUTO_NOT_POSSIBLE)
        if isinstance(spool_id, bool):
            raise loading.NotFound(f"spool {spool_id!r} not found")
        spool = loading._spool(sm, spool_id)
        sid = int(spool["id"])
        spool_info = _spool_summary(spool, sid)
        spool_name = spool_info["name"]
        printer_name = registry.display_name(p)

        if dry:
            now = self.clock()
            with self._lock:
                old = self._active_unlocked(serial, now)
            out: dict = {"auto": True, "watching": watching, "dry_run": True}
            if old is not None:
                out["replaces"] = {"spool_id": old.spool_id, "name": old.spool_name}
            return out

        seen_empty = {sl["label"]: sl["present"] is False for sl in slots}
        now = self.clock()
        expires_at = datetime.fromtimestamp(time.time() + TTL).astimezone().isoformat(timespec="seconds")
        job = AutoJob(
            manager=manager, sm=sm, serial=serial, printer_name=printer_name,
            spool_id=sid, spool_name=spool_name, spool=spool_info,
            at=now, expires_at=expires_at, seen_empty=seen_empty, load_kw=load_kw,
        )
        with self.spool_lock(sid), self._send_lock(serial):
            with self._lock:
                old = self._active_unlocked(serial, now)
                self._jobs[serial] = job
            # the newest scan wins (D12): earlier waits for this spool elsewhere, Auto or named, end
            self.cancel_for_spool(sid, keep=serial, why="Auto started for it on another printer")
            pending.STORE.cancel_for_spool(sid, why="Auto started for it")
        log.info("auto %s spool %s: waiting on %s%s", serial, sid, ", ".join(watching),
                 f" (replaces spool {old.spool_id})" if old is not None else "")
        self._ensure_thread()
        return {
            "auto": True,
            "pending": True,
            "confirmed": None,
            "expires_in": int(TTL),
            "message": _say("auto_waiting", spool=spool_name, printer=printer_name),
            **({"replaces": {"spool_id": old.spool_id, "name": old.spool_name}} if old is not None else {}),
        }

    def choose(self, manager, sm, serial: str, slot: str) -> dict:
        """Apply the chosen slot when an Auto assignment is in `state='choose'`."""
        chosen = slot.strip() if isinstance(slot, str) else ""
        with self._lock:
            job = self._active_unlocked(serial, self.clock())
        if job is None:
            raise AutoError("nothing_to_choose", _say("auto_nothing_to_choose"))
        with self.spool_lock(job.spool_id), self._send_lock(serial):
            with self._lock:
                if (self._active_unlocked(serial, self.clock()) is not job or job.state != "choose"
                        or chosen not in job.slots or is_reserved_label(chosen)):
                    raise AutoError("nothing_to_choose", _say("auto_nothing_to_choose"))
                spool_id = job.spool_id
                load_kw = dict(job.load_kw)
            res = _load_into_slot(manager, sm, serial, chosen, spool_id, **load_kw)
            spool_placed(spool_id)
            log.info("auto chose %s %s spool %s", serial, chosen, spool_id)
            return res

    def cancel(self, serial: str) -> dict:
        """Cancel the active Auto assignment on `serial`, or raise `not_waiting`."""
        with self._send_lock(serial), self._lock:
            job = self._active_unlocked(serial, self.clock())
            if job is None:
                raise AutoError("not_waiting", _say("auto_not_waiting"))
            self._jobs.pop(serial, None)
        log.info("auto dropped %s spool %s: cancelled", serial, job.spool_id)
        return {"cancelled": True}

    def cancel_for_spool(self, spool_id: int, *, keep: str | None = None, why: str = "loaded into a named slot") -> int:
        """Cancel any active Auto assignment waiting for `spool_id` (placed elsewhere), except the one of `keep`."""
        sid = int(spool_id)
        removed = 0
        with self.spool_lock(sid), self._lock:
            for serial, job in list(self._jobs.items()):
                if job.spool_id == sid and serial != keep:
                    self._jobs.pop(serial, None)
                    removed += 1
                    log.info("auto dropped %s spool %s: %s", serial, sid, why)
        return removed

    def drop_printer(self, serial: str) -> bool:
        """Drop any active Auto assignment when a printer is removed."""
        with self._send_lock(serial), self._lock:
            job = self._jobs.pop(serial, None)
        if job is not None:
            log.info("auto dropped %s spool %s: printer removed", serial, job.spool_id)
        return job is not None

    def active_view(self, serial: str) -> dict | None:
        """The API/UI view of the active Auto assignment on `serial`, or None."""
        now = self.clock()
        with self._lock:
            job = self._active_unlocked(serial, now)
            if job is None:
                return None
            sid = job.spool_id
            spool = dict(job.spool)
            state = job.state
            slots = list(job.slots)
            spool_name = job.spool_name
            pname = job.printer_name
            expires_at = job.expires_at
        p = registry.load().get(serial)
        if p is not None:
            pname = registry.display_name(p)
        out = {
            "spool_id": sid,
            "spool": spool,
            "state": state,
            "text": (_say("auto_choose", spool=spool_name) if state == "choose"
                     else _say("auto_waiting", spool=spool_name, printer=pname)),
            "expires_at": expires_at,
        }
        if state == "choose":
            out["slots"] = slots
        return out

    def annotate(self, serial: str, st: dict | None, *, adapter: str | None = None) -> dict | None:
        """Add `watchable` to each slot, `auto_possible` to `st`, and `auto` when an assignment is active."""
        if not isinstance(st, dict):
            return st
        count = 0
        for u in st.get("ams") or []:
            if not isinstance(u, dict):
                continue
            for sl in u.get("slots") or []:
                if not isinstance(sl, dict):
                    continue
                w = _is_watchable_slot(st, u, sl, adapter=adapter)
                sl["watchable"] = w
                if w:
                    count += 1
        st["auto_possible"] = count >= 2
        active = self.active_view(serial)
        if active is not None:
            st["auto"] = active
        else:
            st.pop("auto", None)
        return st

    # --- watcher ---

    def tick(self) -> None:
        for serial, job in self.jobs().items():
            try:
                self._process(serial, job)
            except Exception as e:  # noqa: BLE001 - one bad job must not stop the others; it expires
                self._wait(serial, job, type(e).__name__, f"{type(e).__name__}: {e}")

    def _wait(self, serial: str, job: AutoJob, reason: str, text: str) -> None:
        if job.waiting != reason:
            job.waiting = reason
            log.warning("auto %s spool %s waits: %s", serial, job.spool_id, text)

    def _end(self, serial: str, job: AutoJob, why: str) -> None:
        with self._lock:
            if self._jobs.get(serial) is not job:
                return
            self._jobs.pop(serial, None)
        log.info("auto dropped %s spool %s: %s", serial, job.spool_id, why)

    def _process(self, serial: str, job: AutoJob) -> None:
        now = self.clock()
        if now - job.at > TTL:
            return self._end(serial, job, "the filament was not loaded within 15 min")
        if job.state == "choose":
            return
        st = job.manager.status(serial)
        if not isinstance(st, dict):
            return
        p = registry.load().get(serial)
        adapter = _adapter_for(job.manager, serial, p)
        candidates = []
        named = {label for (printer, label) in pending.STORE.jobs() if printer == serial}
        for sl in _watchable_slots(st, adapter=adapter):
            lab = sl["label"]
            if lab in named or sl.get("pending"):
                # Do not reuse an insertion the owner designated for a named spool.
                job.seen_empty[lab] = False
                continue
            if sl.get("present") is False:
                job.seen_empty[lab] = True
            if job.seen_empty.get(lab) and _eligible(st, sl):
                job.since.setdefault(lab, now)
                candidates.append(lab)
        current = set(candidates)
        for lab in list(job.since):
            if lab not in current:
                job.since.pop(lab, None)
        if len(candidates) >= 2:
            with self._lock:
                if self._jobs.get(serial) is not job or job.state != "waiting":
                    return
                job.state = "choose"
                job.slots = list(candidates)
            log.info("auto %s spool %s: multiple slots loaded (%s); waiting for choice",
                     serial, job.spool_id, ", ".join(candidates))
            return
        if len(candidates) == 1:
            lab = candidates[0]
            if now - job.since[lab] < SETTLE:
                return
            self._apply(serial, job, lab)

    def _apply(self, serial: str, job: AutoJob, label: str) -> None:
        with self.spool_lock(job.spool_id), self._send_lock(serial):
            with self._lock:
                if self._active_unlocked(serial, self.clock()) is not job or job.state != "waiting":
                    return
            try:
                r = _load_into_slot(job.manager, job.sm, serial, label, job.spool_id, **job.load_kw)
            except AutoError as e:
                job.since.pop(label, None)
                return self._wait(serial, job, e.code, e.message)
            except FINAL as e:
                return self._end(serial, job, f"refused: {e}")
            except Exception as e:  # noqa: BLE001 - SlotInUse while printing, offline, mapping running: retry
                return self._wait(serial, job, type(e).__name__, f"{type(e).__name__}: {e}")
            spool_placed(job.spool_id)
            p = (r or {}).get("preset") or {}
            log.info("auto applied %s %s spool %s: %s%s", serial, label, job.spool_id, p.get("name"),
                     "" if (r or {}).get("confirmed") else " (not confirmed yet)")


STORE = Store()


def default_store() -> Store:
    return STORE


def spool_placed(spool_id, *, keep_slot: tuple[str, str] | None = None) -> None:
    """Spool `spool_id` was loaded or given a named placement (D3, D12): every Auto waiting for it ends, and every
    named wait for it except the one of `keep_slot` (printer serial, slot label), on every printer. Takes the spool
    lock (re-entrant), so a send of that spool in progress elsewhere finishes first; hold no printer's slot lock."""
    default_store().cancel_for_spool(spool_id)
    pending.STORE.cancel_for_spool(spool_id, keep=keep_slot)


def load_named_klipper(kf, sm, serial: str, label: str, spool_id: int, dry: bool, **kw) -> dict:
    """Serialize a named Klipper write against Auto for this spool. A real load the printer has not confirmed yet is
    still the spool's newest placement (follow mode settles it), so it ends Auto and the named waits for the spool on
    every printer (D12); a load that raises (offline, printing, no such slot) leaves them."""
    store = default_store()
    with nullcontext() if dry else store.spool_lock(spool_id):
        result = kf.load(sm, serial, label, spool_id, dry, **kw)
        if not dry:
            spool_placed(spool_id)
        return result


def annotate(serial: str, st: dict | None, *, adapter: str | None = None, store: Store | None = None) -> dict | None:
    """Pipe a printer status through `Store.annotate` (using the default store unless `store` is given)."""
    return (store or default_store()).annotate(serial, st, adapter=adapter)
