"""The Slicer Sync's own state files as XML (R85 section 1, step RC1c): config, state, the install marker, the pairing
kept aside, and the build marker files (BUILD, KEPT) of an installed build.

The codec is the Hub's (`filament_kit/profiles/statexml.py`): the two write the same bytes for the same data, and a
test holds them to it. Slicer Sync has no dependencies, so reading uses the standard parser after the two things a
local file never needs are refused: a DOCTYPE and an entity declaration. A file larger than MAX_BYTES is not read.

Both forms stay for one release: `read` takes the XML form when there is one, else the JSON form of an older Sync
(and moves it to XML); the config is also written in both forms (an older Sync keeps its pairing on a rollback).
The release after next deletes the JSON files.
"""
from __future__ import annotations

import contextlib
import errno
import json
import math
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

_INVALID_XML = re.compile("[^\u0009\u000a\u000d -퟿-�\U00010000-\U0010ffff]")


def _clean(value) -> str:
    return _INVALID_XML.sub("", "" if value is None else str(value))


def _fromstring(data: bytes):
    if re.search(rb"<!\s*(DOCTYPE|ENTITY)", data, re.I):
        raise ValueError("a DOCTYPE or entity declaration is not allowed")
    return ET.fromstring(data)


FORMAT = 1
MAX_BYTES = 64 << 20
_NAME = re.compile(r"[a-z][a-z0-9_]*")
_RESERVED = {"as", "key", "version"}
_LITERAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_INT = re.compile(r"-?(?:0|[1-9][0-9]*)")
_BREAKS = re.compile(r"[\n\r\t]")


class StateXmlError(ValueError):
    pass


# ---------- writing ----------

def _esc(s: str, attr: bool = False) -> str:
    s = _clean(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\r", "&#13;")
    return s.replace('"', "&quot;") if attr else s


def _name(key: str) -> str:
    return key.replace("_", "-")


def _scalar_text(v) -> tuple[str | None, str]:
    """(`as`, text) of a number, bool or None."""
    if v is None:
        return "null", ""
    if isinstance(v, bool):
        return "bool", "true" if v else "false"
    if isinstance(v, int):
        return "int", str(v)
    if isinstance(v, float):
        if not math.isfinite(v):
            return "null", ""
        return "float", repr(v)
    raise TypeError(type(v).__name__)


def _record_ok(d: dict) -> bool:
    return all(isinstance(k, str) and _NAME.fullmatch(k) and k not in _RESERVED for k in d)


def _attr_ok(v: str) -> bool:
    return not (_LITERAL.fullmatch(v) or v in ("true", "false") or _BREAKS.search(v))


def _element(tag: str, v, ind: str, extra: str = "") -> str:
    """The element for value `v` in a slot (a list item, a map entry, a field that is not a plain attribute)."""
    if isinstance(v, str):
        return f"{ind}<{tag}{extra}>{_esc(v)}</{tag}>\n"
    if v is None or isinstance(v, (bool, int, float)):
        kind, text = _scalar_text(v)
        return f"{ind}<{tag}{extra} as=\"{kind}\">{text}</{tag}>\n" if text else f"{ind}<{tag}{extra} as=\"{kind}\"/>\n"
    if isinstance(v, (list, tuple)):
        if not v:
            return f"{ind}<{tag}{extra} as=\"list\"/>\n"
        body = "".join(_element("item", x, ind + "  ") for x in v)
        return f"{ind}<{tag}{extra} as=\"list\">\n{body}{ind}</{tag}>\n"
    if isinstance(v, dict):
        if not v:
            return f"{ind}<{tag}{extra} as=\"dict\"/>\n"
        if _record_ok(v):
            attrs, kids = _fields(v, ind + "  ")
            return (f"{ind}<{tag}{extra}{attrs}>\n{kids}{ind}</{tag}>\n" if kids else f"{ind}<{tag}{extra}{attrs}/>\n")
        body = "".join(_element("entry", x, ind + "  ", f' key="{_esc(str(k), True)}"') for k, x in v.items())
        return f"{ind}<{tag}{extra} as=\"map\">\n{body}{ind}</{tag}>\n"
    raise TypeError(f"cannot write a {type(v).__name__} as XML")


def _fields(d: dict, ind: str) -> tuple[str, str]:
    attrs, kids = "", ""
    for k, v in d.items():
        n = _name(k)
        if isinstance(v, str):
            if _attr_ok(v):
                attrs += f' {n}="{_esc(v, True)}"'
            else:
                kids += _element(n, v, ind)
        elif isinstance(v, (bool, int, float)) and (not isinstance(v, float) or math.isfinite(v)):
            attrs += f' {n}="{_scalar_text(v)[1]}"'
        else:
            kids += _element(n, v, ind)
    return attrs, kids


def dumps(tag: str, obj: dict, *, version: int | None = None) -> bytes:
    """`obj` (an object of JSON values) as the XML form. A `version` key of the object is the root's `version`."""
    if not isinstance(obj, dict):
        raise StateXmlError("a state file holds an object")
    fields = {k: v for k, v in obj.items() if k != "version"}
    ver = obj.get("version", FORMAT if version is None else version)
    if _record_ok(fields):
        attrs, kids = _fields(fields, "  ")
    else:                                       # a top-level key that is not a plain name: the root is a map
        attrs = " as=\"map\""
        kids = "".join(_element("entry", x, "  ", f' key="{_esc(str(k), True)}"') for k, x in fields.items())
    head = f"<{tag} version=\"{ver}\"{attrs}"
    body = f"{head}>\n{kids}</{tag}>\n" if kids else f"{head}/>\n"
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" + body).encode("utf-8")


def dumps_list(tag: str, items: list, *, version: int | None = None) -> bytes:
    """A top-level JSON list as the XML form: the root has `as="list"` and one `<item>` per entry."""
    if not isinstance(items, (list, tuple)):
        raise StateXmlError("a list file holds a list")
    ver = FORMAT if version is None else version
    head = f"<{tag} version=\"{ver}\" as=\"list\""
    kids = "".join(_element("item", x, "  ") for x in items)
    body = f"{head}>\n{kids}</{tag}>\n" if kids else f"{head}/>\n"
    return ("<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n" + body).encode("utf-8")


# ---------- reading ----------

def _literal(v: str):
    if v in ("true", "false"):
        return v == "true"
    if _LITERAL.fullmatch(v):
        return int(v) if _INT.fullmatch(v) else float(v)
    return v


def _slot(el):
    kind = el.get("as")
    if kind == "list":
        return [_slot(c) for c in el if c.tag == "item"]
    if kind == "map":
        return {c.get("key", ""): _slot(c) for c in el if c.tag == "entry"}
    if kind == "dict":
        return {}
    if kind == "null":
        return None
    text = el.text or ""
    if kind == "int":
        return int(text)
    if kind == "float":
        return float(text)
    if kind == "bool":
        return text == "true"
    if len(el) or any(a not in ("as", "key") for a in el.attrib):
        return _record(el)
    return text


def _record(el) -> dict:
    out: dict = {}
    for a, v in el.attrib.items():
        if a not in ("as", "key", "version"):
            out[a.replace("-", "_")] = _literal(v)
    for c in el:
        out[c.tag.replace("-", "_")] = _slot(c)
    return out


def _parse_root(data: bytes, tag: str):
    """(root element, its version text) of an XML form file; StateXmlError when it is not one."""
    if len(data) > MAX_BYTES:
        raise StateXmlError(f"larger than {MAX_BYTES} bytes")
    try:
        root = _fromstring(data)
    except Exception as e:  # noqa: BLE001 - ParseError, DTDForbidden, EntitiesForbidden
        raise StateXmlError(f"not readable XML ({type(e).__name__})") from None
    if root.tag != tag:
        raise StateXmlError(f"root element is <{root.tag}>, not <{tag}>")
    v = root.get("version", "")
    if not re.fullmatch(r"\d{1,6}", v or "1"):
        raise StateXmlError(f"version {v!r} is not a number")
    return root, v


def loads(data: bytes, tag: str, *, keep_version: bool = False) -> dict:
    """The object in an XML form file; StateXmlError when it is not one (too large, not well-formed, wrong root)."""
    root, v = _parse_root(data, tag)
    try:
        out = _slot(root) if root.get("as") == "map" else _record(root)
    except (ValueError, TypeError) as e:
        raise StateXmlError(f"a value is not what its type says ({e})") from None
    if keep_version and v:
        out = {"version": int(v), **out}
    return out


def loads_list(data: bytes, tag: str) -> list:
    """The list in an XML form list file; StateXmlError when it is not one."""
    root, _ = _parse_root(data, tag)
    if root.get("as") != "list":
        raise StateXmlError("not a list file")
    try:
        return [_slot(c) for c in root if c.tag == "item"]
    except (ValueError, TypeError) as e:
        raise StateXmlError(f"a value is not what its type says ({e})") from None


# ---------- the files ----------

def xml_path(path: Path) -> Path:
    return Path(path).with_suffix(".xml")


def tag_of(path: Path) -> str:
    return Path(path).stem.lower().replace("_", "-")


_unreadable: dict[str, str] = {}


def problems() -> list[str]:
    """What could not be converted or read since this process started: ["<file> (<reason>)", ...]."""
    return [f"{k} ({v})" for k, v in sorted(_unreadable.items())]


def note_problem(path: Path, reason: str) -> None:
    """Record that `path` (any file, not only a state file) could not be read, for `problems()` and the Checks row."""
    _unreadable[str(path)] = reason


def clear_problem(path: Path) -> None:
    """Forget the problem noted for `path` (it reads again, or was set aside)."""
    _unreadable.pop(str(path), None)


def atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    """Write data to a temp file next to path, then replace path with it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        if sys.platform != "win32":
            os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


_DEFAULT_MODE = 0o600
_NO_WRITE = {errno.EACCES, errno.EPERM, errno.EROFS}


def _put(path: Path, data: bytes, mode: int, state_owned: bool) -> None:      # state_owned is the Hub's: unused here
    atomic_write(path, data, mode)


def _mkdir(path: Path) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def _plain(path: Path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _plain_list(path: Path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, list) else None


def quarantine(path: Path) -> bool:
    """Keep the XML form of `path` (its JSON name, or the XML name) as `<name>.xml.unreadable` so the next write does
    not destroy it, and forget its problem. True when there was a file to keep."""
    xp = xml_path(path)
    try:
        os.replace(xp, xp.with_name(xp.name + ".unreadable"))
    except OSError:
        return False
    clear_problem(xp)
    return True


def _read(path: Path, tag: str | None, keep_version: bool, is_list: bool, mode: int | None, state_owned: bool):
    path = Path(path)
    xp = xml_path(path)
    t = tag or tag_of(path)
    try:
        raw = xp.read_bytes()
    except FileNotFoundError:
        raw = None
    except OSError as e:
        note_problem(xp, f"cannot read: {e.strerror or e}")
        raw = None
    else:
        try:
            out = loads_list(raw, t) if is_list else loads(raw, t, keep_version=keep_version)
            clear_problem(xp)
            return out
        except StateXmlError as e:
            note_problem(xp, str(e))
    old = _plain_list(path) if is_list else _plain(path)
    if old is not None and raw is None:
        _move(path, old, t, mode, state_owned)
    return old


def read(path: Path, *, keep_version: bool = False, tag: str | None = None, mode: int | None = None,
         state_owned: bool = True) -> dict | None:
    """The data of state file `path` (given by its JSON name), or None when there is none or neither form holds an
    object. The XML form wins; a JSON-only file is moved to XML on the way (with `mode` and owner as given: the default
    mode of the copy, the state's owner). `tag` is the root element when it is not the file stem (a month-named file).
    A store that cannot be rewritten (a read by a user with no write permission) reads the old form, records nothing
    and raises nothing."""
    return _read(path, tag, keep_version, False, mode, state_owned)


def read_list(path: Path, *, tag: str | None = None, mode: int | None = None, state_owned: bool = True) -> list | None:
    """Like `read` for a file that holds a JSON list (reads.json, devices.json): None when neither form holds a list."""
    return _read(path, tag, False, True, mode, state_owned)


def _move(path: Path, data, tag: str, mode: int | None, state_owned: bool) -> None:
    xp = xml_path(path)
    moving = xp.with_name(xp.name + ".moving")
    try:
        if xp.exists():
            return
        _put(moving, dumps_list(tag, data) if isinstance(data, list) else dumps(tag, data),
             _DEFAULT_MODE if mode is None else mode, state_owned)
        try:
            os.link(moving, xp)
        except FileExistsError:
            pass
    except (OSError, StateXmlError, TypeError, ValueError) as e:
        if not (isinstance(e, OSError) and e.errno in _NO_WRITE):      # no write permission is not a failure
            note_problem(path, f"could not be converted: {e}")
    finally:
        try:
            moving.unlink()
        except OSError:
            pass


def migrate(path: Path, obj: dict, mode: int | None = None, *, tag: str | None = None, state_owned: bool = True) -> None:
    """Write `obj` (what the JSON file `path` holds) as its XML form, only when there is none yet: a reader that moves
    a file must never write over what a writer saved a moment before. A failing move changes nothing (the JSON form
    is read until the next save writes the XML form); no write permission is not recorded as a problem."""
    _move(Path(path), obj, tag or tag_of(path), mode, state_owned)


def migrate_list(path: Path, items: list, mode: int | None = None, *, tag: str | None = None,
                 state_owned: bool = True) -> None:
    """Like `migrate` for a file that holds a JSON list."""
    _move(Path(path), list(items), tag or tag_of(path), mode, state_owned)


def _save(path: Path, data, tag: str | None, mode: int | None, state_owned: bool, is_list: bool) -> None:
    path = Path(path)
    xp = xml_path(path)
    t = tag or tag_of(path)
    if xp.exists():                                 # never overwrite what could not be read
        try:
            loads_list(xp.read_bytes(), t) if is_list else loads(xp.read_bytes(), t, keep_version=True)
        except (OSError, StateXmlError):
            quarantine(path)
    _mkdir(xp.parent)
    _put(xp, dumps_list(t, data) if is_list else dumps(t, data), _DEFAULT_MODE if mode is None else mode, state_owned)
    clear_problem(xp)
    clear_problem(path)


def write(path: Path, obj: dict, mode: int | None = None, *, tag: str | None = None, state_owned: bool = True) -> None:
    """Save `obj` as state file `path`'s XML form (the JSON form is left as it was at the move). An XML file that cannot
    be read is kept as `<name>.xml.unreadable`. `mode` and `state_owned` as `read`."""
    _save(path, obj, tag, mode, state_owned, False)


def write_list(path: Path, items: list, mode: int | None = None, *, tag: str | None = None,
               state_owned: bool = True) -> None:
    """Like `write` for a file that holds a JSON list."""
    _save(path, list(items), tag, mode, state_owned, True)


def write_both(path: Path, obj: dict, mode: int = 0o600) -> None:
    """XML first, then the JSON form of the file (an older Sync reads that one after a rollback)."""
    write(path, obj, mode)
    atomic_write(Path(path), (json.dumps(obj, indent=2, sort_keys=True) + "\n").encode("utf-8"), mode)


def write_plain(path: Path, obj: dict, *, also_json: bool = False) -> None:
    """Like `write` for a folder being made (no temp file, no check of an earlier file). `also_json` writes the JSON
    form too: the two forms of a marker file stay for one release."""
    xp = xml_path(path)
    xp.parent.mkdir(parents=True, exist_ok=True)
    xp.write_bytes(dumps(tag_of(path), obj))
    if also_json:
        Path(path).write_text(json.dumps(obj), encoding="utf-8", newline="\n")


def exists(path: Path) -> bool:
    return xml_path(path).is_file() or Path(path).is_file()


def unlink(path: Path) -> None:
    for p in (xml_path(path), Path(path)):
        with contextlib.suppress(FileNotFoundError):
            p.unlink()
