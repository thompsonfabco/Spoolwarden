"""The station's own settings (R83 section 9 and 10.4): what its page changes, kept in `settings.xml` in the station's
state folder (`state.state_dir()`: the one folder its service user owns). They WIN over the values the installer wrote
(the systemd drop-in `state.settings_from_env` reads) and "back to the installed values" (`Store.reset`) removes the
file. Only values that differ from the installed ones are kept, so the file's presence is `settings_changed`.

The ids are the contract's: `beep`, `speech`, `voice`, `volume`, `panel_swap`, and one `lamp.<step>` per step of the
intake flow. Which lamp shows a step is `flow.lamp_modes`: the mapping is one small function, easy to change.

The file is XML, 0600, in a folder made 0700; read without following a link, written whole through a temp file and a
rename (a link at the file's name is replaced, never written through).
"""
from __future__ import annotations

import logging
import os
import tempfile
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

from . import flow
from . import state as st

log = logging.getLogger("intake-station")

FILE = "settings.xml"
MAX_BYTES = 64 * 1024
VERSION = "1"
VOICES = ("auto", "espeak")           # the speech engine: Piper when its voice is installed, or always espeak-ng
STEP_LABELS = {"pairing": "Asking the Hub to pair", "idle": "Ready", "lookup": "Looking up a tag",
               "camera": "Show the spool to the camera", "recognising": "Recognising the spool",
               "weigh": "Put it on the scale", "ready": "Weight taken", "saving": "Saving", "done": "Saved",
               "phone": "Use the phone"}
LAMP_LABELS = {"off": "No lamp", "yellow": "Yellow lamp", "red": "Red lamp", "both": "Both lamps"}


class SettingsError(ValueError):
    """A value the station refuses (unknown id, wrong kind, out of range), or a file it could not write."""


def _spec(id_, label, kind, **more) -> dict:
    return {"id": id_, "label": label, "kind": kind, **more}


def specs() -> list[dict]:
    """The settings and what each accepts, in the order the page shows them: id, label, kind and (choices | min, max)."""
    out = [_spec("beep", "Beep when a tag is scanned", "switch"),
           _spec("speech", "Speak the steps aloud", "switch"),
           _spec("voice", "Voice", "choice", choices=list(VOICES), choice_labels={"auto": "Automatic", "espeak": "eSpeak"}),
           _spec("volume", "Speaker volume (percent)", "number", min=0, max=100),
           _spec("panel_swap", "Swap the yellow and red buttons and lamps", "switch")]
    out += [_spec(f"lamp.{step}", f"Lamp for: {STEP_LABELS.get(step, step)}", "choice", choices=list(flow.LAMP_CHOICES),
                  choice_labels=LAMP_LABELS)
            for step in flow.STEP_LAMPS]
    return out


_KEYS = {"beep": "beep", "speech": "speech", "voice": "tts", "volume": "speaker_volume", "panel_swap": "panel_swap"}


def clean(spec: dict, value):
    """`value` as the setting's own type, or SettingsError. A switch takes true/false (and "1"/"0", "on"/"off"), a number
    an integer in its range (a bool is no number), a choice one of its choices."""
    kind = spec["kind"]
    if kind == "switch":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower() if isinstance(value, (str, int)) else ""
        if text in ("1", "on", "true"):
            return True
        if text in ("0", "off", "false"):
            return False
    elif kind == "number":
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            value = int(value.strip())
        if isinstance(value, int) and not isinstance(value, bool) and spec["min"] <= value <= spec["max"]:
            return value
    elif kind == "choice":
        if isinstance(value, str) and value in spec["choices"]:
            return value
    raise SettingsError(f"{spec['id']}: not a value this setting takes")


def _text(value) -> str:
    return ("on" if value else "off") if isinstance(value, bool) else str(value)


class Store:
    """The installed values (a copy of `target` when the store is made) with the file's values over them. `target` is
    `state.settings` and `lamps` is `state.lamp_map`: both are changed in place, so every module that reads them sees a
    change at once. `after(ids)` is told which ids changed (the speaker volume is sent to the panel)."""

    def __init__(self, path: Path | None = None, target: dict | None = None, lamps: dict | None = None, after=None):
        self.path = path or st.state_dir() / FILE
        self.target = st.settings if target is None else target
        self.lamps = st.lamp_map if lamps is None else lamps
        self.after = after
        self.installed = dict(self.target)
        self._lock = threading.Lock()
        self.problem: str | None = None       # why the file was not used (a line for the journal), else None
        self.values: dict = self._read()
        self._apply()

    # ---- the file
    def _read(self) -> dict:
        wanted = {s["id"]: s for s in specs()}
        try:
            fd = os.open(self.path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            return {}
        except OSError as e:
            self.problem = f"{self.path} could not be opened ({e.strerror or e}): the installed values are used"
            log.warning("station settings: %s", self.problem)
            return {}
        try:
            with os.fdopen(fd, "rb") as fh:
                raw = fh.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError("too big")
            root = ET.fromstring(raw)
            if root.tag != "settings":
                raise ValueError("not a settings file")
        except (OSError, ValueError, ET.ParseError) as e:
            self.problem = f"{self.path} could not be read ({e}): the installed values are used"
            log.warning("station settings: %s", self.problem)
            return {}
        out = {}
        for el in root.iter("setting"):
            spec = wanted.get(el.get("id") or "")
            try:
                if spec:
                    out[spec["id"]] = clean(spec, el.get("value"))
            except SettingsError:
                log.warning("station settings: ignoring %s=%r", el.get("id"), el.get("value"))
        return {k: v for k, v in out.items() if v != self._installed(k)}

    def _write(self, values: dict) -> None:
        root = ET.Element("settings", version=VERSION)
        for k, v in sorted(values.items()):
            ET.SubElement(root, "setting", id=k, value=_text(v))
        data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
        tmp = None
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=FILE + ".", suffix=".tmp")
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
                if hasattr(os, "fchmod"):
                    os.fchmod(fh.fileno(), 0o600)
            os.replace(tmp, self.path)
        except OSError as e:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)
            raise SettingsError(f"the settings could not be saved ({e.strerror or e})") from e

    def _remove(self) -> None:
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        except OSError as e:
            raise SettingsError(f"the station's own values could not be removed ({e.strerror or e})") from e

    # ---- values
    def _installed(self, id_):
        if id_.startswith("lamp."):
            return flow.lamp_default(id_[5:])
        return self.installed[_KEYS[id_]]

    def current(self, id_):
        return self.values[id_] if id_ in self.values else self._installed(id_)

    def listing(self) -> list[dict]:
        """`[{id, label, kind, value, choices?, min?, max?}]`: the contract's answer of GET /settings."""
        with self._lock:
            return [dict(s, value=self.current(s["id"])) for s in specs()]

    @property
    def changed(self) -> bool:
        """The station's own values are in force (the `settings_changed` of GET /state)."""
        return bool(self.values)

    def _apply(self) -> None:
        for id_, key in _KEYS.items():
            self.target[key] = self.current(id_)
        self.lamps.clear()
        self.lamps.update({i[5:]: v for i, v in self.values.items() if i.startswith("lamp.")})

    def _told(self, ids) -> None:
        if self.after and ids:
            try:
                self.after(set(ids))
            except Exception:  # noqa: BLE001 - the values are saved; a panel that does not answer is no reason to refuse them
                log.exception("station settings: after a change")

    def change(self, body: dict) -> list[dict]:
        """Take `{id: value}` (all of it or none: one bad value refuses the whole), keep it, apply it, answer the list."""
        if not isinstance(body, dict) or not body:
            raise SettingsError("nothing to change")
        wanted = {s["id"]: s for s in specs()}
        new = {}
        for k, v in body.items():
            if k not in wanted:
                raise SettingsError(f"{str(k)[:40]}: no such setting")
            new[k] = clean(wanted[k], v)
        with self._lock:
            values = dict(self.values)
            for k, v in new.items():
                if v == self._installed(k):
                    values.pop(k, None)
                else:
                    values[k] = v
            if values:
                self._write(values)
            else:
                self._remove()
            before, self.values = self.values, values
            self._apply()
            moved = {k for k in set(before) | set(values) if before.get(k) != values.get(k)}
        self._told(moved)
        return self.listing()

    def reset(self) -> list[dict]:
        """Back to the installed values: the station's own file goes."""
        with self._lock:
            self._remove()
            moved, self.values = set(self.values), {}
            self._apply()
        self._told(moved)
        return self.listing()


def after_change(ids: set) -> None:
    """What a change needs besides the new value: the C3 panel is told its volume (it keeps none of its own)."""
    if "volume" in ids:
        from . import panel
        panel.send_volume()
