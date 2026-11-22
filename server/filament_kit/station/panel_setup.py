"""Setting up the wireless panel (R88 section 4.4): the plain functions under `spoolwarden station panel-wifi`.

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

# The two set-up lines the panel's firmware accepts over USB (firmware 12, WL2: not built yet, so this is the contract it
# is built against). Each is one line ending in a newline; the panel answers with `OK` or `ERR <reason>`.
# The name and password go as the hex of their UTF-8 bytes, so a space or any other character cannot break the line.
LINE_WIFI = "CFG WIFI {ssid_hex} {password_hex}"
LINE_LINK = "CFG LINK {address} {port} {fingerprint} {token}"
LINE_CLEAR = "CFG CLEAR"                  # `--off`: the panel forgets all of it (best effort; the token is gone anyway)
REPLY_OK = "OK"

TOKEN_FILE = "panel_link.xml"             # in the station's state folder: {token, created}; 0600, the station user's

# Plain constants (key, English text): RG1 puts them in the string catalogue
TEXT_DEVICES_USB = "Panel: on USB"                                          # key: check_panel_on_usb
TEXT_DEVICES_WIFI = "Panel: on Wi-Fi"                                       # key: check_panel_on_wifi
TEXT_NOT_CONNECTED = "The Weigh Station's panel is not connected."          # key: check_panel_not_connected
TEXT_NOT_CONNECTED_FIX = ("check the panel has power and is in Wi-Fi range; to go back to the USB cable: "
                          "sudo spoolwarden station panel-wifi --off")      # key: check_panel_not_connected_fix


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
        raise PanelSetupError("The Wi-Fi name must be 1 to 32 characters.")
    if password and not 8 <= len(password.encode("utf-8")) <= 63:
        raise PanelSetupError("The Wi-Fi password must be 8 to 63 characters, or empty for an open network.")
    if not address or re.search(r"\s", address):
        raise PanelSetupError("The Weigh Station's address must be one name or number with no spaces.")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise PanelSetupError("The port must be a number from 1 to 65535.")
    fp = re.sub(r"[\s:]", "", fingerprint or "").lower()
    if not re.fullmatch(r"[0-9a-f]{64}", fp):
        raise PanelSetupError("The certificate fingerprint must be 64 hex digits (SHA-256).")
    return fp


def setup_lines(*, ssid: str, password: str, address: str, port: int, fingerprint: str, token: str) -> list[str]:
    """The two set-up lines for the panel (the only place the password goes into text)."""
    return [LINE_WIFI.format(ssid_hex=_hex(ssid), password_hex=_hex(password) or "-"),
            LINE_LINK.format(address=address, port=port, fingerprint=fingerprint, token=token)]


def _send_all(send: Callable[[str], str], lines: list[str]) -> None:
    """Send each line; the panel must answer OK to every one. The refusal sentence never repeats a line."""
    for n, line in enumerate(lines, 1):
        try:
            reply = (send(line) or "").strip()
        except OSError:
            raise PanelSetupError("The panel did not answer over USB. Check the cable and try again.") from None
        if reply != REPLY_OK:
            raise PanelSetupError(f"The panel refused set-up line {n} of {len(lines)}"
                                  f"{': ' + reply[4:].strip() if reply.startswith('ERR ') and reply[4:].strip() else ''}.")


def token_path(folder: Path | None = None) -> Path:
    return (folder or st.state_dir()) / TOKEN_FILE


def store_token(token: str, folder: Path | None = None) -> None:
    """Keep the token in the station's state folder as XML, 0600, handed to the folder's owner (the station's user) when
    this runs as root; written whole through a temp file and a rename."""
    from ..profiles import statexml
    path = token_path(folder)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
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
    except (OSError, ValueError, statexml.StateXmlError):
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


def enable(send: Callable[[str], str], *, ssid: str, password: str, address: str, port: int, fingerprint: str,
           folder: Path | None = None) -> dict:
    """Set the panel up for Wi-Fi. `send` writes one line to the panel over USB and returns its answer line. With a new
    token the two set-up lines go to the panel; when it answers OK to both, the token is kept in the station's state and
    `station.panel_link` becomes `wifi` (with `station.panel_port`). Any refusal leaves the config and the token as they
    were. The caller then applies the config (`spoolwarden modules apply`), which writes the drop-in and restarts the
    station: the listener comes up. Returns `{"panel_link": "wifi", "panel_port": <port>}`: never the password."""
    fp = check_values(ssid=ssid, password=password, address=address, port=port, fingerprint=fingerprint)
    token = new_token()
    _send_all(send, setup_lines(ssid=ssid, password=password, address=address, port=port, fingerprint=fp, token=token))
    store_token(token, folder)
    _set_link("wifi", port)
    return {"panel_link": "wifi", "panel_port": port}


def disable(send: Callable[[str], str] | None = None, *, folder: Path | None = None) -> dict:
    """`--off`: `station.panel_link` back to `usb` and the token removed. With a panel on USB (`send`) it is told to forget
    its settings first; a panel that is not there or does not answer does not stop this (the token is gone, so it can no
    longer connect). The caller then applies the config."""
    forgot = False
    if send is not None:
        try:
            forgot = (send(LINE_CLEAR) or "").strip() == REPLY_OK
        except OSError:
            forgot = False
    forget_token(folder)
    _set_link("usb")
    return {"panel_link": "usb", "panel_forgotten": forgot}


def panel_rows(cfg: dict, station_state: dict | None, *, has_station: bool | None = None) -> Iterator:
    """The rows for the lists: under Devices "Panel: on USB" / "Panel: on Wi-Fi" (a note) when the Hub has a Weigh Station,
    and under Weigh Station "The Weigh Station's panel is not connected." (needs a look) when the link is `wifi`, the
    station answered (`station_state` is its `/state`; None when it did not) and it has no panel. `status.Row`s; the
    caller puts them in its list."""
    from ..status import FAIL, INFO, Row
    link = config.panel_link(cfg)
    if has_station is None:
        has_station = bool((cfg.get("station") or {}).get("url"))
    if has_station:
        yield Row(INFO, TEXT_DEVICES_WIFI if link == "wifi" else TEXT_DEVICES_USB, group="Devices", key="devices.panel-link")
    if link == "wifi" and station_state is not None and not station_state.get("panel"):
        yield Row(FAIL, TEXT_NOT_CONNECTED, TEXT_NOT_CONNECTED_FIX, group="Weigh Station", key="weigh-station.panel")
