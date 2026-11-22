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


def loads(data: bytes, tag: str, *, keep_version: bool = False) -> dict:
    """The object in an XML form file; StateXmlError when it is not one (too large, not well-formed, wrong root)."""
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
    try:
        out = _slot(root) if root.get("as") == "map" else _record(root)
    except (ValueError, TypeError) as e:
        raise StateXmlError(f"a value is not what its type says ({e})") from None
    if keep_version and v:
        out = {"version": int(v), **out}
    return out


# ---------- the files ----------

def xml_path(path: Path) -> Path:
    return Path(path).with_suffix(".xml")


def tag_of(path: Path) -> str:
    return Path(path).stem.lower().replace("_", "-")


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


def _plain(path: Path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read(path: Path, *, keep_version: bool = False) -> dict | None:
    """The data of state file `path` (given by its JSON name): the XML form when there is one and it can be read, else
    the JSON form (moved to XML on the way); None when neither holds an object."""
    path = Path(path)
    xp = xml_path(path)
    try:
        raw = xp.read_bytes()
    except OSError:
        raw = None
    if raw is not None:
        try:
            return loads(raw, tag_of(path), keep_version=keep_version)
        except StateXmlError:
            pass                                # an unreadable XML file is never written over: see write()
    old = _plain(path)
    if old is not None and raw is None:
        migrate(path, old)
    return old


def migrate(path: Path, obj: dict, mode: int = 0o600) -> None:
    """Write `obj` as `path`'s XML form only when there is none yet (a reader must never write over what a writer saved
    a moment before). A failing move changes nothing."""
    xp = xml_path(path)
    moving = xp.with_name(xp.name + ".moving")
    try:
        if xp.exists():
            return
        atomic_write(moving, dumps(tag_of(path), obj), mode)
        try:
            os.link(moving, xp)
        except FileExistsError:
            pass
    except (OSError, StateXmlError, TypeError, ValueError):
        pass
    finally:
        with contextlib.suppress(OSError):
            moving.unlink()


def write(path: Path, obj: dict, mode: int = 0o600) -> None:
    """Save `obj` as state file `path`'s XML form. An XML file that cannot be read is kept as `<name>.xml.unreadable`."""
    path = Path(path)
    xp = xml_path(path)
    if xp.exists():
        try:
            loads(xp.read_bytes(), tag_of(path), keep_version=True)
        except (OSError, StateXmlError):
            with contextlib.suppress(OSError):
                os.replace(xp, xp.with_name(xp.name + ".unreadable"))
    atomic_write(xp, dumps(tag_of(path), obj), mode)


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
