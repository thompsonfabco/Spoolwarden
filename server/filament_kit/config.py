"""Server configuration: paths, defaults, load/save of config.xml (and config.json, kept in both forms for one release)."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import socket
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Paths:
    etc: Path
    state: Path
    backups: Path
    opt: Path
    web: Path            # the web unit's own state (its OAuth store, kit token, Web Push keys), user filament-kit-web


def paths() -> Paths:
    return Paths(
        etc=Path(os.environ.get("FK_ETC", "/etc/filament-kit")),
        state=Path(os.environ.get("FK_STATE", "/var/lib/filament-kit")),
        backups=Path(os.environ.get("FK_BACKUPS", "/var/backups/filament-kit")),
        opt=Path(os.environ.get("FK_OPT", "/opt/filament-kit")),
        web=_web_state(),
    )


def _web_state() -> Path:
    """FK_WEB_STATE; else beside an FK_STATE given (tests, a dev copy: `<state>-web`); else the installed place."""
    if os.environ.get("FK_WEB_STATE"):
        return Path(os.environ["FK_WEB_STATE"])
    if os.environ.get("FK_STATE"):
        st = Path(os.environ["FK_STATE"])
        return st.with_name(st.name + "-web")
    return Path("/var/lib/filament-kit-web")


WEB_USER = "filament-kit-web"   # the web unit's user: its own group, never in the filament-kit group
KIT_USER = "filament-kit"


def uid_of(user: str) -> int | None:
    try:
        import pwd
        return pwd.getpwnam(user).pw_uid
    except (ImportError, KeyError):
        return None


def grant_web_read(path) -> None:
    """Let the web unit's user read `path` (a root-owned settings file it needs: config.json, web_passphrase,
    anthropic_key) with a POSIX ACL; a no-op unless root, with that user, on a file system with ACLs."""
    from . import acl
    if not acl.supported() or getattr(os, "geteuid", lambda: -1)() != 0:
        return
    uid = uid_of(WEB_USER)
    if uid is not None:
        try:
            acl.grant(path, {uid: acl.R})
        except OSError as e:
            import logging
            logging.getLogger(__name__).warning(
                "cannot let %s read %s (%s); the web unit will fail to read it: check that the file system "
                "supports ACLs, then run: sudo spoolwarden modules apply", WEB_USER, path, e)


# The one install line (README). It also repairs an install: get.sh fetches the release, unlike an ./install.sh it deleted.
GET_COMMAND = "curl -fsSLO https://github.com/thompsonfabco/filament-kit/releases/latest/download/get.sh && sudo bash get.sh"

DEFAULTS: dict = {
    "server_name": socket.gethostname(),
    "spoolman_url": "http://127.0.0.1:7912",
    "port": 8710,
    "model": "claude-opus-5-5",
    "reads_per_day": 60,
    "locations": ["Sealed stock", "Rack 1", "Storage box", "Overflow"],
    "placement": {
        "pla_location": "Rack 1",
        "box_prefix": "Storage box ",
        "box_count": 0,
        "overflow": "Overflow",
        "sealed_location": "Sealed stock",
    },
    "printers": [],
    "auto_remap": True,         # re-map a printer's slots by itself (when idle) after its AMS layout changed
    "library": {"url": None, "schedule": "daily"},
    "station": {"url": None, "allow": ["127.0.0.1"], "port": 5003,
                "nfc_device": "auto", "beep": True, "beep_device": "",
                "speech": True, "tts": "auto", "voice": None, "camera": "auto", "panel_flash": True,
                "panel_swap": False,  # the panel's yellow and red are wired to each other's pins
                "page_from": "home",  # who the unit admits for the page: home, subnets or allowed (apply.page_ranges)
                "speaker_volume": 50,   # the C3 panel's speaker, percent
                "panel_link": "usb", "panel_port": 5004},   # how the panel reaches the station, and its Wi-Fi listener (R88 4.4)
    # merge-ops: web unit, remote access, orders file, tag readers, slot service, slicer/presets/intake extras
    "web": {"port": 8101, "public_url": None, "push": False,   # push: Web Push alerts (needs internet egress)
            "lan": ["192.168.0.0/16", "10.0.0.0/8"]},   # not 172.16/12: Docker's bridges live there (A-M6)
    "remote": {"hostname": None, "acme_window": None, "email": None},
    # who may reach the spoolman module's listener, which has no sign-in. None: the same ranges as web.lan
    "spoolman": {"allow": None},
    "orders": {"file": None},
    "tag_sources": [],          # [{"name": str, "url": str}]: extra readers speaking the lasttag shape
    "slicer_printers": [],
    "presets_orca": False,
    # reader: "auto" (local reader first, the cloud when it is unsure; or the one that is on) | cloud | local |
    # local-then-cloud | cloud-then-local
    "intake": {"tare_hints": {}, "model": None, "reader": "auto"},
    # the local label reader (local_intake module): its address, here or on another machine, and its tier
    "local_intake": {"url": "http://127.0.0.1:7915", "tier": "auto"},
    "web_instructions": None,
    "appearance": {"theme": "console"},     # the theme for devices that have not chosen one (design 01 6.2)
    "events": {"keep_days": 400},           # how long the event store keeps its month files (R80 section 1)
}

KEEP_DAYS_MIN, KEEP_DAYS_MAX = 30, 3650


def keep_days(cfg: dict) -> int:
    """`events.keep_days`: whole days, 30 to 3650; 400 when it is missing or not a number in that range."""
    e = cfg.get("events")
    v = e.get("keep_days") if isinstance(e, dict) else None
    if isinstance(v, bool) or not isinstance(v, int) or not KEEP_DAYS_MIN <= v <= KEEP_DAYS_MAX:
        return DEFAULTS["events"]["keep_days"]
    return v

def _whole_in(v, low: int, high: int, default: int) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and low <= v <= high else default


PANEL_LINKS = ("usb", "wifi")


def panel_link(cfg: dict) -> str:
    """`station.panel_link` (R88 4.4): `usb` or `wifi`; `usb` when it is missing or anything else."""
    st = cfg.get("station")
    v = st.get("panel_link") if isinstance(st, dict) else None
    return v if v in PANEL_LINKS else DEFAULTS["station"]["panel_link"]


def panel_port(cfg: dict) -> int:
    """`station.panel_port`: the port the Wi-Fi listener takes, 1 to 65535; 5004 when it is missing or out of range."""
    st = cfg.get("station")
    return _whole_in(st.get("panel_port") if isinstance(st, dict) else None, 1, 65535, DEFAULTS["station"]["panel_port"])


LOOKS = ("console", "workbench", "palette", "panes")    # the four themes, Console first (design 01 section 6)


def look(cfg: dict) -> str:
    """The Hub's default theme: `appearance.theme`, Console when it is missing or not one of the four."""
    a = cfg.get("appearance")
    v = a.get("theme") if isinstance(a, dict) else None
    return v if v in LOOKS else "console"


def _merge(base: dict, over: dict) -> dict:
    """Merge `over` onto `base`; dict values are merged one level down."""
    out = copy.deepcopy(base)
    for key, val in over.items():
        if isinstance(val, dict) and isinstance(out.get(key), dict):
            out[key].update(copy.deepcopy(val))
        else:
            out[key] = copy.deepcopy(val)
    return out


def _file() -> Path:
    return paths().etc / "config.json"


def _xml_file() -> Path:
    return paths().etc / "config.xml"


def _read_xml(xf: Path) -> dict | None:
    """The settings in config.xml; None when there is none. A file that cannot be read is noted for the Checks row
    (once) and raises, for `load` to fall back on config.json."""
    from .profiles import statexml
    try:
        data = xf.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as e:
        reason = f"cannot read: {e.strerror or e}"
    else:
        try:
            out = statexml.loads(data, "config")
            statexml.clear_problem(xf)
            return out
        except statexml.StateXmlError as e:
            reason = str(e)
    if not any(x.startswith(f"{xf} (") for x in statexml.problems()):
        import logging
        logging.getLogger(__name__).warning("%s cannot be read (%s); reading config.json", xf, reason)
    statexml.note_problem(xf, reason)
    raise ValueError(f"{xf} cannot be read ({reason})")


def _newer_json(stored: dict, f: Path) -> dict:
    """config.xml's settings, unless config.json was changed after it was written (an older release after a rollback):
    config.xml records the hash of the config.json it was saved with, as printers.xml does (registry.py `_read`)."""
    sha = stored.pop("json_sha256", None)
    if sha is None:
        return stored                   # saved before the hash was recorded
    try:
        raw = f.read_bytes()
        if hashlib.sha256(raw).hexdigest() == sha:
            return stored
        other = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return stored
    return other if isinstance(other, dict) and other != stored else stored


def load_raw() -> dict:
    """What the settings files hold, with no defaults merged in ({} when there is none): config.xml when it reads, else
    config.json; an error (ValueError) only when no form reads."""
    damaged = None
    try:
        stored = _read_xml(_xml_file())
    except ValueError as e:
        stored, damaged = None, e       # damaged: config.json is read, and an error only when it fails too
    f = _file()
    if stored is not None:
        return _newer_json(stored, f)
    if not f.exists():
        if damaged:
            raise damaged
        return {}
    return json.loads(f.read_text(encoding="utf-8"))


def load() -> dict:
    defaults = copy.deepcopy(DEFAULTS)
    defaults["server_name"] = socket.gethostname()
    return _merge(defaults, load_raw())


def _write(f: Path, write) -> None:
    f.parent.mkdir(parents=True, exist_ok=True)
    # a name of its own (mkstemp, mode 0600 until the chmod below): two savers at once never write into one file
    fd, tmp = tempfile.mkstemp(dir=f.parent, prefix=f.name + ".", suffix=".tmp")
    tmp = Path(tmp)
    try:
        with os.fdopen(fd, "wb") as fh:
            write(fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o640)
        grant_web_read(tmp)
        os.replace(tmp, f)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise


KEEP_UNREADABLE = 5       # copies of a set-aside file kept: `.unreadable` (newest), `.unreadable.1` ... `.unreadable.4`


def _parses_broken(xf: Path) -> bool:
    """True only when config.xml was read and really is broken; a read that fails with an OSError (EACCES, EIO) says
    nothing about the file, so it stays where it is (the statexml helper's rule)."""
    from .profiles import statexml
    try:
        data = xf.read_bytes()
    except OSError:
        return False
    try:
        statexml.loads(data, "config")
    except statexml.StateXmlError:
        return True
    return False


def _set_aside(xf: Path) -> None:
    """Keep config.xml as config.xml.unreadable; an earlier copy is renamed .1, .2, ... (five at most, the oldest
    beyond that is replaced), never deleted unseen by a plain overwrite of the newest."""
    names = [xf.with_name(xf.name + ".unreadable" + (f".{i}" if i else "")) for i in range(KEEP_UNREADABLE)]
    if names[-1].exists():
        names[-1].unlink()
    for i in range(KEEP_UNREADABLE - 1, 0, -1):
        if names[i - 1].exists():
            os.replace(names[i - 1], names[i])
    os.replace(xf, names[0])


def save(cfg: dict) -> None:
    """Both forms (R85 section 1.5: the JSON form stays for one release, for `update --rollback`)."""
    from .profiles import statexml
    xf = _xml_file()
    if any(x.startswith(f"{xf} (") for x in statexml.problems()) and xf.exists() and _parses_broken(xf):
        _set_aside(xf)                  # never write over what could not be read (a passing read error keeps the file)
        statexml.clear_problem(xf)
    js = json.dumps(cfg, indent=2).encode("utf-8")
    # config.json first, then config.xml with its hash: a stop in between leaves a hash that does not match, and the
    # next read takes the newer JSON
    _write(_file(), lambda fh: fh.write(js))
    _write(xf, lambda fh: fh.write(statexml.dumps("config", {**cfg, "json_sha256": hashlib.sha256(js).hexdigest()})))
