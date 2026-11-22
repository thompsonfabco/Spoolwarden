"""Settings and the shared state of the station's threads (reader, scale, panel, intake flow, HTTP).

Settings come from the environment (the systemd drop-in written by `spoolwarden modules apply`); the station never
reads /etc/filament-kit. The other station modules import this one and use these objects; nothing else holds state.
"""
from __future__ import annotations

import glob
import os
import re
import threading
from pathlib import Path

from . import page as _station_page
from . import voice


def volume_from_env() -> int:
    """STATION_SPEAKER_VOLUME clamped to 0-100; 50 when unset or not a number."""
    try:
        return max(0, min(100, int(os.environ.get("STATION_SPEAKER_VOLUME", "50"))))
    except ValueError:
        return 50


# the live voice values (R88 2.5): the ranges the station takes, and where each one sits in `settings`
VOICE_RANGES = {"speaker": (0, 903), "speed": (50, 200), "volume": (0, 100)}
VOICE_SPEED_STEP = 10      # the speed goes in steps of 10 (R88 2.5): the station checks it again
VOICE_KEYS = {"speech": "speech", "speaker": "voice_speaker", "speed": "voice_speed", "volume": "speaker_volume"}
# the sentences of the voice routes' refusals (the string catalogue's, the English as the fallback: RC9 / RG1 add the keys)
_t = _station_page.catalogue_text
VOICE_TEXTS = {
    "bad_speech": _t("station_voice_bad_speech", "speech must be true or false."),
    "bad_speaker": _t("station_voice_bad_speaker", "speaker must be a whole number from 0 to 903."),
    "bad_speed": _t("station_voice_bad_speed", "speed must be a whole number from 50 to 200, in steps of 10."),
    "bad_volume": _t("station_voice_bad_volume", "volume must be a whole number from 0 to 100."),
    "busy": _t("station_voice_busy", "The Weigh Station is in use. Try again when it is idle."),
    "off": _t("station_voice_off", "Speaking is switched off."),
    # once the Hub has sent voice values the station page's Speak and Volume are read-only: the line beside them, and the
    # sentence of the 409 `set_on_hub` that answers a change of either
    "set_on_hub_note": _t("station_voice_set_on_hub_note",
                          "Speaking and volume are set on the Hub: Settings > Weigh Station voice."),
    "set_on_hub": _t("station_voice_set_on_hub", "Speaking and volume are set on the Hub."),
}


def settings_from_env() -> dict:
    return {
        "port": int(os.environ.get("STATION_PORT", "5003")),
        # one space-separated value in the drop-in; localhost unless apply adds the server's address
        "allow": set(os.environ.get("STATION_ALLOW", "127.0.0.1").split()),
        "nfc_device": os.environ.get("NFC_DEVICE", "auto"),  # auto: the ttyUSB port that isn't the button panel
        "beep": os.environ.get("STATION_BEEP", "1") != "0",
        "beep_device": os.environ.get("STATION_BEEP_DEVICE", ""),
        "speech": os.environ.get("STATION_SPEECH", "1") != "0",  # the panel talks (Piper or espeak-ng, + ffmpeg)
        # auto: Piper when its voice model is installed, else espeak-ng; espeak: always espeak-ng
        "tts": os.environ.get("STATION_TTS", "auto"),
        "piper_voice": os.environ.get("STATION_PIPER_VOICE", voice.model_path()),
        "camera": os.environ.get("STATION_CAMERA", "auto"),  # the new spool's picture: auto | /dev/videoN | off
        # the bundled panel firmware (a VERSION file beside it); a panel on another version is reflashed
        "panel_fw": os.environ.get("STATION_PANEL_FW", "/opt/filament-kit/resources/panel/intake-panel.bin"),
        "panel_flash": os.environ.get("STATION_PANEL_FLASH", "1") != "0",
        # the panel is wired the other way round (yellow on D2/D6, red on D1/D5): swap the buttons and lamps
        "panel_swap": os.environ.get("STATION_PANEL_SWAP", "0") == "1",
        "speaker_volume": volume_from_env(),  # the C3 panel's speaker, percent
        # the voice's speaker number (Piper's many-speaker voice only) and speaking speed in percent: only the Hub sets
        # these (`live`, R88 2.5), so no drop-in key reads them
        "voice_speaker": 0,
        "voice_speed": 100,
        # the intake flow: no progress this long and it goes back to idle; a recognition (local AI, then Claude)
        # may take this long. The server's address and token come from the pairing files (server_api).
        "flow_timeout": int(os.environ.get("STATION_FLOW_TIMEOUT", "600")),
        "recognise_timeout": int(os.environ.get("STATION_RECOGNISE_TIMEOUT", "90")),
    }


settings = settings_from_env()
# the lamp the owner chose for a step of the intake flow on the station's page (settings.py): {step: "yellow" | "red" |
# "both" | "off"}; a step with no entry keeps the lamps flow.STEP_LAMPS gives it
lamp_map: dict[str, str] = {}

# The voice values the Hub handed over (`POST /voice`, the hello answer): kept in memory over the drop-in's values in
# `settings`, so a change needs no restart and the station page's own store (settings.py) cannot undo it
live: dict = {}


def voice_value(name: str):
    """The voice value `name` (speech, speaker, speed or volume): the live one, else the drop-in's."""
    return live[name] if name in live else settings[VOICE_KEYS[name]]


def voice_values() -> dict:
    return {name: voice_value(name) for name in VOICE_KEYS}


def clean_voice(body: dict) -> tuple[dict, dict]:
    """`(values, problems)` of a voice request: the values that are valid (a whole number inside its range; speech a
    true/false) and `{name: sentence}` for each that is not. Names the station does not know are left out."""
    values, problems = {}, {}
    for name in VOICE_KEYS:
        if name not in body:
            continue
        v = body[name]
        if name == "speech":
            ok = isinstance(v, bool)
        else:
            ok = isinstance(v, int) and not isinstance(v, bool) and VOICE_RANGES[name][0] <= v <= VOICE_RANGES[name][1]
            ok = ok and (name != "speed" or v % VOICE_SPEED_STEP == 0)
        if ok:
            values[name] = v
        else:
            problems[name] = VOICE_TEXTS["bad_" + name]
    return values, problems


MIN_GRAMS = 20  # an empty pan reads 0; anything under this isn't a spool

lock = threading.Lock()
state = {"tag": None, "tag_at": None, "scale": None, "scale_at": None, "last": None, "last_at": None,
         "held": None, "held_at": None, "blink_until": 0, "air": None, "air_at": None,
         "photo": None, "photo_at": None}
# the intake flow (flow.py): its step, attempt number, the linked spool or recognised filament, the server's display
# lines for the step and the station's own message
flow = {"step": "idle", "seq": 0, "spool": None, "filament": None, "display": [], "message": "", "at": 0.0}
# serial ports: the panel's (found by its HELLO) and the one nfcpy uses; neither probes the other's
ports = {"panel": None, "panel_version": None, "panel_chip": None, "panel_nfc": None, "nfc": None}
flashed: set[str] = set()  # bundled firmware versions already written this run
probed = threading.Event()  # set once the panel search has looked at every port, so nfcpy picks the right one
panel_lock = threading.Lock()
panel = {"ser": None}
panel_dirty = threading.Event()
panel_resend = threading.Event()  # the panel said HELLO on its own (it reset): refresh sends every row and lamp again


def usb_ttys() -> list[str]:
    return _by_number(glob.glob("/dev/ttyUSB*"))


SYSFS_TTY = "/sys/class/tty"
PANEL_VENDOR = "303a"  # Espressif: the ESP32-C3's own USB serial


def _by_number(paths) -> list[str]:
    return sorted(paths, key=lambda p: int(re.sub(r"\D", "", p) or 0))


def usb_vendor(dev: str) -> str | None:
    """The USB vendor ID (lowercase hex) of the device behind a tty, or None when sysfs doesn't say."""
    try:
        iface = os.path.realpath(os.path.join(SYSFS_TTY, os.path.basename(dev), "device"))  # the USB interface
        with open(os.path.join(os.path.dirname(iface), "idVendor")) as f:  # its parent is the USB device
            return f.read().strip().lower() or None
    except OSError:
        return None


def panel_ttys() -> list[str]:
    """The ports that may hold the button panel: ttyACM ports of an Espressif USB device (other ttyACM boards are
    never opened), then the ttyUSB ports."""
    acm = [d for d in glob.glob("/dev/ttyACM*") if usb_vendor(d) == PANEL_VENDOR]
    return _by_number(acm) + usb_ttys()


def state_dir() -> Path:
    """The station's own state folder (systemd's StateDirectory, owned by its service user): its certificate and key
    (`tls/`) and its settings file (`settings.xml`)."""
    return Path(os.environ.get("STATE_DIRECTORY", "/var/lib/filament-kit-station").split(":")[0])


def tls_dir() -> Path:
    """`tls/` in the station's own state folder: its certificate and key, made at the first start."""
    return state_dir() / "tls"


def ensure_cert(folder: Path | None = None) -> tuple[Path, Path, str]:
    """(certificate file, key file, SHA-256 fingerprint) of the station's own certificate, made with the Hub's
    function (`pairing.make_cert`: self-signed, 10 years, this host's names and addresses; key.pem 0600) when there is
    none or the one there cannot be read. Raises OSError / ValueError when none can be made."""
    from .. import pairing
    d = folder or tls_dir()
    cert, key = d / "cert.pem", d / "key.pem"
    try:
        if key.is_file():
            return cert, key, pairing.fingerprint(cert.read_bytes())
    except (OSError, ValueError):
        pass
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    fp = pairing.make_cert(pairing.short_hostname(), pairing.local_ips(), d)
    return cert, key, fp


def view() -> dict:
    """A snapshot for the HTTP handler: the state plus the panel's port and firmware version and the flow."""
    with lock:
        return dict(state, panel=ports["panel"], panel_version=ports["panel_version"], panel_nfc=ports["panel_nfc"],
                    flow={k: flow[k] for k in ("step", "message", "spool", "filament")})
