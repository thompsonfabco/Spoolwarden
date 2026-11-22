"""An XML state file is set aside only when it is really broken, and a set-aside copy is never replaced (R630 L3). The same
tests run on the Hub copy and the Slicer Sync copy (the Hub's file also holds the test that the two are identical)."""
import errno
from pathlib import Path

import pytest

from filament_kit.profiles import statexml

REPO = Path(__file__).resolve().parents[2]


def _break(p, text=b"<lines version="):
    (p.parent / (p.stem + ".xml")).write_bytes(text)


def test_a_file_that_does_not_parse_is_set_aside_on_the_next_write(tmp_path):
    p = tmp_path / "lines.json"
    _break(p)
    statexml.write(p, {"n": 1})
    assert (tmp_path / "lines.xml.unreadable").read_bytes() == b"<lines version="
    assert statexml.read(p) == {"n": 1}


def test_a_wrong_root_is_set_aside_too(tmp_path):
    p = tmp_path / "lines.json"
    _break(p, b'<other version="1"/>')
    statexml.write(p, {"n": 1})
    assert (tmp_path / "lines.xml.unreadable").read_bytes() == b'<other version="1"/>'


@pytest.mark.parametrize("code", [errno.EACCES, errno.EIO])
def test_a_transient_read_error_leaves_the_good_file_and_notes_it(tmp_path, monkeypatch, code):
    p = tmp_path / "lines.json"
    statexml.write(p, {"n": 1})
    good = (tmp_path / "lines.xml").read_bytes()
    real = Path.read_bytes

    def flaky(self):
        if self.name == "lines.xml":
            raise OSError(code, "transient")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    statexml.read(p)                                            # no value; it must not raise
    assert any("lines.xml" in x and "cannot read" in x for x in statexml.problems())
    statexml.write(p, {"n": 2})                                 # a save over a file that could not be read: no set-aside
    monkeypatch.undo()
    assert not list(tmp_path.glob("*.unreadable*"))
    assert (tmp_path / "lines.xml").read_bytes() != good        # it was saved, not moved away
    assert statexml.read(p) == {"n": 2}


def test_a_transient_read_error_returns_no_value_and_keeps_the_file(tmp_path, monkeypatch):
    p = tmp_path / "lines.xml"
    statexml.write(tmp_path / "lines.json", {"n": 1})
    (tmp_path / "lines.json").unlink()
    (tmp_path / "lines.json.sha256").unlink()
    real = Path.read_bytes

    def flaky(self):
        if self.name == "lines.xml":
            raise OSError(errno.EIO, "io")
        return real(self)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    assert statexml.read(tmp_path / "lines.json") is None
    monkeypatch.undo()
    assert p.is_file() and not list(tmp_path.glob("*.unreadable*"))


def test_two_set_asides_keep_both_copies(tmp_path):
    p = tmp_path / "lines.json"
    _break(p, b"<lines first")
    statexml.write(p, {"n": 1})
    _break(p, b"<lines second")
    statexml.write(p, {"n": 2})
    assert (tmp_path / "lines.xml.unreadable").read_bytes() == b"<lines second"          # the newest
    assert (tmp_path / "lines.xml.unreadable.1").read_bytes() == b"<lines first"         # the older, numbered


def test_public_quarantine_numbers_the_copies_and_keeps_five_at_most(tmp_path):
    p = tmp_path / "lines.json"
    for i in range(7):
        _break(p, f"<lines {i}".encode())
        assert statexml.quarantine(p) is True
    got = {n: (tmp_path / f"lines.xml.unreadable{n}").read_bytes() for n in ("", ".1", ".2", ".3", ".4")}
    assert got == {"": b"<lines 6", ".1": b"<lines 5", ".2": b"<lines 4", ".3": b"<lines 3", ".4": b"<lines 2"}
    assert not (tmp_path / "lines.xml.unreadable.5").exists()
    assert any("oldest" in x for x in statexml.problems())                  # the deletion is noted, not silent


def test_quarantine_with_no_file_is_false_and_makes_no_copy(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    assert statexml.quarantine(d / "lines.json") is False
    assert not list(d.iterdir())


def test_a_deleted_json_removes_a_token_bearing_xml_and_leaves_nothing_behind(tmp_path):
    tmp_path = tmp_path / "s"
    p = tmp_path / "pair-aside.json"
    other = tmp_path / "other.json"
    statexml.write(other, {"n": 1}, 0o640)
    statexml.write(p, {"token": "SECRET-TOKEN"})
    modes = {x.name: x.stat().st_mode for x in tmp_path.iterdir() if x.name.startswith("other")}
    p.unlink()
    assert statexml.read(p) is None
    left = sorted(x.name for x in tmp_path.iterdir())
    assert left == sorted(["other.json", "other.json.sha256", "other.xml"])      # no .unreadable, no temp, no hash
    assert {x.name: x.stat().st_mode for x in tmp_path.iterdir() if x.name.startswith("other")} == modes
    assert all(b"SECRET-TOKEN" not in x.read_bytes() for x in tmp_path.iterdir())


@pytest.mark.parametrize("fn", ["unlink"])
def test_a_removal_that_fails_keeps_the_hash_and_notes_it(tmp_path, monkeypatch, fn):
    p = tmp_path / "pair-aside.json"
    statexml.write(p, {"n": 1})
    p.unlink()
    real = Path.unlink

    def deny(self, *a, **k):
        if self.suffix == ".xml":
            raise PermissionError(13, "denied")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", deny)
    assert statexml.read(p) == {"n": 1}                                    # the XML still wins this time
    assert (tmp_path / "pair-aside.json.sha256").is_file() and (tmp_path / "pair-aside.xml").is_file()
    assert any("pair-aside" in x and "could not be removed" in x for x in statexml.problems())
    monkeypatch.undo()
    assert statexml.read(p) is None                                        # the next read tries again and succeeds
    assert not (tmp_path / "pair-aside.xml").exists() and not (tmp_path / "pair-aside.json.sha256").exists()


def test_the_set_aside_code_is_the_same_in_both_copies():
    def part(p):
        t = p.read_text(encoding="utf-8").replace("\r\n", "\n")
        return t[t.index("KEEP_UNREADABLE = "):t.index("# ---- end of the shared block")]

    hub = part(REPO / "server" / "filament_kit" / "profiles" / "statexml.py")
    sync = part(REPO / "pc-sync" / "fksync" / "statexml.py")
    assert hub.split("# ---- shared block")[0] == sync.split("# ---- shared block")[0] and "def quarantine" in hub
