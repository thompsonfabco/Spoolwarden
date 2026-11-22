"""A state file is written under a faked root on a platform with no os.fchown (Windows), as test_cli_menus does."""
import os

import pytest

from filament_kit import config, library, pairing


@pytest.fixture(autouse=True)
def fk_env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))


def test_a_state_file_is_written_under_a_faked_root_with_no_fchown(monkeypatch):
    monkeypatch.setattr(os, "geteuid", lambda: 0, raising=False)
    monkeypatch.delattr(os, "fchown", raising=False)
    library._write_state("current.json", {"dir": "7-20261101", "version": 7, "at": 1.5})
    assert library._read_state("current.json")["dir"] == "7-20261101"
    pairing._write_atomic(config.paths().state / "x", b"1", 0o600, state_owned=True)
    assert (config.paths().state / "x").read_bytes() == b"1"
