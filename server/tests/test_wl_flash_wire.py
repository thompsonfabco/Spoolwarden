"""The station's flash path (panel.update_firmware / flash_panel) goes through panel_flash: a firmware 14 panel is
written program-only, an older or empty one whole, and the from-scratch notice appears only after a whole flash
while the link is Wi-Fi. subprocess is a fake; nothing is flashed."""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from filament_kit.station import panel, panel_flash as pf
from filament_kit.station import state as st


class Done:
    returncode, stdout, stderr = 0, b"", b""


class Ser:
    def __init__(self, version, chip="esp32c3"):
        self.port, self.panel_version, self.panel_chip, self.closed = "/dev/ttyACM1", version, chip, False

    def close(self):
        self.closed = True


@pytest.fixture
def fw(tmp_path, monkeypatch):
    (tmp_path / pf.WHOLE_IMAGE).write_bytes(b"\xe9whole")
    (tmp_path / pf.PROGRAM_IMAGE).write_bytes(b"\xe9program")
    (tmp_path / "VERSION").write_text("15\n")
    monkeypatch.setitem(st.settings, "panel_fw", str(tmp_path / pf.WHOLE_IMAGE))
    monkeypatch.setitem(st.settings, "panel_flash", True)
    monkeypatch.setitem(st.settings, "panel_link", "wifi")
    monkeypatch.setitem(st.ports, "nfc", None)
    monkeypatch.setattr(pf, "time", types.SimpleNamespace(sleep=lambda s: None))
    monkeypatch.setitem(st.state, "flash_notice", None)
    st.flashed.clear()
    calls = []
    monkeypatch.setattr(pf.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or Done())
    yield calls
    st.flashed.clear()


def _written(cmd):
    i = cmd.index("write-flash")
    return cmd[i + 1], Path(cmd[i + 2]).name


def test_a_14_panel_is_flashed_program_only(fw):
    assert panel.update_firmware(Ser("14")) is True
    assert [_written(c) for c in fw] == [("0x10000", pf.PROGRAM_IMAGE)]
    assert st.state["flash_notice"] is None


@pytest.mark.parametrize("have", ["13", "12", "?", ""])
def test_an_older_or_unreadable_panel_is_flashed_whole_with_the_notice(fw, have):
    assert panel.update_firmware(Ser(have)) is True
    assert [_written(c) for c in fw] == [("0x0", pf.WHOLE_IMAGE)]
    assert "panel-wifi" in st.state["flash_notice"]


def test_no_notice_for_a_whole_flash_on_usb(fw, monkeypatch):
    monkeypatch.setitem(st.settings, "panel_link", "usb")
    panel.update_firmware(Ser("12"))
    assert _written(fw[0]) == ("0x0", pf.WHOLE_IMAGE) and st.state["flash_notice"] is None


def test_no_notice_when_the_write_failed(fw, monkeypatch):
    Done.returncode = 1
    try:
        panel.update_firmware(Ser("12"))
    finally:
        Done.returncode = 0
    assert st.state["flash_notice"] is None


def test_flash_panel_result_carries_the_notice_and_flash_panel_stays_a_bool(fw):
    r = panel.flash_panel_result("/dev/ttyACM1", "15", None)
    assert r.ok and r.whole and r.notice and st.state["flash_notice"] == r.notice
    assert panel.flash_panel("/dev/ttyACM1", "15") is True
    assert _written(fw[-1]) == ("0x0", pf.WHOLE_IMAGE)       # a blank board (cli): whole


# ---- the notice in the station's GET /state view (st.view), cleared by a new flash or a Wi-Fi panel ----

@pytest.fixture
def notice_state(monkeypatch):
    monkeypatch.setitem(st.notice_link, "ser", None)
    monkeypatch.setitem(st.panel, "ser", None)


def test_the_view_carries_the_notice_after_a_whole_flash_while_wifi(fw, notice_state):
    assert st.view()["flash_notice"] is None
    panel.update_firmware(Ser("12"))
    notice = st.view()["flash_notice"]
    assert isinstance(notice, str) and "panel-wifi" in notice
    assert st.view()["flash_notice"] == notice          # reading it does not use it up


def test_the_next_flash_start_clears_the_notice(fw, notice_state, monkeypatch):
    panel.flash_panel_result("/dev/ttyACM1", "15", None)
    assert st.view()["flash_notice"]
    seen = []
    real = pf.flash
    monkeypatch.setattr(pf, "flash", lambda *a: seen.append(st.view()["flash_notice"]) or real(*a))
    panel.flash_panel_result("/dev/ttyACM1", "15", "14")       # a program-only flash: no new notice
    assert seen == [None] and st.view()["flash_notice"] is None


def test_a_wifi_hello_clears_the_notice(fw, notice_state, monkeypatch):
    panel.flash_panel_result("/dev/ttyACM1", "15", None)
    assert st.view()["flash_notice"]
    monkeypatch.setitem(st.panel, "ser", types.SimpleNamespace(wifi=True))    # what panel_net.admit puts there
    assert st.view()["panel"] == "wifi" and st.view()["flash_notice"] is None
    assert st.state["flash_notice"] is None


def test_a_usb_panel_does_not_clear_the_notice(fw, notice_state, monkeypatch):
    panel.flash_panel_result("/dev/ttyACM1", "15", None)
    monkeypatch.setitem(st.panel, "ser", types.SimpleNamespace(wifi=False))
    assert st.view()["flash_notice"]


def test_a_wifi_link_that_was_there_during_the_flash_does_not_clear_it(fw, notice_state, monkeypatch):
    old = types.SimpleNamespace(wifi=True)
    monkeypatch.setitem(st.panel, "ser", old)
    panel.flash_panel_result("/dev/ttyACM1", "15", None)
    assert st.view()["flash_notice"]
    monkeypatch.setitem(st.panel, "ser", types.SimpleNamespace(wifi=True))    # a new Wi-Fi session after it
    assert st.view()["flash_notice"] is None
