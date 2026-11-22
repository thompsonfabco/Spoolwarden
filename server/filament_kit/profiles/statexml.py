"""The slicer-preset state files as XML (R85 section 1, step RC1c), and the one way every reader and writer of them goes.

A state file that was `name.json` is `name.xml` (the root element is the lower-case stem: `lines`, `installed`,
`owner-edits`, `build`, `slicer`, `kept`, ...). The form follows the one `printers.xml` and `storage.xml` use:

* a single value is an attribute (a number or true/false is written as such; a text that would read back as one, or
  holds a line break, is written as element text instead);
* a list is an element `as="list"` with repeated `<item>` children, and an object whose keys are not plain names
  (preset names) is `as="map"` with `<entry key="...">` children;
* free text is element text; an empty object is `as="dict"`; an absent value is `as="null"`;
* names are lower-case with hyphens (`orca_stamp` is written `orca-stamp`);
* the root has `version`, the file format (a file whose own data has a `version`, `lines`, uses that one).

The Python copy for Slicer Sync is `pc-sync/fksync/statexml.py`; the two codecs write the same bytes (a test holds them
to it). Reading goes through defusedxml (no DTD, no entities); a file larger than MAX_BYTES is not read.

A reader gets the XML form when there is one and it can be read. With only the JSON form it reads that and writes the
XML form once (`read` moves it; a failing move changes nothing). An XML file that cannot be read is never overwritten:
the next write keeps it as `<name>.xml.unreadable`. Both forms stay for one release (a rollback reads the JSON form as
it was at the move); the release after next deletes the JSON files.
"""
from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

from ..bridge import xmlfile

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
    s = xmlfile.clean(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\r", "&#13;")
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
        root = xmlfile.fromstring(data)
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

_unreadable: dict[str, str] = {}


def xml_path(path: Path) -> Path:
    return Path(path).with_suffix(".xml")


def tag_of(path: Path) -> str:
    return Path(path).stem.lower().replace("_", "-")


def problems() -> list[str]:
    """What could not be converted or read since this process started: ["<file> (<reason>)", ...]."""
    return [f"{k} ({v})" for k, v in sorted(_unreadable.items())]


def note_problem(path: Path, reason: str) -> None:
    """Record that `path` (any file, not only a state file) could not be read, for `problems()` and the Checks row."""
    _unreadable[str(path)] = reason


def clear_problem(path: Path) -> None:
    """Forget the problem noted for `path` (it reads again, or was set aside)."""
    _unreadable.pop(str(path), None)


def _plain(path: Path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def read(path: Path, *, keep_version: bool = False) -> dict | None:
    """The data of state file `path` (given by its JSON name), or None when there is none or neither form holds an
    object. The XML form wins; a JSON-only file is moved to XML on the way."""
    path = Path(path)
    xp = xml_path(path)
    try:
        raw = xp.read_bytes()
    except FileNotFoundError:
        raw = None
    except OSError as e:
        _unreadable[str(xp)] = f"cannot read: {e.strerror or e}"
        raw = None
    else:
        try:
            out = loads(raw, tag_of(path), keep_version=keep_version)
            _unreadable.pop(str(xp), None)
            return out
        except StateXmlError as e:
            _unreadable[str(xp)] = str(e)
    old = _plain(path)
    if old is not None and raw is None:
        migrate(path, old)
    return old


def migrate(path: Path, obj: dict) -> None:
    """Write `obj` (what the JSON file `path` holds) as its XML form, only when there is none yet: a reader that moves
    a file must never write over what a writer saved a moment before. A failing move changes nothing (the JSON form
    is read until the next save writes the XML form)."""
    from ..pairing import _write_atomic
    xp = xml_path(path)
    moving = xp.with_name(xp.name + ".moving")
    try:
        if xp.exists():
            return
        _write_atomic(moving, dumps(tag_of(path), obj), 0o644, state_owned=True)
        try:
            os.link(moving, xp)
        except FileExistsError:
            pass
    except (OSError, StateXmlError, TypeError, ValueError) as e:
        _unreadable[str(path)] = f"could not be converted: {e}"
    finally:
        try:
            moving.unlink()
        except OSError:
            pass


def _store(path: Path, obj: dict, *, mode: int = 0o644, state_owned: bool = True) -> None:
    from ..pairing import _write_atomic, make_state_dir
    xp = xml_path(path)
    if str(xp) in _unreadable and xp.exists():     # never overwrite what could not be read
        os.replace(xp, xp.with_name(xp.name + ".unreadable"))
        _unreadable.pop(str(xp), None)
    make_state_dir(xp.parent)
    _write_atomic(xp, dumps(tag_of(path), obj), mode, state_owned=state_owned)


def write(path: Path, obj: dict, *, mode: int = 0o644, state_owned: bool = True) -> None:
    """Save `obj` as state file `path`'s XML form (the JSON form is left as it was at the move)."""
    _store(Path(path), obj, mode=mode, state_owned=state_owned)


def write_plain(path: Path, obj: dict, *, also_json: bool = False) -> None:
    """Like `write` for a folder that is not the state's own (a build being made in a temp folder). `also_json` writes
    the JSON form too: the two forms of a marker file stay for one release."""
    xp = xml_path(path)
    xp.parent.mkdir(parents=True, exist_ok=True)
    with open(xp, "wb") as fh:
        fh.write(dumps(tag_of(path), obj))
    if also_json:
        Path(path).write_text(json.dumps(obj, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def exists(path: Path) -> bool:
    return xml_path(path).is_file() or Path(path).is_file()


def unlink(path: Path) -> None:
    for p in (xml_path(path), Path(path)):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def json_bytes(path: Path) -> bytes | None:
    """The state file as JSON bytes (for an older Slicer Sync that reads only BUILD.json): the JSON form when it is
    there, else the XML form turned back; None when there is neither."""
    path = Path(path)
    try:
        return path.read_bytes()
    except FileNotFoundError:
        pass
    obj = read(path, keep_version=False)
    return None if obj is None else (json.dumps(obj, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def xml_bytes(path: Path) -> bytes | None:
    """The XML form's bytes: the file's own when it is there, else made from the JSON form."""
    path = Path(path)
    try:
        return xml_path(path).read_bytes()
    except FileNotFoundError:
        pass
    obj = _plain(path)
    return None if obj is None else dumps(tag_of(path), obj)
