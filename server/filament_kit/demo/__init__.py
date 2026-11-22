"""Demo mode (R85 section 4, docs/design 03 section 3.9): the whole product on made-up data with pretend printers.

The demo is on while `<state>/demo/demo.xml` exists. Everything the demo makes lives in `<state>/demo/` (the inventory,
the events, the usage log, the storage locations, Recently added: `recorded(name)` is the path of one of them), and
leaving deletes that folder, so the Hub is the empty Hub it was. Nothing the demo does reaches a printer or Spoolman
or the outside: the pieces that would answer `NotInDemo` (message "Not in the demo.") through `stop()`.

Pieces: `inventory` (the demo's Spoolman, which `spoolman.Spoolman` calls while the demo is on), `printers` (the
pretend printers and the scripted alert), `routes` (the route list RC9 mounts). The sample set is `sample.xml`
(docs/design mockups/CONTENT-SPEC.md, "Workshop hub": 64 spools of 23 filaments, three printers and one found).
No JSON is stored.
"""
from __future__ import annotations

import os
import shutil
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from .. import config

FOLDER = "demo"
MARKER = "demo.xml"
VERSION = "1"
NOT_EMPTY = "The demo is for an empty Hub. This Hub has real data."
NOT_IN_DEMO = "Not in the demo."
CANNOT_TELL = "Cannot tell whether this Hub is empty: Spoolman cannot be reached. Start Spoolman, then try again."
NO_CAMERA = "No camera in the demo."
NOT_MOUNTABLE = "The demo cannot be switched on in this version."
NOT_IN_DEMO_CODE = "in_demo"            # the code of a stop (409); "not_in_demo" is the leave route's: the demo is off
SAMPLE = Path(__file__).with_name("sample.xml")
# Whether the demo may be switched on: `start()` refuses (`NotMountable`) while this is False. Set True only when the seal test's leak lists (`server/tests/test_demo_sealed.py`)
# are all empty: `test_the_demo_is_not_mountable_while_a_leak_list_is_not_empty` fails otherwise.
# True only in the commit that proves the remaining parts of R85 assumption 5 (event stream, pairing, profiles routes, routes
# with real bodies, a Manager started before the demo, root helper/systemd paths, the CLI beyond three stops): Bug-bash 1 owns that proof.
MOUNTABLE = False


class DemoError(Exception):
    """A demo request the Hub refuses: `code` and the sentence (`message`) go into `{"error": code, "message": ...}`."""

    code = "demo"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class NotEmpty(DemoError):
    code = "not_empty"

    def __init__(self):
        super().__init__(NOT_EMPTY)


class CannotTell(DemoError):
    """Spoolman does not answer, so whether the Hub holds spools is not known: the demo does not start (R85 4.2)."""

    code = "cannot_tell_if_empty"

    def __init__(self):
        super().__init__(CANNOT_TELL)


class NotInDemo(DemoError):
    """What a stop raises: an outside reach the demo does not make."""

    code = NOT_IN_DEMO_CODE

    def __init__(self):
        super().__init__(NOT_IN_DEMO)


class NotMountable(DemoError):
    """`start()` while `MOUNTABLE` is False: this version cannot switch the demo on (the seal is not complete)."""

    code = "not_mountable"

    def __init__(self):
        super().__init__(NOT_MOUNTABLE)


class NotDemo(DemoError):
    """`demo off` / leave while the demo is off."""

    code = "not_in_demo"

    def __init__(self):
        super().__init__("The demo is not on.")


def folder() -> Path:
    return config.paths().state / FOLDER


def is_on() -> bool:
    """True while the demo's marker file is there. A link is no marker (nothing is followed)."""
    try:
        m = folder() / MARKER
        return m.is_file() and not m.is_symlink()
    except OSError:
        return False


def recorded(name: str) -> Path:
    """The path of something the Hub records, inside the demo's folder (`events`, `usage`, `locations.xml` ...).
    Callers use it instead of `<state>/<name>` while `is_on()`; the parent folder is made."""
    p = folder() / name
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def where(real: Path, name: str) -> Path:
    """`real` normally; the demo's own place for `name` while the demo is on. The one line a recorder needs."""
    return recorded(name) if is_on() else real


def _give_to_state_owner(root: Path) -> None:
    """As root (the command line), the demo's folder and files belong to the owner of the state folder, so the Hub's
    service can write them; nothing is followed through a link."""
    if getattr(os, "geteuid", lambda: -1)() != 0:
        return
    st = root.parent.stat()
    for p in [root, *root.rglob("*")]:
        if not p.is_symlink():
            os.chown(p, st.st_uid, st.st_gid)


def stop() -> None:
    """The stop: call where the Hub would reach outside (a printer, Spoolman, the Label Reader's cloud, GitHub, a
    push, a backup, a flash, the root helper). Raises `NotInDemo` while the demo is on; nothing otherwise."""
    if is_on():
        raise NotInDemo()


def refusal(e: DemoError) -> dict:
    return {"error": e.code, "message": e.message}


def state() -> dict:
    """What the demo adds to `GET /api/info`, `GET /api/hub` and the page's state."""
    return {"demo": is_on()}


def _now(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).astimezone().isoformat(timespec="seconds")


def real_data(spoolman=None, registry_load=None) -> str | None:
    """Why the Hub is not empty ("spools" or "printers"), None when it has no spool and no printer. A Spoolman that
    cannot be reached is not an empty Hub: `CannotTell`, as is one that answers with an error it is not known to be
    empty ("spools"). A found printer nobody added is not a printer yet."""
    from ..bridge import registry
    from ..spoolman import Spoolman, SpoolmanError
    try:
        if any(p.configured for p in (registry_load or registry.load)().values()):
            return "printers"
    except registry.RegistryError:
        return "printers"
    try:
        sm = spoolman if spoolman is not None else Spoolman(config.load()["spoolman_url"], timeout=5)
        if sm.get("spool?allow_archived=true"):
            return "spools"
    except SpoolmanError as e:
        if e.status is not None:
            return "spools"
        raise CannotTell() from None
    return None


def start(now: datetime | None = None, *, spoolman=None, registry_load=None) -> dict:
    """Switch the demo on: only on an empty Hub (`NotEmpty` otherwise). Already on is a plain yes. `NotMountable` while `MOUNTABLE` is False. Makes the demo's
    folder with its inventory (the sample's) and the marker; returns `{demo: True}`."""
    if is_on():
        return {"demo": True}
    if not MOUNTABLE:                            # nothing is made or written: the seal is not complete in this version
        raise NotMountable()
    if real_data(spoolman, registry_load) is not None:
        raise NotEmpty()
    from . import inventory
    d = folder()
    d.mkdir(parents=True, exist_ok=True)
    inventory.seed(d / inventory.FILE)
    from . import printers
    printers.seed_usage(now)
    printers.rearm_all()
    root = ET.Element("demo", version=VERSION, started=_now(now))
    tmp = d / (MARKER + ".tmp")
    ET.ElementTree(root).write(tmp, encoding="utf-8", xml_declaration=True)
    _give_to_state_owner(d)
    os.replace(tmp, d / MARKER)                  # the marker comes last: a half-made folder is not a demo
    _give_to_state_owner(d)
    printers.switched()                          # a real printer family started before this closes its connections now
    return {"demo": True}


def leave() -> dict:
    """Leave the demo: delete its folder. `NotDemo` when it is off. The Hub is as it was: empty."""
    if not is_on():
        raise NotDemo()
    d = folder()
    from .. import events                        # not at the top: events.py imports this module
    try:                                         # the real log goes on above every id the demo gave: no cursor skips
        high = events.EventStore(d / "events").last_id()
        if high:
            events.EventStore(config.paths().state / "events").carry(high)
    except (OSError, events.EventError):
        pass
    (d / MARKER).unlink()                        # off first, so a half-deleted folder is no demo
    shutil.rmtree(d, ignore_errors=True)
    from . import printers
    printers.switched()                          # and opens them again for the real registry
    return {"demo": False}


def started() -> str | None:
    from ..bridge import xmlfile          # not at the top: events.py imports this module and must not need defusedxml
    try:
        root = xmlfile.fromstring((folder() / MARKER).read_bytes())
        return root.get("started")
    except Exception:  # noqa: BLE001 - no marker, or one that cannot be read: no time to show
        return None
