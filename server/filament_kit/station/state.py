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

from . import voice


def volume_from_env() -> int:
    """STATION_SPEAKER_VOLUME clamped to 0-100; 50 when unset or not a number."""
    try:
        return max(0, min(100, int(os.environ.get("STATION_SPEAKER_VOLUME", "50"))))
    except ValueError:
        return 50


def int_from_env(name: str, default: int, low: int, high: int) -> int:
    """The environment variable `name` clamped to low-high; `default` when unset or not a number."""
    try:
        return max(low, min(high, int(os.environ.get(name, str(default)))))
    except ValueError:
        return default


# the live voice values (R88 2.5): the ranges the station takes, and where each one sits in `settings`
VOICE_RANGES = {"speaker": (0, 903), "speed": (50, 200), "volume": (0, 100)}
VOICE_KEYS = {"speech": "speech", "speaker": "voice_speaker", "speed": "voice_speed", "volume": "speaker_volume"}
# the sentences of the voice routes' refusals (plain constants; the string catalogue takes them later, RC9 / RG1)
VOICE_TEXTS = {
    "bad_speech": "speech must be true or false.",
    "bad_speaker": "speaker must be a whole number from 0 to 903.",
    "bad_speed": "speed must be a whole number from 50 to 200.",
    "bad_volume": "volume must be a whole number from 0 to 100.",
    "busy": "The Weigh Station is in use. Try again when it is idle.",
    "off": "Speaking is switched off.",
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
        # the panel's carrier (R88 4.3): usb (default) or wifi, which turns the TLS listener on `panel_port` on (WL4 writes both)
        "panel_link": "wifi" if os.environ.get("STATION_PANEL_LINK", "usb") == "wifi" else "usb",
        "panel_port": int_from_env("STATION_PANEL_PORT", 5004, 1, 65535),
        "speaker_volume": volume_from_env(),  # the C3 panel's speaker, percent
        # the voice: Piper's speaker number (the many-speaker voice only) and the speaking speed in percent
        "voice_speaker": int_from_env("STATION_VOICE_SPEAKER", 0, *VOICE_RANGES["speaker"]),
        "voice_speed": int_from_env("STATION_VOICE_SPEED", 100, *VOICE_RANGES["speed"]),
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
panel_scale = {"on": False}  # state["scale"] holds a reading the panel sent (`W` lines), not the computer's own scale


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
    value = os.environ.get("STATE_DIRECTORY", "/var/lib/filament-kit-station")
    end = value.find(":", 2 if re.match(r"[A-Za-z]:[\\/]", value) else 0)      # systemd joins folders with ":"; a drive's is not one
    return Path(value if end < 0 else value[:end])


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


def panel_kind() -> str | None:
    """How the panel is connected now: "usb", "wifi" (R88 4.3) or None when there is none."""
    ser = panel["ser"]
    return None if not ser else "wifi" if getattr(ser, "wifi", False) else "usb"


def view() -> dict:
    """A snapshot for the HTTP handler: the state plus how the panel is connected ("usb", "wifi" or None), its
    firmware version and the flow."""
    with lock:
        return dict(state, panel=panel_kind(), panel_version=ports["panel_version"], panel_nfc=ports["panel_nfc"],
                    flow={k: flow[k] for k in ("step", "message", "spool", "filament")})
