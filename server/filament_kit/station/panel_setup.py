"""Setting up the wireless panel (R88 section 4.4): the plain functions under `spoolwarden weigh-station panel-wifi`.

Over USB, once, the caller hands in the Wi-Fi name and password and the station's address, port and certificate
fingerprint; `enable` makes a new token, writes the two set-up lines to the panel, keeps the token in the station's state
folder and switches `station.panel_link` to `wifi`. `disable` (`--off`) reverses it. These functions ask nothing and print
nothing: the command line owns the questions and the words.

The Wi-Fi password lives in the panel only. It is never put in the kit config, the state folder, an exception or a log:
it appears in `setup_lines` (what goes down the USB cable) and nowhere else.

Also here: the rows for the Devices and Checks lists (`panel_rows`), which `status.py` adds to its list.
"""
from __future__ import annotations

import os
import re
import secrets
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterator

from .. import config
from . import state as st

# The two set-up lines the panel's firmware accepts over USB (firmware 13 and later, WL2: the contract it is
# built against). Each is one line ending in a newline; the panel answers with `OK` or `ERR <reason>`.
# The name and password go as the hex of their UTF-8 bytes, so a space or any other character cannot break the line.
LINE_WIFI = "CFG WIFI {ssid_hex} {password_hex}"
LINE_LINK = "CFG LINK {address} {port} {fingerprint} {token}"
LINE_CLEAR = "CFG CLEAR"                  # `--off`: the panel forgets all of it (best effort; the token is gone anyway)
REPLY_OK = "OK"
REPLY_ERR = "ERR"
MIN_FIRMWARE = 13                         # the first panel firmware with Wi-Fi (12 is the shipped USB-only one)
MAX_ADDRESS = 64                          # characters; the panel's line holds 256 and answers `ERR long`, never truncates
MAX_UNASKED = 50                          # unasked lines (AIR, P, ...) skipped while waiting for OK or ERR
# the panel's own lines that may arrive between a set-up line and its answer
UNASKED = re.compile(r"(AIR|P|L|TAG|NFC|HELLO)(?![A-Za-z0-9])")

TOKEN_FILE = "panel_link.xml"             # in the station's state folder: {token, created}; 0600, the station user's

# Sentences: each resolves from the string catalogue by its key (the English text is the fallback until RG1 adds the key)
def _t(key: str, text: str) -> str:
    from .page import catalogue_text
    return catalogue_text(key, text)


TEXT_DEVICES_USB = _t("check_panel_on_usb", "Panel: on USB")
TEXT_DEVICES_WIFI = _t("check_panel_on_wifi", "Panel: on Wi-Fi")
TEXT_SSID = _t("panel_setup_ssid", "The Wi-Fi name must be 1 to 32 characters.")
TEXT_PASSWORD = _t("panel_setup_password", "The Wi-Fi password must be 8 to 63 characters, or empty for an open network.")
TEXT_KEY_PASSWORD = _t("panel_setup_key_password", "A 64-digit key cannot be used. Type the Wi-Fi password instead.")
TEXT_ADDRESS = _t("panel_setup_address", "The Weigh Station's address must be one name or number with no spaces.")
TEXT_ADDRESS_LONG = _t("panel_setup_address_long", f"The address is too long ({MAX_ADDRESS} characters at most).")
TEXT_PORT = _t("panel_setup_port", "The port must be a number from 1 to 65535.")
TEXT_PORT_SAME = _t("panel_setup_port_same", "The panel's port must differ from the Weigh Station's own port ({port}).")
TEXT_FINGERPRINT = _t("panel_setup_fingerprint", "The certificate fingerprint must be 64 hex digits (SHA-256).")
TEXT_FIRMWARE_OLD = _t("panel_setup_firmware_old", "This panel's firmware is too old for Wi-Fi. "
                       "Flash it first: sudo spoolwarden weigh-station flash-display")
TEXT_NO_ANSWER_USB = _t("panel_setup_no_answer_usb", "The panel did not answer over USB. Check the cable and try again.")
TEXT_NOT_ACCEPTED = _t("panel_setup_not_accepted", "The panel did not accept the Wi-Fi settings.")
TEXT_TIMEOUT = _t("panel_setup_timeout", "The panel did not answer in time.")
TEXT_NOT_CONNECTED = _t("check_panel_not_connected", "The Weigh Station's panel is not connected.")
TEXT_NOT_CONNECTED_FIX = _t("check_panel_not_connected_fix", "check the panel has power and is in Wi-Fi range; to go back to "
                            "the USB cable: sudo spoolwarden weigh-station panel-wifi --off")



class PanelSetupError(ValueError):
    """A set-up that cannot go on: a value the panel cannot take, or a panel that did not answer. The message is one
    sentence that never holds the password."""


def new_token() -> str:
    """A new panel token: 32 hex digits from the system's random source."""
    return secrets.token_hex(16)


def _hex(text: str) -> str:
    return text.encode("utf-8").hex()


def check_values(*, ssid: str, password: str, address: str, port: int, fingerprint: str) -> str:
    """The fingerprint without separators, in lower case; PanelSetupError for a value the panel cannot take. The Wi-Fi
    name is 1 to 32 bytes, the password empty (an open network) or 8 to 63 bytes (WPA)."""
    if not ssid or len(ssid.encode("utf-8")) > 32:
        raise PanelSetupError(TEXT_SSID)
    if len(password.encode("utf-8")) == 64:
        raise PanelSetupError(TEXT_KEY_PASSWORD)
    if password and not 8 <= len(password.encode("utf-8")) <= 63:
        raise PanelSetupError(TEXT_PASSWORD)
    if len(address) > MAX_ADDRESS:
        raise PanelSetupError(TEXT_ADDRESS_LONG)
    if not address or re.search(r"\s", address):
        raise PanelSetupError(TEXT_ADDRESS)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise PanelSetupError(TEXT_PORT)
    fp = re.sub(r"[\s:]", "", fingerprint or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fp):
        raise PanelSetupError(TEXT_FINGERPRINT)
    return fp


def setup_lines(*, ssid: str, password: str, address: str, port: int, fingerprint: str, token: str) -> list[str]:
    """The two set-up lines for the panel (the only place the password goes into text)."""
    return [LINE_WIFI.format(ssid_hex=_hex(ssid), password_hex=_hex(password) or "-"),
            LINE_LINK.format(address=address, port=port, fingerprint=fingerprint, token=token)]


def check_version(version: str | None) -> None:
    """PanelSetupError unless the panel's firmware version (its HELLO's, as the caller read it) is 13 or later."""
    m = re.match(r"\d+", str(version or "").strip())
    if not m or int(m.group()) < MIN_FIRMWARE:
        raise PanelSetupError(TEXT_FIRMWARE_OLD)


def _answer(send: Callable[[str | None], str], line: str | None) -> None:
    """Send `line` (None sends nothing, it only reads) and read until the panel says OK or ERR, skipping its unasked
    lines. `send` returns the next line, "" when none came in 5 s. No sentence repeats a line or a reply."""
    for _ in range(MAX_UNASKED):
        try:
            reply = (send(line) or "").strip()
        except OSError:
            raise PanelSetupError(TEXT_NO_ANSWER_USB) from None
        line = None
        if not reply:
            raise PanelSetupError(TEXT_TIMEOUT)
        if reply.split(None, 1)[0] == REPLY_OK:
            return
        if not UNASKED.match(reply):
            raise PanelSetupError(TEXT_NOT_ACCEPTED)        # ERR <reason>, or a line that is not the panel's
    raise PanelSetupError(TEXT_TIMEOUT)


def _send_all(send: Callable[[str | None], str], lines: list[str]) -> None:
    for line in lines:
        _answer(send, line)


def token_path(folder: Path | None = None) -> Path:
    return (folder or st.state_dir()) / TOKEN_FILE


def _set_aside(path: Path) -> None:
    """A panel_link.xml that cannot be read is kept as `panel_link.xml.unreadable` and noted, never written over (as
    voice.xml)."""
    from ..profiles import statexml
    try:
        raw = path.read_bytes()
    except OSError:
        return
    try:
        statexml.loads(raw, "panel-link")
    except (ValueError, statexml.StateXmlError) as e:
        kept = path.with_name(path.name + ".unreadable")
        os.replace(path, kept)
        statexml.note_problem(kept, f"{path.name} could not be read and is kept aside ({e})")


def store_token(token: str, folder: Path | None = None) -> None:
    """Keep the token in the station's state folder as XML, 0600, handed to the folder's owner (the station's user) when
    this runs as root; written whole through a temp file and a rename."""
    from ..profiles import statexml
    path = token_path(folder)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    _set_aside(path)
    data = statexml.dumps("panel-link", {"token": token, "created": datetime.now().astimezone().isoformat(timespec="seconds")})
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
            if hasattr(os, "fchmod"):
                os.fchmod(fh.fileno(), 0o600)
            if getattr(os, "geteuid", lambda: -1)() == 0:
                owner = os.stat(path.parent)
                os.fchown(fh.fileno(), owner.st_uid, owner.st_gid)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def stored_token(folder: Path | None = None) -> str | None:
    """The token kept by `store_token`, or None when there is none or it cannot be read."""
    from ..profiles import statexml
    try:
        tok = statexml.loads(token_path(folder).read_bytes(), "panel-link").get("token")
    except FileNotFoundError:
        return None
    except (OSError, ValueError, statexml.StateXmlError) as e:
        statexml.note_problem(token_path(folder), f"{TOKEN_FILE} could not be read ({e})")
        return None
    return tok if isinstance(tok, str) and tok else None


def forget_token(folder: Path | None = None) -> None:
    try:
        token_path(folder).unlink()
    except FileNotFoundError:
        pass


def _set_link(link: str, port: int | None = None) -> None:
    """station.panel_link (and the port) through the config's own save path, which writes both forms."""
    cfg = config.load_raw()
    station = cfg.get("station")
    station = dict(station) if isinstance(station, dict) else {}
    station["panel_link"] = link
    if port is not None:
        station["panel_port"] = port
    config.save({**cfg, "station": station})


def enable(send: Callable[[str | None], str], *, version: str | None, ssid: str, password: str, address: str, port: int, fingerprint: str,
           folder: Path | None = None) -> dict:
    """Set the panel up for Wi-Fi. `version` is the panel's firmware version (13 or later). `send(line)` writes one line to
    the panel over USB (None: writes nothing) and returns the next line the panel sends, "" after 5 s. With a new
    token the two set-up lines go to the panel; when it answers OK to both, the token is kept in the station's state and
    `station.panel_link` becomes `wifi` (with `station.panel_port`). Any refusal leaves the config and the token as they
    were. The caller then applies the config (`spoolwarden modules apply`), which writes the drop-in and restarts the
    station: the listener comes up. Returns `{"panel_link": "wifi", "panel_port": <port>}`: never the password."""
    check_version(version)
    station_port = int(config._merge(config.DEFAULTS["station"], config.load_raw().get("station") or {})["port"])
    if port == station_port:
        raise PanelSetupError(TEXT_PORT_SAME.format(port=station_port))
    fp = check_values(ssid=ssid, password=password, address=address, port=port, fingerprint=fingerprint)
    token = new_token()
    _send_all(send, setup_lines(ssid=ssid, password=password, address=address, port=port, fingerprint=fp, token=token))
    store_token(token, folder)
    _set_link("wifi", port)
    return {"panel_link": "wifi", "panel_port": port}


def disable(send: Callable[[str | None], str] | None = None, *, folder: Path | None = None) -> dict:
    """`--off`: `station.panel_link` back to `usb` and the token removed. With a panel on USB (`send`) it is told to forget
    its settings first; a panel that is not there or does not answer does not stop this (the token is gone, so it can no
    longer connect). The caller then applies the config."""
    forgot = False
    if send is not None:
        try:
            _answer(send, LINE_CLEAR)
            forgot = True
        except (OSError, PanelSetupError):
            forgot = False
    forget_token(folder)
    _set_link("usb")
    return {"panel_link": "usb", "panel_forgotten": forgot}


def panel_rows(cfg: dict, station_state: dict | None, *, has_station: bool | None = None) -> Iterator:
    """The rows for the lists: under Devices "Panel: on USB" / "Panel: on Wi-Fi" (a note) when the Hub has a Weigh Station
    and its `/state` `panel` says so (nothing when it does not answer or has no panel), and under Weigh Station "The Weigh Station's panel is not connected." (needs a look) when the link is `wifi`, the
    station answered (`station_state` is its `/state`; None when it did not) and it has no panel. `status.Row`s; the
    caller puts them in its list."""
    from ..status import FAIL, INFO, Row
    link = config.panel_link(cfg)
    if has_station is None:
        has_station = bool((cfg.get("station") or {}).get("url"))
    panel = (station_state or {}).get("panel")      # the station's own word (usb | wifi | null), nothing when it did not answer
    if has_station and panel in ("usb", "wifi"):
        yield Row(INFO, TEXT_DEVICES_WIFI if panel == "wifi" else TEXT_DEVICES_USB, group="Devices", key="devices.panel-link")
    if link == "wifi" and station_state is not None and not station_state.get("panel"):
        yield Row(FAIL, TEXT_NOT_CONNECTED, TEXT_NOT_CONNECTED_FIX, group="Weigh Station", key="weigh-station.panel")
