"""The station's own settings: what its page changes, kept in `settings.xml` in the station's
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
from . import page as _station_page
from . import state as st

log = logging.getLogger("intake-station")

FILE = "settings.xml"
MAX_BYTES = 64 * 1024
VERSION = "1"
VOICES = ("auto", "espeak")           # the speech engine: Piper when its voice is installed, or always espeak-ng
_t = _station_page.catalogue_text
STEP_LABELS = {
    "pairing": _t("station_step_pairing", "Asking the Hub to pair"),
    "idle": _t("station_step_idle", "Ready"),
    "lookup": _t("station_step_lookup", "Looking up a tag"),
    "camera": _t("station_step_camera", "Show the spool to the camera"),
    "recognising": _t("station_step_recognising", "Recognising the spool"),
    "weigh": _t("station_step_weigh", "Put it on the scale"),
    "ready": _t("station_step_ready", "Weight taken"),
    "saving": _t("station_step_saving", "Saving"),
    "done": _t("station_step_done", "Saved"),
    "phone": _t("station_step_phone", "Use the phone"),
}
LAMP_LABELS = {
    "off": _t("station_lamp_off", "No lamp"),
    "yellow": _t("station_lamp_yellow", "Yellow lamp"),
    "red": _t("station_lamp_red", "Red lamp"),
    "both": _t("station_lamp_both", "Both lamps"),
}


class SettingsError(ValueError):
    """A value the station refuses (unknown id, wrong kind, out of range), or a file it could not write."""


class SetOnHub(SettingsError):
    """A change of Speak or Volume after the Hub has sent voice values: the Hub's win, so the page may not change them."""

    code = "set_on_hub"

    def __init__(self):
        super().__init__(st.VOICE_TEXTS["set_on_hub"])


HUB_VOICE_IDS = ("speech", "volume")     # the two settings the Hub's voice values (`state.live`) take over


def _spec(id_, label, kind, **more) -> dict:
    return {"id": id_, "label": label, "kind": kind, **more}


def specs() -> list[dict]:
    """The settings and what each accepts, in the order the page shows them: id, label, kind and (choices | min, max)."""
    out = [_spec("beep", _t("station_setting_beep", "Beep when a tag is scanned"), "switch"),
           _spec("speech", _t("station_setting_speech", "Speak the steps aloud"), "switch"),
           _spec("voice", _t("station_setting_voice", "Voice"), "choice", choices=list(VOICES),
                 choice_labels={"auto": _t("station_voice_auto", "Automatic"), "espeak": _t("station_voice_espeak", "eSpeak")}),
           _spec("volume", _t("station_setting_volume", "Speaker volume (percent)"), "number", min=0, max=100),
           _spec("panel_swap", _t("station_setting_panel_swap", "Swap the yellow and red buttons and lamps"), "switch")]
    out += [_spec(f"lamp.{step}", _t("station_setting_lamp", "Lamp for: {step}", step=STEP_LABELS.get(step, step)),
                  "choice", choices=list(flow.LAMP_CHOICES), choice_labels=LAMP_LABELS)
            for step in flow.STEP_LAMPS]
    out += [_spec("flow_timeout", _t("station_setting_flow_timeout", "Intake flow timeout (seconds)"), "number", min=30, max=3600),
            _spec("recognise_timeout", _t("station_setting_recognise_timeout", "Spool recognition timeout (seconds)"), "number", min=15, max=300),
            _spec("min_grams", _t("station_setting_min_grams", "Minimum spool weight (grams)"), "number", min=1, max=500),
            _spec("camera", _t("station_setting_camera", "Camera"), "choice", choices=["auto", "off"],
                  choice_labels={"auto": _t("station_camera_auto", "Automatic"), "off": _t("station_camera_off", "Off")}),
            _spec("panel_flash", _t("station_setting_panel_flash", "Flash panel firmware automatically"), "switch"),
            _spec("auto_ota", _t("station_setting_auto_ota", "Allow remote OTA updates"), "switch")]
    return out


_KEYS = {
    "beep": "beep",
    "speech": "speech",
    "voice": "tts",
    "volume": "speaker_volume",
    "panel_swap": "panel_swap",
    "flow_timeout": "flow_timeout",
    "recognise_timeout": "recognise_timeout",
    "min_grams": "min_grams",
    "camera": "camera",
    "panel_flash": "panel_flash",
    "auto_ota": "auto_ota",
}

_DEFAULTS = {
    "min_grams": getattr(st, "MIN_GRAMS", 20),
    "panel_flash": True,
    "auto_ota": True,
    "camera": "auto",
    "flow_timeout": getattr(st, "FLOW_TIMEOUT", 600),
    "recognise_timeout": getattr(st, "RECOGNISE_TIMEOUT", 90),
}


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
    raise SettingsError(_t("station_settings_bad_value", "{id}: not a value this setting takes", id=spec["id"]))


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
            raise SettingsError(_t("station_settings_save_failed", "the settings could not be saved ({reason})",
                                   reason=str(e.strerror or e))) from e

    def _remove(self) -> None:
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        except OSError as e:
            raise SettingsError(_t("station_settings_remove_failed",
                                   "the Weigh Station's own values could not be removed ({reason})",
                                   reason=str(e.strerror or e))) from e

    # ---- values
    def _installed(self, id_):
        if id_.startswith("lamp."):
            return flow.lamp_default(id_[5:])
        key = _KEYS[id_]
        if key in self.installed:
            return self.installed[key]
        return _DEFAULTS[id_]

    def current(self, id_):
        return self.values[id_] if id_ in self.values else self._installed(id_)

    def listing(self) -> list[dict]:
        """`[{id, label, kind, value, choices?, min?, max?}]`: the contract's answer of GET /settings."""
        with self._lock:
            return [self._listed(s) for s in specs()]

    def _listed(self, s: dict) -> dict:
        """One setting of the list. Speak and Volume show the Hub's value, read-only with a note, once it sent voice values."""
        if s["id"] in HUB_VOICE_IDS and st.live:
            return dict(s, value=st.voice_value(s["id"]), readonly=True, note=st.VOICE_TEXTS["set_on_hub_note"])
        return dict(s, value=self.current(s["id"]))

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
            raise SettingsError(_t("station_settings_none", "nothing to change"))
        if st.live and any(k in body for k in HUB_VOICE_IDS):
            raise SetOnHub()
        wanted = {s["id"]: s for s in specs()}
        new = {}
        for k, v in body.items():
            if k not in wanted:
                raise SettingsError(_t("station_settings_no_such", "{id}: no such setting", id=str(k)[:40]))
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
    if "recognise_timeout" in ids:
        flow._api["api"] = None
