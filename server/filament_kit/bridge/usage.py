"""Usage tracking: when a print ends, deduct the filament it used from the spools in its AMS slots.

Ported from the owner's H2D usage tracker (`h2d-filament-scanner/usage/usage_tracker.py`, proven on hardware).

Lifecycle (`Tracker`, fed by `Manager.add_listener`): while a printer is RUNNING / PAUSE / PREPARE / SLICING the
job is remembered (`subtask_name`, plate, `mapping`); when it turns FINISH or FAILED it is settled
once. A job start drops the previous job's `mapping` / `plate_idx` from the merged report (`status.merge_report`),
so a job settles with its own mapping or none (`no_mapping`, nothing written). A FINISH/FAILED already shown by the first report after a (re)connect is never settled (it ended while
nobody watched, and may have been settled before a restart); a server restarted mid-print picks the job up from
that first RUNNING report and settles it when it ends. Exactly-once rests on that transition alone (the
watched state goes ACTIVE -> FINISH/FAILED once per job), not on `task_id`: LAN, SD-card and touchscreen
prints, and our calibration job, all report "0", and a reprint may reuse an id. Our own calibration job
(`fk-calib-*`) is never settled and logs nothing.

Settling (on a worker thread): the job's 3MF is fetched from the printer's SD card over FTPS by name,
`<subtask_name>_plate_<n>.gcode.3mf` then `<subtask_name>.gcode.3mf`, each in `/` then `/cache`, then a listed
file whose name is exactly one of those two, ignoring case, in an NLST of both (the printer has no MLSD and no
mtimes: never "the newest file", never a similar name).
`Metadata/slice_info.config` lists, per plate, `<filament id=N used_g=...>` with N the project filament number;
the printer's slot for it is `mapping[N - 1]` (a global slot id), which the slot map (`ams.load_map`) turns into a
label (A1, HT-A ...) (`decode`: the calibrated map's own numbers and the numbers prints taught it, then the
numbering scheme calibration proved, a label the printer had at the print's start; an uncalibrated map: the scheme
it has, else the model's first candidate scheme, for the log only), and the spool in that slot is the Spoolman
spool whose `location` is exactly "<printer name> – AMS <label>" (two non-archived spools there:
`ambiguous_spool`, nothing written). Grams = `used_g` × final `mc_percent` / 100 (a FINISH counts as 100 %, so
a late percent delta cannot under-count), rounded to 0.01 g; filaments with 0 g are skipped. The deduction is
`Spoolman.use(spool_id, grams)` (`PUT /spool/{id}/use`). A printer whose map is not `calibrated`, or is
`observe`, is in observe mode: entries are logged with `observe: true`, nothing is written.

Learning and checking (calibrated maps; ported from the scanner): the slots the printer showed when the print
started (`layout.build` of its first active report) are kept with the job. A number nothing decodes is learned
when exactly one loaded slot at the start holds the sliced filament (same `tray_info_idx`, or with no preset in the
slice the same type and colour): saved in the map (`SlotMap.learned`) and used from then on. A decoded slot that
held another filament than the slice says (another preset, or with none another type) is not deducted
(`slot_mismatch`); two prints in a row with such a slot make the map `suspect`: observe mode (from that print on)
until `mapping.LayoutWatch` re-maps the printer when it is idle (`mapping.request_remap`). A print whose decoded
slots all agreed resets the streak. Without a start layout (no AMS in the report) nothing is learned or checked.

Log: `state/printers/<serial>/usage/<yyyy-mm>.xml`, one XML file per month per printer (tag `usage`, through
`statexml`), written under a module lock and a file lock (`usage/.lock`, waited for at most LOCK_WAIT s), so the root CLI and the service do not collide. Each file
holds `entries` (a list of entry records) and `undone` (a list of {entry, at}):
  entry:  {"id", "at", "job", "grams", "spool_id", "slot", "observe"[, "error"]} and the newer fields, in the month of
          its `at`
  undone: {"entry": <entry id>, "at"}, in the month of the entry; a failed credit takes the record back
Each append rewrites the month's file (atomic, mode 600, owned by the state owner). The old `usage.jsonl` of the release
before (one JSON object per line: entries, {"undo": id, "at"} and {"undo_failed": id, "at"}) is taken into the month
files; `usage/.migrated` holds how many bytes (and their sha256) were taken in, and a read takes in what the file holds
beyond that (what the release before wrote after a rollback), merging entries by id. A file shorter than that, or whose
first bytes differ, is read from its start again. For one release every entry and undo record is also appended to it in
the old form (and counted taken in), so a rollback finds the current history. When usage/.lock stays busy for
LOCK_WAIT s, the record is only appended to `usage/.pending-old` (one line each, nothing rewritten, so the holder cannot
lose it); reads count it as in force, and the next locked write puts it into the month files and appends it to usage.jsonl
before its own line, then drops the file and the note.

Entry shape returned by `entries` / `undo` (what `GET /api/printers/{id}/usage` and `POST /api/usage/{id}/undo`
serve):
  id        32 hex chars (unique across printers)
  at        ISO-8601 UTC, "2026-11-01T22:59:08Z"
  job       the print's subtask_name
  grams     float grams deducted (or that would be, in observe mode); null when the job could not be read
  spool_id  Spoolman spool id, or null (no spool in that slot / unknown)
  slot      slot label ("A2", "HT-A", "Ext"), or null (no mapping for that filament / job unreadable)
  observe   true: logged only, nothing written to Spoolman
  undone    true once undone
  error     only when set: "file_not_found" | "no_slice_info" | "plate_not_found" | "printer_unreachable" |
            "cert_changed" | "no_mapping" (the job's reports never carried `mapping`) (job-level, one entry,
            nothing written) | "spoolman_unreachable" | "spoolman_failed" | "ambiguous_spool" |
            "unmapped_slot" (calibrated map, but nothing decodes its slot id) | "slot_mismatch" (the decoded
            slot held another filament at the print's start) (that filament's write did not happen)
An entry was written to Spoolman iff observe is false, spool_id is set, grams > 0 and there is no error; only
those can be undone.

Exceptions (each has `.code` and `.status` for the API to map):
  UsageNotFound  404 not_found        unknown entry id / malformed serial or id
  TooOld         409 too_old          the entry is older than 7 days
  AlreadyUndone  409 already_undone   undone before
  NothingToUndo  409 nothing_to_undo  the entry wrote nothing (observe, no spool, error)
  SpoolChanged   409 spool_changed    that spool id is another spool now, or the spool is gone
A Spoolman failure during undo raises `SpoolmanError` and leaves the entry not undone."""
from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import logging
import math
import os
import re
import threading
import time
import uuid
import zipfile
from concurrent.futures import Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from defusedxml import ElementTree as SafeET

from .. import config, event_sink
from ..pairing import _write_atomic, give_to_state_owner, make_state_dir, open_lock
from ..profiles import statexml
from ..spoolman import SpoolmanError
from . import ams, ftps, hardware, layout, mapping, registry, status, tlspin

try:
    import fcntl
except ImportError:  # Windows dev PCs: no cross-process lock (the service only runs on Linux)
    fcntl = None

log = logging.getLogger(__name__)

FILE = "usage.jsonl"            # the log of the release before: its new lines are taken in, it is only appended to
DIR = "usage"
MIGRATED = ".migrated"           # says how many bytes of usage.jsonl were taken in
TAKEN = re.compile(r"usage\.jsonl taken in: (\d+) ([0-9a-f]{64})\n")
LOCK_WAIT = 5.0                 # seconds to wait for usage/.lock
PENDING_LOCK = ".pending.lock"  # guards the pending file's open and write against the drain's rename
TAIL = 256 * 1024               # how much of the end of usage.jsonl is looked at for lines a drain already appended
PENDING = ".pending-old"        # lines for usage.jsonl not appended yet (the lock was busy), in its form, one per line
DRAINING = ".pending-old.draining"      # the pending lines being appended (kept until all of them are in usage.jsonl)
MONTH_FILE = re.compile(r"\d{4}-(?:0[1-9]|1[0-2])\.xml")
TAG = "usage"
UNDO_DAYS = 7
ACTIVE = {'RUNNING', 'PAUSE', 'PREPARE', 'SLICING'}
TERMINAL = {'FINISH', 'FAILED'}
DIRS = ('/', '/cache')
CALIB_PREFIX = 'fk-calib-'      # our calibration job (calib3mf): not a print, its file is deleted afterwards
CALIB_PREFIXES = (CALIB_PREFIX, 'sw-calib-')     # the same job under the new name is skipped too
MAX_SLICE_INFO = 4 * 1024 * 1024
MAX_3MF = 512 * 1024 * 1024        # a sliced job is tens of MB; more is not read into memory
MAX_USED_G = 5000.0        # more than this on one plate is a damaged or hostile file, not a print
ENTRY_ID = re.compile(r"[0-9a-f]{32}")
OUTCOMES = ('finished', 'failed', 'cancelled', 'unknown')
FIELDS = ('id', 'at', 'job', 'grams', 'spool_id', 'slot', 'observe',
          'material', 'vendor', 'name', 'color_hex', 'printer_name', 'outcome')

_lock = threading.RLock()       # re-entrant: a reader migrates the old log under it
_held: set[str] = set()         # printers whose usage/.lock this process holds (guarded by _lock)
_reported: set[str] = set()     # files a problem was logged for
_undoing: set[str] = set()      # entries an Undo is working on (guarded by _lock)
_pend_lock = threading.Lock()   # with usage/.pending.lock: held only for one appended pending line or one rename, never while waiting for another lock
_lost: set[str] = set()         # rollback copies a record was lost from (it could be neither appended nor kept pending): noted until a restart


class UsageError(Exception):
    code = 'usage_error'
    status = 400


class UsageNotFound(UsageError):
    code, status = 'not_found', 404


class TooOld(UsageError):
    code, status = 'too_old', 409


class AlreadyUndone(UsageError):
    code, status = 'already_undone', 409


class NothingToUndo(UsageError):
    code, status = 'nothing_to_undo', 409


class SpoolChanged(UsageError):
    code, status = 'spool_changed', 409


def _registered(spool) -> float | None:
    """A spool's `registered` time from Spoolman, in epoch seconds, or None when it can't be read."""
    text = spool.get('registered') if isinstance(spool, dict) else None
    if not isinstance(text, str):
        return None
    try:
        when = dt.datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError:
        return None
    if when.tzinfo is None:         # Spoolman's times are UTC
        when = when.replace(tzinfo=dt.timezone.utc)
    return when.timestamp()


def _same_spool(spoolman, rec: dict, at: float) -> None:
    """Spoolman's spool ids are reused: delete the newest spool, add one, and it has the old id. An Undo then would take
    grams off a spool that never fed that print. So the spool must have been there when the print ended. When that can't be
    shown, nothing is written: the guard exists to stop a wrong write."""
    entry = rec['id']
    try:
        spool = spoolman.get(f"spool/{int(rec['spool_id'])}")
    except SpoolmanError as e:
        if e.status == 404:
            raise SpoolChanged(f"usage entry {entry}: its spool is gone from Spoolman; nothing was changed") from None
        raise
    registered = _registered(spool)
    if registered is None:
        raise SpoolChanged(f"usage entry {entry}: can't tell when its spool was added; nothing was changed")
    if registered > at:
        raise SpoolChanged(f"usage entry {entry}: that spool was added after this print, so the original spool is gone; "
                           "nothing was changed")


def _now() -> float:
    return time.time()


def _iso(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def _epoch(s: Any) -> float | None:
    try:
        return dt.datetime.strptime(str(s), '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=dt.timezone.utc).timestamp()
    except ValueError:
        return None


# ---------------------------------------------------------------- the log

def _dir(serial: str) -> Path:
    if not isinstance(serial, str) or not registry.SERIAL.fullmatch(serial):
        raise UsageNotFound(f"no printer {serial!r}")
    return config.paths().state / "printers" / serial


def append(serial: str, entry: dict) -> dict:
    """Add an entry (id and at are filled in) to the printer's log; returns the stored line."""
    rec = {'id': uuid.uuid4().hex, 'at': _iso(_now()), **entry}
    _write_line(serial, rec)            # takes the locks itself, the wait for the file lock is made without the module lock
    return rec


def _month_files(serial: str, since: str | None = None) -> list[Path]:
    """The printer's month files, oldest first; `since` ("2026-11") leaves out the months before it."""
    folder = _dir(serial) / DIR
    try:
        names = sorted(n for n in os.listdir(folder) if MONTH_FILE.fullmatch(n))
    except OSError:
        return []
    return [folder / n for n in names if since is None or n[:7] >= since]


def _report(path: Path, reason: str) -> None:
    """Note a file that could not be read or moved (statexml.problems(), the Checks row) and log it once."""
    statexml.note_problem(path, reason)
    if str(path) not in _reported:
        _reported.add(str(path))
        log.warning("usage: %s: %s", path, reason)


def _forget(path: Path) -> None:
    statexml.clear_problem(path)
    _reported.discard(str(path))


class LockTimeout(OSError):
    """usage/.lock stayed with another process for LOCK_WAIT seconds."""


@contextmanager
def _flocked(serial: str):
    """usage/.lock (flock) so the root CLI and the service do not move or write at once, then the module lock for the body.
    The wait for the file lock is bounded (LockTimeout) and made without the module lock, so a stopped holder does not
    hold up the other printers' writes (a caller that already holds the module lock keeps it while it waits)."""
    with _lock:
        if fcntl is None or serial in _held:        # nothing to take, or this thread has it (the module lock is held)
            yield
            return
    folder = _dir(serial) / DIR
    make_state_dir(folder)
    with open_lock(folder / ".lock") as fh:
        deadline = time.monotonic() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"{folder / '.lock'} is held by another process") from None
                time.sleep(0.05)
        try:
            with _lock:
                _held.add(serial)
                try:
                    yield
                finally:
                    _held.discard(serial)
        finally:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _load(path: Path, for_write: bool = False) -> dict:
    """{'entries': [...], 'undone': [...]} of a month file. One that parses as broken is logged once and noted
    (statexml.problems()) and read as empty; before the next write it is kept as `<name>.unreadable` (`.1` ... `.4` when
    older copies exist: none is replaced), never overwritten, and the problem stays noted under that name. A read that
    fails (permission, I/O) is only noted and reads as empty: the file is a good one, it stays, and a write that needs
    it fails instead of overwriting it."""
    empty = {'entries': [], 'undone': []}
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return empty
    except OSError as e:                # a passing read error: the file may be good, so it is not set aside
        reason = f"cannot read: {e.strerror or e}"
        _report(path, reason)
        if for_write:
            raise OSError(f"cannot read {path.name} ({reason}); not written over it") from e
        return empty
    try:
        data = statexml.loads(raw, TAG)
    except statexml.StateXmlError as e:
        reason = str(e)
    else:
        _forget(path)
        return {k: [x for x in data[k] if isinstance(x, dict)] if isinstance(data.get(k), list) else []
                for k in ('entries', 'undone')}
    _report(path, reason)
    if for_write and path.exists():
        aside = _set_aside(path)
        if aside is None:
            raise OSError(f"cannot keep the damaged {path.name} aside ({reason})")
        _forget(path)
        _report(aside, f"damaged ({reason}); kept aside, its entries are not shown")
    return empty


def _set_aside(path: Path) -> Path | None:
    """Move a damaged file to `<name>.unreadable`, or `.unreadable.1` ... `.4` when that name is taken (an older copy is
    never replaced). The new name, or None when it could not be moved or five copies are there already."""
    for n in range(5):
        dest = path.with_name(path.name + ".unreadable" + (f".{n}" if n else ""))
        if dest.exists():
            continue
        try:
            os.replace(path, dest)
        except OSError:
            return None
        return dest
    return None


def _save(path: Path, data: dict) -> None:
    make_state_dir(path.parent)
    _write_atomic(path, statexml.dumps(TAG, data), 0o600, state_owned=True)


def _read_old(serial: str) -> bytes | None:
    """The bytes of the old usage.jsonl, or None when there is none (or it cannot be read: noted)."""
    path = _dir(serial) / FILE
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as e:
        _report(path, f"cannot read: {e.strerror or e}; its entries are not shown")
        return None


def _parse_old(raw: bytes) -> tuple[list[dict], dict[str, str], list[tuple[str, str, Any]]]:
    """(entries, {undone entry id: undo time}, [(kind, entry id, time) of each undo / undo_failed line in order]) of lines
    of the old usage.jsonl."""
    out, undone, ops = [], {}, []
    for line in raw.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:              # a torn last line after a crash, or a byte that is not UTF-8
            continue
        if not isinstance(rec, dict):
            continue
        if 'undo' in rec:
            undone.setdefault(str(rec['undo']), rec.get('at'))
            ops.append(('undo', str(rec['undo']), rec.get('at')))
        elif 'undo_failed' in rec:
            undone.pop(str(rec['undo_failed']), None)
            ops.append(('undo_failed', str(rec['undo_failed']), rec.get('at')))
        elif isinstance(rec.get('id'), str):
            out.append(rec)
    return out, undone, ops


def _month_of(at: Any) -> str | None:
    return at[:7] if isinstance(at, str) and re.fullmatch(r"\d{4}-\d{2}", at[:7]) else None


def _taken(serial: str) -> tuple[int, str | None] | None:
    """(bytes of usage.jsonl taken in, their sha256) from the marker; None without a marker. A marker of the earlier
    form (no numbers) counts as nothing taken in."""
    try:
        text = (_dir(serial) / DIR / MIGRATED).read_text('ascii', errors='replace')
    except OSError:
        return None
    m = TAKEN.fullmatch(text)
    return (int(m[1]), m[2]) if m else (0, None)


def _write_marker(serial: str, raw: bytes) -> None:
    """Mark the first len(raw) bytes of usage.jsonl as taken in (with their hash, to tell a rewritten file)."""
    make_state_dir(_dir(serial) / DIR)
    _write_atomic(_dir(serial) / DIR / MIGRATED,
                  f"usage.jsonl taken in: {len(raw)} {hashlib.sha256(raw).hexdigest()}\n".encode('ascii'),
                  0o600, state_owned=True)


def _start(raw: bytes, taken: tuple[int, str | None] | None) -> int:
    """Where in `raw` the lines not yet taken in begin. A file shorter than what was taken in, or whose first bytes are
    not the ones taken in, was truncated or rewritten: it is read from its start again (entries are merged by id, so
    none is counted twice; an undo is only added and an undo_failed only takes back what the file says)."""
    if taken is None or taken[1] is None:
        return 0
    off, digest = taken
    if off > len(raw) or hashlib.sha256(raw[:off]).hexdigest() != digest:
        return 0
    return off


def _migrate(serial: str) -> tuple[list[dict], dict[str, str]] | None:
    """Take what the old usage.jsonl holds beyond what was taken in into the month files (the file stays as it was): the
    first time its whole content, later what the release before wrote after a rollback. None when there was nothing to
    do or it was moved; the not yet taken lines' (entries, undone) when the move failed (read from memory until it works)."""
    d = _dir(serial)
    try:
        size = (d / FILE).stat().st_size
    except OSError:
        return None
    taken = _taken(serial)
    if taken is not None and taken[0] == size:          # steady state: no lock, no read
        return None
    try:
        with _flocked(serial):
            return _move(serial)
    except OSError as e:                # the lock could not be taken (a read-only reader): read it from memory
        _report(d / FILE, f"could not be converted ({e}); reading it until it can")
        raw = _read_old(serial)
        if raw is None:
            return None
        return _parse_old(raw[_start(raw, taken):])[:2]


def _move(serial: str) -> tuple[list[dict], dict[str, str]] | None:
    d = _dir(serial)
    raw = _read_old(serial)
    if raw is None:
        return None
    taken = _taken(serial)
    start = _start(raw, taken)
    if taken is not None and taken[1] is not None and start == len(raw):
        return None
    entries, undone, ops = _parse_old(raw[start:])
    month_of = {e['id']: _entry_month(e) for e in entries}
    fresh: dict[str, list[dict]] = {}
    for e in entries:
        fresh.setdefault(month_of[e['id']] or _iso(_now())[:7], []).append(e)
    try:
        for m, add in sorted(fresh.items()):          # idempotent: a stop half way is finished by the next run
            path = d / DIR / f"{m}.xml"
            data = _load(path, True)
            have = {e.get('id') for e in data['entries']}
            data['entries'] += [e for e in add if e['id'] not in have]
            _save(path, data)
        for kind, eid, at in ops:
            _mark(serial, eid, at, kind == 'undo')
        _write_marker(serial, raw)
    except (OSError, statexml.StateXmlError, TypeError, ValueError) as e:
        _report(d / FILE, f"could not be converted ({e}); reading it until it can")
        return entries, undone
    _forget(d / FILE)
    return None


def _write_line(serial: str, rec: dict) -> None:
    """Store an entry, or an {"undo": id, "at"} / {"undo_failed": id, "at"} record, in its month's file, and (for one
    release) as a line of the old usage.jsonl, so a rollback finds the history current."""
    try:
        with _flocked(serial):
            drained = _drain(serial)            # lines an earlier write could not append go first, in their order
            _store(serial, rec)
            if not (drained and _old_form(serial, rec)):
                try:
                    _pend(serial, rec)          # a line must not pass the ones before it
                except OSError as e:            # the XML has it: only the rollback copy is lost, and that stays noted
                    _lost.add(str(_copy_key(serial)))
                    _report(_copy_key(serial), f"cannot be kept current for a rollback: {e}")
    except LockTimeout as e:            # another process holds usage/.lock: the record waits in the pending file
        _note_pending(serial, str(e))
        _pend(serial, rec)


def _note_pending(serial: str, why: str) -> None:
    """The weaker note (kept pending, will be appended): a record lost from the rollback copy has the stronger one, which stays."""
    if str(_copy_key(serial)) not in _lost:
        _report(_copy_key(serial), f"not kept current for a rollback yet: {why}")


def _copy_key(serial: str) -> Path:
    """What a failed rollback copy is noted under (the move of usage.jsonl has its own note, under usage.jsonl)."""
    return _dir(serial) / (FILE + " (rollback copy)")


def _line(rec: dict) -> bytes:
    return (json.dumps(rec, separators=(',', ':')) + "\n").encode('utf-8')


@contextmanager
def _pend_guard(serial: str):
    """A short lock of its own (not usage/.lock, which can be held for long) around the pending file's open, write and
    close, and the drain's rename of it: a line cannot land in the file a drain has just renamed, read and removed."""
    folder = _dir(serial) / DIR
    make_state_dir(folder)
    with _pend_lock:
        if fcntl is None:
            yield
            return
        with open_lock(folder / PENDING_LOCK) as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)         # held for a rename or one line only
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _pend(serial: str, rec: dict) -> None:
    """Keep `rec` for usage.jsonl until a locked write can append it: one appended, fsynced line (it survives a restart,
    and nothing is read and rewritten, so a process that holds the lock cannot lose it). The month file is not touched
    here (that is a read and a rewrite). Raises OSError, so the caller's write fails as a failed XML write does."""
    d = _dir(serial) / DIR
    make_state_dir(d)
    f = d / PENDING
    with _pend_guard(serial):
        new = not f.exists()
        fd = os.open(f, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_BINARY', 0), 0o600)
        try:
            if new:
                give_to_state_owner(f)
            os.write(fd, _line(rec))
            os.fsync(fd)
        finally:
            os.close(fd)


def _pending(serial: str) -> list[dict]:
    """The records waiting for usage.jsonl, oldest first: the ones being appended, then the ones added since. (The first
    file is read first: a drain renames it into the second, so a record is seen in one of them at least.)"""
    got = []
    for name in (PENDING, DRAINING):
        try:
            got.append(_pending_in(_dir(serial) / DIR / name))
        except OSError:
            got.append([])
    return got[1] + got[0]


def _drain(serial: str) -> bool:
    """Under the lock: put the pending records into the month files (those not there yet) and append them to usage.jsonl
    in order, then drop the pending file and the note. False (file and note stay) when a line could not be appended."""
    d = _dir(serial) / DIR
    for _ in range(2):                      # a leftover of an earlier stop first, then what came in since
        draining = d / DRAINING
        if not draining.exists():
            if not (d / PENDING).exists():
                break
            try:
                with _pend_guard(serial):
                    os.replace(d / PENDING, draining)   # a line added from now on goes to a new file
            except FileNotFoundError:
                break
        recs = _pending_in(draining)
        for rec in recs:
            _store(serial, rec, dedupe=True)
        for rec in recs[_appended(serial, recs):]:      # in order, and none after one that failed
            if not _old_form(serial, rec):
                return False
        draining.unlink()
    _clear_note(serial)
    return True


def _appended(serial: str, recs: list[dict]) -> int:
    """How many of the first of `recs` the end of usage.jsonl already holds as its last lines (a drain that stopped after
    some appends, or a line whose append was reported failed after it was written): those are not appended again."""
    try:
        with open(_dir(serial) / FILE, 'rb') as fh:
            size = fh.seek(0, os.SEEK_END)
            fh.seek(max(0, size - TAIL))
            raw = fh.read()
    except OSError:
        return 0
    if size > TAIL:
        raw = raw.partition(b"\n")[2]               # the first line may be cut
    lines = []
    for line in raw.splitlines():
        try:
            lines.append(json.loads(line))
        except ValueError:
            continue
    for k in range(min(len(recs), len(lines)), 0, -1):
        if lines[-k:] == recs[:k]:
            return k
    return 0


def _clear_note(serial: str) -> None:
    """Drop the rollback-copy note when nothing is waiting for usage.jsonl and no record was lost from it."""
    if str(_copy_key(serial)) not in _lost and not _pending(serial):
        _forget(_copy_key(serial))


def _sync_note(serial: str) -> None:
    """The note follows the pending file, which outlives a restart (the note itself is only in memory)."""
    key = _copy_key(serial)
    waiting = _pending(serial)
    if not waiting:
        if str(key) not in _lost:
            _forget(key)
    elif not any(p.startswith(f"{key} (") for p in statexml.problems()):
        _report(key, f"not kept current for a rollback yet: {len(waiting)} record(s) wait to be appended to usage.jsonl")


def _pending_in(f: Path) -> list[dict]:
    out = []
    for line in f.read_bytes().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:                  # a torn last line
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def _old_form(serial: str, rec: dict) -> bool:
    """Append `rec` to usage.jsonl as the release before wrote it (one JSON line, fsynced, mode 600), then mark it taken
    in (the marker is written after the line, so a lost marker only makes the next read see the line again). False when
    it could not be appended (noted). A good append clears the note only when no pending line is left (`_drain` clears
    it when the last one is in)."""
    f = _dir(serial) / FILE
    try:
        line = _line(rec)
        taken = _taken(serial)
        new = not f.exists()
        fd = os.open(f, os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, 'O_BINARY', 0), 0o600)
        try:
            if new:
                give_to_state_owner(f)
            before = os.fstat(fd).st_size
            os.write(fd, line)
            os.fsync(fd)
        finally:
            os.close(fd)
        if before == 0 or (taken is not None and taken[0] == before):       # nothing was waiting to be taken in
            raw = f.read_bytes()
            if len(raw) == before + len(line):
                _write_marker(serial, raw)
                _clear_note(serial)
    except (OSError, TypeError, ValueError) as e:       # the XML is the record: the old copy is for a rollback only
        _report(_copy_key(serial), f"cannot be kept current for a rollback: {e}")
        return False
    return True


def _mark(serial: str, eid: str, at: Any, undone: bool) -> None:
    """Add (undone) or take back an entry's undo record, in the month file of the entry (else of the undo time)."""
    d = _dir(serial)
    holder = None
    for path in reversed(_month_files(serial)):
        data = _load(path)
        if any(e.get('id') == eid for e in data['entries']) or any(u.get('entry') == eid for u in data['undone']):
            holder = path
            break
    if undone:
        path = holder or d / DIR / f"{_month_of(at) or _iso(_now())[:7]}.xml"
        data = _load(path, True)
        if not any(u.get('entry') == eid for u in data['undone']):
            data['undone'].append({'entry': eid, 'at': at})
            _save(path, data)
    elif holder is not None:                # takes the entry's undo back
        data = _load(holder, True)
        data['undone'] = [u for u in data['undone'] if u.get('entry') != eid]
        _save(holder, data)


def _store(serial: str, rec: dict, dedupe: bool = False) -> None:
    """Write `rec` to the month files; `dedupe`: an entry already there (a pending record stored before) is not added twice."""
    d = _dir(serial)
    make_state_dir(d / DIR)
    _migrate(serial)
    if 'undo' in rec or 'undo_failed' in rec:
        _mark(serial, str(rec['undo'] if 'undo' in rec else rec['undo_failed']), rec.get('at'), 'undo' in rec)
        return
    path = d / DIR / f"{_entry_month(rec) or _iso(_now())[:7]}.xml"
    data = _load(path, True)
    if dedupe and any(e.get('id') == rec.get('id') for e in data['entries']):
        return
    data['entries'].append(rec)
    _save(path, data)


def _read(serial: str, since: str | None = None) -> tuple[list[dict], dict[str, str]]:
    """(entry records oldest first, {undone entry id: undo time}) of the printer's month files from `since` ("2026-11")
    on (all of them when None)."""
    _sync_note(serial)
    old = _migrate(serial)
    out, undone = [], {}
    if old is not None:
        out, undone = old[0][:], dict(old[1])
    for path in _month_files(serial, since):
        data = _load(path)
        have = {e['id'] for e in out}          # a failed move may have written some months already
        out += [e for e in data['entries'] if isinstance(e.get('id'), str) and e['id'] not in have]
        for u in data['undone']:
            undone.setdefault(str(u.get('entry')), u.get('at'))
    for rec in _pending(serial):            # records the old log has not got yet (the lock was busy): already in force
        if 'undo' in rec:
            undone.setdefault(str(rec['undo']), rec.get('at'))
        elif 'undo_failed' in rec:
            undone.pop(str(rec['undo_failed']), None)
        elif isinstance(rec.get('id'), str) and all(e['id'] != rec['id'] for e in out):
            out.append(rec)
    return out, undone


def _view(rec: dict, undone: dict) -> dict:
    base = ('id', 'at', 'job', 'grams', 'spool_id', 'slot', 'observe')
    e = {k: rec.get(k) for k in base}
    e['observe'] = bool(e['observe'])
    e['undone'] = rec['id'] in undone
    for k in ('material', 'vendor', 'name', 'color_hex', 'printer_name'):
        if rec.get(k):
            e[k] = rec[k]
    if rec.get('outcome') in OUTCOMES:
        e['outcome'] = rec['outcome']
    if rec.get('error'):
        e['error'] = rec['error']
    return e


def _entry_month(e: dict) -> str | None:
    at = e.get('at')
    if isinstance(at, str) and len(at) >= 7 and at[4] == '-':
        return at[:7]
    ended = e.get('ended')
    if isinstance(ended, (int, float)):
        return dt.datetime.fromtimestamp(ended, dt.timezone.utc).strftime('%Y-%m')
    return None


def by_month(items: list[dict]) -> list[dict]:
    """Aggregate usage entries into monthly summaries, newest month first (undone entries excluded)."""
    buckets: dict[str, dict] = {}
    mats: dict[str, dict[str, float]] = {}
    for e in items:
        if e.get('undone'):
            continue
        m = _entry_month(e)
        if not m:
            continue
        b = buckets.setdefault(m, {'month': m, 'grams': 0.0, 'prints': 0, 'unfinished_prints': 0})
        b['prints'] += 1
        if e.get('outcome') in ('failed', 'cancelled', 'unknown'):
            b['unfinished_prints'] += 1
        g = e.get('grams')
        if isinstance(g, (int, float)) and not isinstance(g, bool) and g > 0:
            b['grams'] += float(g)
            mat = e.get('material')
            if isinstance(mat, str) and mat.strip():
                mm = mats.setdefault(m, {})
                mm[mat] = mm.get(mat, 0.0) + float(g)
    out = []
    for m in sorted(buckets, reverse=True):
        row = buckets[m]
        row['grams'] = round(row['grams'], 2)
        mm = mats.get(m)
        if mm:
            row['top_material'] = max(mm.items(), key=lambda kv: (kv[1], kv[0]))[0]
        out.append(row)
    return out


def entries(serial: str, days: float = UNDO_DAYS, *, by: str | None = None) -> list[dict] | dict:
    """The printer's entries of the last `days` days, newest first (or `{months: [...]}` when `by='month'`).
    UsageNotFound for a malformed serial; a printer without a log has none."""
    since = _now() - max(0.0, float(days)) * 86400
    recs, undone = _read(serial, _iso(since)[:7] if since > 0 else None)
    out = [_view(r, undone) for r in recs if (_epoch(r.get('at')) or 0) >= since]
    out.reverse()
    if by == 'month':
        return {'months': by_month(out)}
    if by is not None:
        raise UsageError(f"unsupported grouping {by!r}")
    return out


def _written(rec: dict) -> bool:
    g = rec.get('grams')
    return (not rec.get('observe') and rec.get('spool_id') is not None and not rec.get('error')
            and isinstance(g, (int, float)) and g > 0)


def undo(entry_id: str, spoolman) -> dict:
    """Give an entry's grams back to its spool (`use_weight` negative) once, within 7 days; returns the entry.
    The undo line is written before the spool is credited, so a failed write credits nothing and a second Undo
    cannot credit twice; a failed credit takes the line back. Spoolman is called outside the module lock, and
    `_undoing` keeps a second Undo of the same entry out meanwhile."""
    if not isinstance(entry_id, str) or not ENTRY_ID.fullmatch(entry_id):
        raise UsageNotFound(f"no usage entry {entry_id!r}")
    root = config.paths().state / "printers"
    with _lock:
        found = None
        for d in sorted(root.iterdir()) if root.is_dir() else []:
            if not d.is_dir() or not registry.SERIAL.fullmatch(d.name):
                continue
            recs, undone = _read(d.name)
            rec = next((r for r in recs if r['id'] == entry_id), None)
            if rec is None:
                continue
            if entry_id in undone or entry_id in _undoing:
                raise AlreadyUndone(f"usage entry {entry_id} was undone already")
            at = _epoch(rec.get('at'))
            if at is None or _now() - at > UNDO_DAYS * 86400:
                raise TooOld(f"usage entry {entry_id} is older than {UNDO_DAYS} days")
            if not _written(rec):
                raise NothingToUndo(f"usage entry {entry_id} wrote nothing to Spoolman")
            found = d.name, rec, at, undone
            _undoing.add(entry_id)
            break
    if found is None:
        raise UsageNotFound(f"no usage entry {entry_id}")
    serial, rec, at, undone = found
    try:
        _same_spool(spoolman, rec, at)
        _write_line(serial, {'undo': entry_id, 'at': _iso(_now())})
        try:
            spoolman.use(int(rec['spool_id']), -float(rec['grams']))
        except BaseException:
            try:
                _write_line(serial, {'undo_failed': entry_id, 'at': _iso(_now())})
            except OSError as e:
                log.error("usage: printer %s: undo of %s failed and could not be taken back: %s", serial, entry_id, e)
            raise
    finally:
        with _lock:
            _undoing.discard(entry_id)
    log.info("usage: printer %s: undid %s g on spool %s (%s)", serial, rec['grams'], rec['spool_id'], rec.get('job'))
    undone[entry_id] = _iso(_now())
    p = registry.load().get(serial)
    pname = rec.get('printer_name') or (registry.display_name(p) if p else serial)
    event_sink.emit("usage.undone", {"type": "printer", "id": serial, "name": pname},
                    {"printer": serial, "job": rec.get('job'), "spool": rec.get('spool_id'),
                     "grams": rec.get('grams'), "entry": entry_id})
    return _view(rec, undone)


# ---------------------------------------------------------------- the job's 3MF

def _plate_of(report: dict) -> str | None:
    p = status._int(report.get('plate_idx'))
    if p is not None and p > 0:
        return str(p)
    m = re.search(r'plate[_-]?(\d+)', str(report.get('gcode_file') or ''), re.I)
    return m.group(1) if m else None


def candidate_paths(subtask: str, plate: str | None) -> list[str]:
    stems = ([f"{subtask}_plate_{plate}"] if plate else []) + [subtask]
    return [f"{d.rstrip('/')}/{stem}.gcode.3mf" for stem in stems for d in DIRS]


def nlst_match(names: list[str], subtask: str, plate: str | None) -> str | None:
    """The listed 3MF named exactly `<subtask>_plate_<n>.gcode.3mf` or `<subtask>.gcode.3mf` ignoring case (that
    order), `/` before `/cache`. Only case differences are forgiven: a similar name is another job."""
    wanted = ([f"{subtask}_plate_{plate}.gcode.3mf".casefold()] if plate else []) +         [f"{subtask}.gcode.3mf".casefold()]
    for w in wanted:
        hits = sorted((n for n in names if n.rsplit('/', 1)[-1].casefold() == w),
                      key=lambda n: n.startswith('/cache'))
        if hits:
            return hits[0]
    return None


def fetch_3mf(printer: registry.Printer, subtask: str, plate: str | None) -> tuple[bytes | None, str | None]:
    """(bytes, path) of the job's 3MF on the printer, or (None, None). Raises tlspin.CertChanged, and
    ftps.FtpsError when the printer cannot be reached."""
    with ftps.Client(printer) as c:
        for p in candidate_paths(subtask, plate):
            try:
                data = c.download(p, MAX_3MF)
            except ftps.FtpsError:
                continue
            if data:
                return data, p
        names = []
        for d in DIRS:
            try:
                listed = c.nlst(d)
            except ftps.FtpsError as e:
                log.info("usage: printer %s: cannot list %s: %s", printer.serial, d, e)
                continue
            for n in listed:
                names.append(n if n.startswith('/') else f"{d.rstrip('/')}/{n}")
        p = nlst_match(names, subtask, plate)
        if p:
            try:
                data = c.download(p, MAX_3MF)
            except ftps.FtpsError as e:
                log.info("usage: printer %s: cannot download %s: %s", printer.serial, p, e)
                return None, None
            if data:
                return data, p
    return None, None


def parse_filaments(data: bytes) -> dict[str | None, list[dict]]:
    """{plate index: [{id, used_g, type, color, idx}]} from the 3MF's Metadata/slice_info.config; {} when there is
    none or it cannot be read."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        name = next((n for n in z.namelist() if n.lower().endswith('slice_info.config')), None)
        if name is None or z.getinfo(name).file_size > MAX_SLICE_INFO:
            return {}
        root = SafeET.fromstring(z.read(name))
    except Exception:  # noqa: BLE001 - a damaged zip raises zlib.error, EOFError, RuntimeError ...; none may escape
        return {}
    plates = root.findall('plate') or [root]
    out: dict[str | None, list[dict]] = {}
    for pl in plates:
        index = next((m.get('value') for m in pl.findall('metadata') if m.get('key') == 'index'), None)
        fils = []
        for f in pl.findall('filament'):
            fid = status._int(f.get('id'))
            try:
                g = float(f.get('used_g') or 0)
            except ValueError:
                g = 0.0
            if not math.isfinite(g) or g > MAX_USED_G:
                g = 0.0                 # "nan", "inf" or absurd: counted as nothing used
            if fid is None:
                continue
            fils.append({'id': fid, 'used_g': g, 'type': f.get('type'), 'color': f.get('color'),
                         'idx': f.get('tray_info_idx')})
        out[index] = fils
    return out


# ---------------------------------------------------------------- settling

def _guess_label(g: int, model=None) -> str | None:
    """Uncalibrated (observe mode, for the log only): the inverse of `ams.default_global`: the number decoded
    with the model's first numbering scheme, labelled as `layout` labels that slot. An external id the model
    does not have (e.g. 255 on a single-holder printer) is None."""
    hw = hardware.load()
    mi = hw.model(model)
    try:
        dec = hw.scheme(mi.numbering[0]).decode(g)
    except KeyError:
        dec = None
    if dec is None:
        return None
    a, t = dec
    if a in layout.EXT_DEFAULT and not any(x.id == a for x in mi.externals):
        return None
    return layout.label_for(a, t, mi, hw)


def start_slots(serial: str, report: dict) -> dict[str, dict]:
    """label -> {ams_id, tray_id, type, idx, color, present} of every slot in the report (the print's start); {}
    when the report shows no AMS (nothing to learn from or check against)."""
    try:
        if not isinstance(status._d(status._d(report).get('ams')).get('ams'), list):
            return {}
        out = {}
        for u in layout.build(report, ams.printer_model(serial)).units:
            for sl in u.slots:
                t = sl.raw if isinstance(sl.raw, dict) else {}
                typ = status._s(t.get('tray_type'))
                out[sl.label] = {'ams_id': u.ams_id, 'tray_id': sl.tray_id, 'type': typ,
                                 'idx': status._s(t.get('tray_info_idx')),
                                 'color': status._s(t.get('tray_color')).upper()[:6],
                                 'present': bool(typ) if sl.present is None else bool(sl.present and typ)}
        return out
    except Exception:  # noqa: BLE001 - an odd report: no start layout, never a lost job
        log.exception("usage: printer %s: cannot read the slots at the print's start", serial)
        return {}


def _same_type(a, b) -> bool:
    return bool(a) and str(a).strip().upper() == str(b or '').strip().upper()


def _holds(slot: dict | None, f: dict) -> bool:
    """The slot (at the print's start) held the sliced filament, the scanner's rule: same preset, or (no preset
    sliced, or another one) the same type. The presence bit is not asked: a slot reporting a type counts."""
    if not slot:
        return False
    return bool(f.get('idx')) and slot.get('idx') == f.get('idx') or _same_type(slot.get('type'), f.get('type'))


def _matches_unseen(slot: dict, f: dict) -> bool:
    """The learn rule's match: the same preset, or with no preset in the slice the same type and colour."""
    if not slot.get('present'):
        return False
    if f.get('idx'):
        return slot.get('idx') == f['idx']
    return _same_type(slot.get('type'), f.get('type')) and \
        slot.get('color') == str(f.get('color') or '').lstrip('#')[:6].upper()


def decode(g: int, smap: ams.SlotMap, model=None, start: dict | None = None,
           hw: hardware.Hardware | None = None) -> tuple[str | None, str]:
    """A printer slot number -> (label or None, how). Calibrated: the map's numbers ("map"), then numbers prints
    taught it ("learned"), then the scheme calibration proved ("scheme": only a label the printer had at the
    print's start, or with no start layout one the map lists, and never one the map gives another number).
    Uncalibrated: the map's scheme if it has one, else the model's first candidate ("model": for the log only)."""
    hw = hw or hardware.load()
    mi = hw.model(model)
    if g is None or g < 0:
        return None, 'unused'
    if smap.calibrated:
        for label, sl in smap.slots.items():
            if isinstance(sl, dict) and status._int(sl.get('global_id')) == g:
                return label, 'map'
        if str(g) in smap.learned:
            return smap.learned[str(g)], 'learned'
    if smap.scheme:
        if g in layout.EXT_DEFAULT or any(x.id == g for x in mi.externals):
            dec = (g, 0)
        else:
            try:
                dec = hw.scheme(smap.scheme).decode(g)
            except KeyError:
                dec = None
        label = None
        if dec is not None and start:       # the label the printer gave that slot at the print's start
            label = next((lab for lab, sl in start.items() if (sl.get('ams_id'), sl.get('tray_id')) == dec), None)
        elif dec is not None:               # no start layout: only a label the map lists for that slot
            lab = layout.label_for(*dec, mi, hw)
            sl = smap.slots.get(lab) if lab else None
            if isinstance(sl, dict) and (status._int(sl.get('ams_id')), status._int(sl.get('tray_id'))) == dec:
                label = lab
        if label is not None:
            mine = smap.slots.get(label)
            other = isinstance(mine, dict) and status._int(mine.get('global_id')) not in (None, g)
            if not (smap.calibrated and other):
                return label, 'scheme'
    if smap.calibrated:
        return None, 'unknown'
    if start:                               # the model's first candidate, labelled as the printer showed it
        try:
            dec = (g, 0) if g in layout.EXT_DEFAULT else hw.scheme(mi.numbering[0]).decode(g)
        except (KeyError, IndexError):
            dec = None
        hit = next((lab for lab, sl in start.items() if (sl.get('ams_id'), sl.get('tray_id')) == dec), None)
        if hit is not None:
            return hit, 'model'
    return _guess_label(g, mi), 'model'


def _save_map(serial: str, smap: ams.SlotMap, stamp) -> None:
    """Save what a print taught the map, unless the map changed since it was read (a mapping job saved a new
    one: that one wins)."""
    try:
        if not ams.save_map_if(serial, smap, stamp):
            log.info("usage: printer %s: the slot map changed meanwhile; this print's learning is dropped", serial)
    except OSError:
        log.exception("usage: printer %s: cannot save the slot map", serial)


class _Watch:
    def __init__(self):
        self.prev: str | None = None
        self.job: dict | None = None


class Tracker:
    """Watches every printer of `manager` and settles each print it saw end (see the module docstring)."""

    def __init__(self, manager, spoolman):
        self._spoolman = spoolman
        self._lock = threading.Lock()
        self._watch: dict[str, _Watch] = {}
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='usage')
        self._futures: list[Future] = []
        self._stopped = False
        manager.add_listener(self._on_report)

    def stop(self) -> None:
        self._stopped = True
        self._pool.shutdown(wait=False)

    def flush(self, timeout: float) -> None:
        """Wait up to `timeout` s for the settles queued so far (tests, shutdown)."""
        with self._lock:
            fs = list(self._futures)
        wait(fs, timeout)

    def _on_report(self, serial: str, report: dict, first: bool) -> None:
        if self._stopped:
            return
        gs = report.get('gcode_state')
        if not isinstance(gs, str):
            return
        with self._lock:
            w = self._watch.setdefault(serial, _Watch())
            prev, w.prev = w.prev, gs
            if gs in ACTIVE:
                sub = str(report.get('subtask_name') or '')
                slots = w.job.get('slots') if w.job is not None and w.job['subtask'] == sub else None
                if slots is None:
                    slots = start_slots(serial, report)
                w.job = {'subtask': sub, 'plate': _plate_of(report), 'mapping': report.get('mapping'),
                         'slots': slots}
                return
            job, w.job = w.job, None
            if first or gs not in TERMINAL or prev not in ACTIVE or job is None:
                return
            if job['subtask'].startswith(CALIB_PREFIXES):
                return
            pct = 100.0 if gs == 'FINISH' else float(status._int(report.get('mc_percent'), 0) or 0)
            if gs == 'FINISH':
                outcome = 'finished'
            else:
                fr = str(report.get('fail_reason') or '').strip().lower()
                if status._print_error(report) or fr not in ('', '0'):
                    outcome = 'failed'
                else:
                    outcome = 'unknown'
            settled_job = {**job, 'outcome': outcome}
            log.info("usage: printer %s: %s %s at %s %%", serial, job['subtask'], gs, pct)
            self._futures = [f for f in self._futures if not f.done()]
            try:
                self._futures.append(self._pool.submit(self._settle, serial, settled_job, pct))
            except RuntimeError:          # stopped meanwhile
                pass

    def _settle(self, serial: str, job: dict, pct: float) -> None:
        try:
            settle(serial, job, pct, self._spoolman)
        except Exception:
            log.exception("usage: printer %s: settling %s failed", serial, job.get('subtask'))


def settle(serial: str, job: dict, pct: float, spoolman) -> list[dict]:
    """Settle one ended job (`{subtask, plate, mapping}`) at `pct` %: deduct and log; returns the new entries."""
    name = job.get('subtask') or ''
    p = registry.load().get(serial)
    pname = registry.display_name(p) if p is not None else serial
    outcome = job.get('outcome') if job.get('outcome') in OUTCOMES else 'unknown'

    def fail(error: str) -> list[dict]:
        log.warning("usage: printer %s: job %r not settled: %s", serial, name, error)
        return [append(serial, {'job': name, 'grams': None, 'spool_id': None, 'slot': None, 'observe': False,
                                'material': None, 'vendor': None, 'name': None, 'color_hex': None,
                                'printer_name': pname, 'outcome': outcome, 'error': error})]
    if p is None or not name:
        return fail('file_not_found')
    if not isinstance(job.get('mapping'), list) or not job['mapping']:
        return fail('no_mapping')        # the printer never said which slots this job used: guess nothing
    try:
        data, path = fetch_3mf(p, name, job.get('plate'))
    except tlspin.CertChanged:
        return fail('cert_changed')
    except ftps.FtpsError:
        return fail('printer_unreachable')
    if data is None:
        return fail('file_not_found')
    plates = parse_filaments(data)
    if not plates:
        return fail('no_slice_info')
    plate = job.get('plate')
    fils = plates.get(plate) if plate in plates else (next(iter(plates.values())) if len(plates) == 1 else None)
    if fils is None:
        return fail('plate_not_found')

    smap = ams.load_map(serial)
    stamp = smap.updated
    hw = hardware.load()
    model = p.model or None
    start = job.get('slots') if isinstance(job.get('slots'), dict) else {}
    spools: dict[str, list[dict]] = {}
    spool_error = None
    try:
        for s in spoolman.get('spool') or []:          # archived spools are not listed
            spools.setdefault(str(s.get('location') or ''), []).append(s)
    except SpoolmanError as e:
        log.warning("usage: printer %s: cannot list spools: %s", serial, e)
        spool_error = 'spoolman_unreachable'
    mapping_ = job.get('mapping') if isinstance(job.get('mapping'), list) else []
    frac = max(0.0, min(100.0, pct)) / 100.0

    # decode every filament first: learning and the mismatch streak decide observe mode for this very print
    plan, mismatches, checked, changed = [], 0, 0, False
    for f in fils:
        grams = round(f['used_g'] * frac, 2)
        if grams <= 0:
            continue
        g = status._int(mapping_[f['id'] - 1]) if 1 <= f['id'] <= len(mapping_) else None
        label, how = decode(g, smap, model, start, hw) if g is not None else (None, 'no mapping entry')
        if label is None and smap.calibrated and g is not None and g >= 0 and start:
            cands = [lab for lab, sl in start.items() if _matches_unseen(sl, f)]
            if len(cands) == 1:
                label, how = cands[0], 'learned from this print'
                smap.learned[str(g)] = label
                changed = True
                log.info("usage: printer %s: learned slot number %s = %s from %r", serial, g, label, name)
        ok = True
        if smap.calibrated and label and start:
            ok = _holds(start.get(label), f)
            checked += 1
            if not ok:
                mismatches += 1
        plan.append((f, grams, g, label, how, ok))
    if smap.calibrated and checked:
        before = (smap.mismatch_streak, smap.suspect)
        if mismatches:
            smap.mismatch_streak += 1
            if smap.mismatch_streak >= 2 and not smap.suspect:
                smap.suspect, smap.observe = True, True
                log.warning("usage: printer %s: two prints in a row disagreed with the slot map: logging only "
                            "until it is re-mapped", serial)
        else:
            smap.mismatch_streak = 0
        changed = changed or (smap.mismatch_streak, smap.suspect) != before
    if changed:
        _save_map(serial, smap, stamp)
        if smap.suspect:
            mapping.request_remap(serial)
    observe = not smap.calibrated or smap.observe

    out, deducted = [], False
    for f, grams, g, label, how, ok in plan:
        here = []
        if label:
            pname = registry.display_name(p)
            here = spools.get(status.location(pname, label), [])
            old = next((a for a, cur in layout.aliases(p.model).items() if cur == label), None)
            if old:     # a spool still at the holder's older location ("<name> – AMS Ext" for Ext-L)
                here = here + spools.get(status.location(pname, old), [])
        spool = here[0] if len(here) == 1 else None
        fil = (spool.get('filament') or {}) if spool else {}
        raw_color = fil.get('color_hex') or f.get('color') or ''
        color_hex = str(raw_color).strip().lstrip('#')[:6].upper() or None
        entry = {'job': name, 'grams': grams, 'spool_id': spool.get('id') if spool else None, 'slot': label,
                 'observe': observe,
                 'material': fil.get('material') or f.get('type') or None,
                 'vendor': (fil.get('vendor') or {}).get('name') or None,
                 'name': fil.get('name') or None,
                 'color_hex': color_hex,
                 'printer_name': pname,
                 'outcome': outcome}
        if smap.calibrated and g is not None and g >= 0 and label is None:
            entry['error'] = 'unmapped_slot'     # a slot the map does not know: run Map slots
        elif not ok:
            entry['error'] = 'slot_mismatch'     # the slot held another filament than the slice says
        elif spool_error and label:
            entry['error'] = spool_error
        elif len(here) > 1:
            log.warning("usage: printer %s: spools %s all claim slot %s; nothing deducted", serial,
                        [x.get('id') for x in here], label)
            entry['error'] = 'ambiguous_spool'
        elif not observe and spool:
            try:
                spoolman.use(spool['id'], grams)
            except SpoolmanError as e:
                log.warning("usage: printer %s: cannot deduct %s g from spool %s: %s", serial, grams, spool['id'], e)
                entry['error'] = 'spoolman_failed'
        log.info("usage: printer %s: %s %s g filament %s (mapping %s, %s) -> %s spool %s%s", serial,
                 'would deduct' if observe else 'deducted', grams, f['id'], g, how, label, entry['spool_id'],
                 f" [{entry['error']}]" if entry.get('error') else '')
        out.append(append(serial, entry))
        if spool and not observe and not entry.get('error'):      # taken off a spool: that is what usage.recorded says
            deducted = True
            event_sink.emit("usage.recorded", {"type": "printer", "id": serial, "name": registry.display_name(p)},
                            {"printer": serial, "job": name, "spool": spool.get('id'),
                             "material": (spool.get("filament") or {}).get("material"), "grams": grams,
                             "run": event_sink.runs.last(serial, name) or None})
    log.info("usage: printer %s: job %r settled from %s (%d entries%s)", serial, name, path, len(out),
             ', observe mode' if observe else '')
    if deducted:
        event_sink.weight_changed(spoolman)       # running low is checked right after the Hub changes a weight
    return out
