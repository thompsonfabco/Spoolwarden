"""The two halves of the panel's Wi-Fi link together (R88 section 4): `panel_setup` (WL4) writes the token, `panel_net`
(WL1) reads it, and the Devices and Checks rows read what the station's /state says. The fake panel is WL1's."""
from __future__ import annotations

import re

import pytest

from filament_kit import config, status
from filament_kit.station import panel_net, panel_setup as ps
from filament_kit.station import state as st
from test_wl1_panel_link import FakePanel, connect, link, net, station  # noqa: F401  (the fixture is autouse there)

WIN = "C:" + chr(92) + "state"      # a Windows drive path
SETUP = dict(ssid="Workshop net", password="hunter2-wifi-pass", address="127.0.0.1", port=5004)


@pytest.fixture(autouse=True)
def etc(tmp_path, monkeypatch):
    (tmp_path / "etc").mkdir()
    monkeypatch.setenv("FK_ETC", str(tmp_path / "etc"))


def set_up():
    """`panel-wifi` against a stub panel over USB: returns the token that went down the cable."""
    lines = []

    def send(line):
        lines.append(line)
        return ps.REPLY_OK

    ps.enable(send, fingerprint=net.fp, **SETUP)
    return re.fullmatch(r"CFG LINK \S+ \d+ [0-9a-f]{64} ([0-9a-f]{32})", lines[1]).group(1)


def rows():
    cfg = config.load()
    return [(r.group, r.text, r.state) for r in ps.panel_rows(cfg, st.view(), has_station=True)]


def test_the_token_set_up_writes_is_the_one_the_station_accepts():
    token = set_up()
    assert panel_net.read_token() == token and ps.stored_token() == token
    p = connect(token=token)
    assert link() is not None and st.view()["panel"] == "wifi"
    assert p.sock is not None


def test_the_rows_follow_the_state():
    token = set_up()
    assert ("Weigh Station", "The Weigh Station's panel is not connected.", status.FAIL) in rows()
    connect(token=token)
    assert rows() == [("Devices", "Panel: on Wi-Fi", status.INFO)]


def test_a_wrong_token_is_refused_and_a_new_set_up_retires_the_old_token():
    old = set_up()
    new = set_up()
    assert new != old
    p = FakePanel(net.port).hello(token=old)
    assert p.line() == b"REFUSED token" and link() is None and st.view()["panel"] is None
    connect(token=new)


def test_off_removes_the_token_and_the_panel_is_refused():
    token = set_up()
    ps.disable(None)
    assert panel_net.read_token() is None and not (ps.token_path()).exists()
    p = FakePanel(net.port).hello(token=token)
    assert p.line() == b"REFUSED token" and link() is None
    assert config.load()["station"]["panel_link"] == "usb"
    assert rows() == [("Devices", "Panel: on USB", status.INFO)]


@pytest.mark.parametrize("value, folder", [
    ("/var/lib/x", "/var/lib/x"),
    ("/var/lib/x:/var/lib/y", "/var/lib/x"),
    (WIN, WIN),
    ("C:/state:D:/other", "C:/state"),
    (WIN + ":/var/lib/y", WIN),
])
def test_the_state_folder_takes_a_drive_path_and_still_splits_a_list(monkeypatch, value, folder):
    monkeypatch.setenv("STATE_DIRECTORY", value)
    assert str(st.state_dir()) == str(type(st.state_dir())(folder))
