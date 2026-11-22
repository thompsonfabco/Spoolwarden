"""The panel flash picks its image: a panel on firmware 14 or later keeps its Wi-Fi (program image at 0x10000), an
old, empty or re-laid-out one gets the whole image at 0. subprocess is a fake; nothing is flashed."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from filament_kit.station import panel_flash as pf
from filament_kit.station import state as st

FW = Path(__file__).resolve().parents[2] / "station" / "panel" / "firmware"


class Done:
    returncode, stdout, stderr = 0, b"", b""


@pytest.fixture(autouse=True)
def settings_stay_as_they_were():
    """A test here must leave st.settings exactly as it found it (a hand-made key would leak into later tests)."""
    before = dict(st.settings)
    yield
    assert st.settings == before


@pytest.fixture
def fw(tmp_path, monkeypatch):
    (tmp_path / pf.WHOLE_IMAGE).write_bytes(b"\xe9whole")
    (tmp_path / pf.PROGRAM_IMAGE).write_bytes(b"\xe9program")
    monkeypatch.setitem(st.settings, "panel_fw", str(tmp_path / pf.WHOLE_IMAGE))
    monkeypatch.setitem(st.ports, "nfc", None)
    monkeypatch.setattr(pf.time, "sleep", lambda s: None)
    calls = []
    monkeypatch.setattr(pf.subprocess, "run", lambda cmd, **kw: calls.append(cmd) or Done())
    return type("F", (), {"dir": tmp_path, "calls": calls})


def _written(cmd):
    i = cmd.index("write-flash")
    return cmd[i + 1], Path(cmd[i + 2]).name


def test_a_version_14_panel_gets_the_program_image_only(fw):
    r = pf.flash("/dev/ttyACM1", "14", "15", link="wifi")
    assert r == (True, False, None)
    assert _written(fw.calls[0]) == ("0x10000", pf.PROGRAM_IMAGE)
    assert fw.calls[0][:3] == [sys.executable, "-m", "esptool"]


@pytest.mark.parametrize("have", ["13", "12", "9", "1"])   # 13 is the USB-only panel: not Wi-Fi-ready
def test_an_older_panel_gets_the_whole_image(fw, have):
    assert pf.flash("/dev/ttyACM1", have, "14", link="usb").whole is True
    assert _written(fw.calls[0]) == ("0x0", pf.WHOLE_IMAGE)


@pytest.mark.parametrize("have", [None, "", "garbled"])
def test_an_empty_panel_gets_the_whole_image(fw, have):
    assert pf.flash("/dev/ttyACM1", have, "14").whole is True
    assert _written(fw.calls[0]) == ("0x0", pf.WHOLE_IMAGE)


def test_a_changed_flash_layout_gets_the_whole_image(fw, monkeypatch):
    assert pf.flash("/dev/ttyACM1", "14", "16").whole is False
    monkeypatch.setattr(pf, "LAYOUT_BREAKS", (15,))
    assert pf.flash("/dev/ttyACM1", "14", "16").whole is True      # 15 changed the layout after this panel's 14
    assert pf.flash("/dev/ttyACM1", "15", "16").whole is False     # it already has that layout
    assert [_written(c)[0] for c in fw.calls] == ["0x10000", "0x0", "0x10000"]


def test_the_whole_image_is_used_when_the_program_image_is_missing(fw):
    (fw.dir / pf.PROGRAM_IMAGE).unlink()
    assert pf.flash("/dev/ttyACM1", "14", "15").whole is True
    assert _written(fw.calls[0]) == ("0x0", pf.WHOLE_IMAGE)


def test_the_wifi_notice_only_after_a_from_scratch_flash_on_wifi(fw, monkeypatch):
    assert pf.flash("/dev/ttyACM1", "13", "14", link="wifi").notice == pf.WIFI_LOST_NOTICE
    assert pf.flash("/dev/ttyACM1", "13", "14", link="usb").notice is None
    assert pf.flash("/dev/ttyACM1", "13", "14", link=None).notice is None
    assert pf.flash("/dev/ttyACM1", "14", "15", link="wifi").notice is None     # program image: Wi-Fi survives
    monkeypatch.setitem(st.settings, "panel_link", "wifi")
    assert pf.flash("/dev/ttyACM1", None, "14").notice == pf.WIFI_LOST_NOTICE   # link read from the settings
    assert pf.WIFI_LOST_NOTICE == ("The panel was flashed from scratch, so its Wi-Fi set-up is gone. "
                                   "Run: sudo {command} weigh-station panel-wifi")
    assert pf.flash("/dev/ttyACM1", None, "14").notice == ("The panel was flashed from scratch, so its Wi-Fi set-up "
                                                          "is gone. Run: sudo spoolwarden weigh-station panel-wifi")


def test_the_notice_test_leaves_a_real_panel_link_setting_alone(fw, monkeypatch):
    monkeypatch.setitem(st.settings, "panel_link", "usb")       # a default that exists once the link setting lands
    test_the_wifi_notice_only_after_a_from_scratch_flash_on_wifi(fw, monkeypatch)
    assert st.settings["panel_link"] == "wifi"                  # the inner monkeypatch is this test's own
    monkeypatch.undo()


def test_the_notice_comes_through_the_catalogue(fw, monkeypatch):
    seen = []
    monkeypatch.setattr(pf._station_page, "catalogue_text",
                        lambda key, fallback, **v: seen.append((key, fallback)) or "from the catalogue")
    assert pf.flash("/dev/ttyACM1", "13", "14", link="wifi").notice == "from the catalogue"
    assert seen == [(pf.WIFI_LOST_KEY, pf.WIFI_LOST_NOTICE)]


def test_no_notice_when_the_flash_failed(fw, monkeypatch):
    monkeypatch.setattr(pf.subprocess, "run", lambda cmd, **kw: type("D", (), {"returncode": 2, "stdout": b"", "stderr": b""})())
    assert pf.flash("/dev/ttyACM1", "13", "14", link="wifi") == (False, True, None)


def test_never_flashes_the_nfc_readers_port(fw, monkeypatch):
    monkeypatch.setitem(st.ports, "nfc", "/dev/ttyACM1")
    assert pf.flash("/dev/ttyACM1", "13", "14", link="wifi") == (False, False, None) and fw.calls == []


def test_the_kit_ships_both_images():
    whole, prog = (FW / pf.WHOLE_IMAGE).read_bytes(), (FW / pf.PROGRAM_IMAGE).read_bytes()
    assert whole[:1] == prog[:1] == b"\xe9"
    assert whole[int(pf.PROGRAM_ADDRESS, 16):] == prog         # the program image is the merged image's application
    root = FW.parents[2]
    for rel in ("install/install.sh", "tools/make_release.sh", "station/panel/build.sh"):
        assert pf.PROGRAM_IMAGE in (root / rel).read_text(), rel
