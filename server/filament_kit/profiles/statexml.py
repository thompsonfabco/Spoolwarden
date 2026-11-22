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
the next write keeps it (`quarantine`) as `<name>.xml.unreadable`. Both forms stay for one release (a rollback reads the JSON form as
it was at the move); the release after next deletes the JSON files.
"""
from __future__ import annotations

import errno
import hashlib
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
        root = xmlfile.fromstring(data)
    except Exception as e:  # noqa: BLE001 - ParseError, DTDForbidden, EntitiesForbidden
        raise StateXmlError(f"not readable XML ({type(e).__name__})") from None
    if root.tag != tag:
        raise StateXmlError(f"root element is <{root.tag}>, not <{tag}>")
    v = root.get("version", "")
    if not re.fullmatch(r"[0-9]{1,18}", v or "1"):
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


_DEFAULT_MODE = 0o644
_NO_WRITE = {errno.EACCES, errno.EPERM, errno.EROFS}
OLD_FORMAT = {"indent": 2, "ensure_ascii": False}     # how the older Hub release wrote its state files (builds.py, lines.py)


def _put(path: Path, data: bytes, mode: int, state_owned: bool) -> None:
    from ..pairing import _write_atomic
    _write_atomic(path, data, mode, state_owned=state_owned)


def _mkdir(path: Path) -> None:
    from ..pairing import make_state_dir
    make_state_dir(path)


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


# ---- shared block: identical in both copies (a test holds them to it); OLD_FORMAT, _put, _mkdir are each copy's own ----

def _json_path(path: Path, old_name) -> Path:
    """The old (JSON) form of state file `path`: its own name, or `old_name` (a file name next to it, or a path) when the
    old release used another name (usage.jsonl, station_server.json)."""
    return Path(path) if not old_name else Path(path).parent / old_name


def _old_bytes(data, old_format) -> bytes:
    """`data` as the older release wrote it. `old_format` is the keyword arguments of json.dumps (the default OLD_FORMAT of
    this copy), or a function that returns the bytes (a file that was not one JSON document). A newline follows when an
    indent was used, as the older writers did."""
    if callable(old_format):
        return old_format(data)
    kw = OLD_FORMAT if old_format is None else old_format
    text = json.dumps(data, **kw)
    return (text + "\n" if kw.get("indent") is not None else text).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_path(jp: Path) -> Path:
    return jp.with_name(jp.name + ".sha256")


def _recorded(jp: Path) -> str | None:
    """The hash of the JSON form as this code last wrote it, or None (nothing recorded yet)."""
    try:
        return _sha_path(jp).read_text(encoding="ascii").strip() or None
    except (OSError, UnicodeDecodeError):
        return None


def _note_sha(jp: Path, raw: bytes, mode: int, state_owned: bool) -> None:
    _put(_sha_path(jp), (_sha(raw) + "\n").encode("ascii"), mode, state_owned)


def _keep_old(path: Path, data, mode: int, state_owned: bool, old_name, old_format) -> None:
    """The JSON form next to the XML one, then the hash of it: written after the XML, so a stop in between leaves the XML as
    the truth (a JSON that equals the XML's data is never taken for a newer one)."""
    jp = _json_path(path, old_name)
    body = _old_bytes(data, old_format)
    _put(jp, body, mode, state_owned)
    _note_sha(jp, body, mode, state_owned)


def _same(a, b) -> bool:
    if isinstance(a, dict) and isinstance(b, dict):
        a, b = {k: v for k, v in a.items() if k != "version"}, {k: v for k, v in b.items() if k != "version"}
    return a == b


_GONE = object()      # `_newer_json`: the older release deleted the JSON form, so the file is none


def _newer_json(path: Path, xml_data, t: str, is_list: bool, keep_version: bool, mode: int | None, state_owned: bool,
                old_name):
    """The data of a JSON form that changed after this code last wrote it (an older release after a rollback), or None.
    It wins once: the XML form is written from it and its hash recorded. Nothing recorded, an unreadable JSON, or one
    that holds what the XML form holds: the XML form wins. A recorded hash with the JSON gone means an older release
    deleted the file (the way it says "none"): the XML form is set aside (`quarantine`), the hash cleared and `_GONE`
    returned. A store that cannot be rewritten still returns the data."""
    jp = _json_path(path, old_name)
    want = _recorded(jp)
    if want is None:
        return None
    try:
        raw = jp.read_bytes()
    except FileNotFoundError:
        quarantine(path)
        try:
            _sha_path(jp).unlink()
        except OSError:
            pass
        return _GONE
    except OSError:
        return None
    if _sha(raw) == want:
        return None
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(data, list if is_list else dict):
        return None
    m = _DEFAULT_MODE if mode is None else mode
    same = _same(data, xml_data)
    try:
        if not same:
            _write_xml(path, data, t, m, state_owned, is_list)
        _note_sha(jp, raw, m, state_owned)
    except (OSError, StateXmlError, TypeError, ValueError) as e:
        if not (isinstance(e, OSError) and e.errno in _NO_WRITE):
            note_problem(path, f"could not be converted: {e}")
    if same:
        return None
    return {"version": FORMAT, **data} if keep_version and not is_list and "version" not in data else data


def _read(path: Path, tag: str | None, keep_version: bool, is_list: bool, mode: int | None, state_owned: bool,
          keep_old_form: bool = True, old_name=None):
    path = Path(path)
    xp = xml_path(path)
    jp = _json_path(path, old_name)
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
            if keep_old_form:
                newer = _newer_json(path, out, t, is_list, keep_version, mode, state_owned, old_name)
                if newer is _GONE:
                    return None
                if newer is not None:
                    return newer
            return out
        except StateXmlError as e:
            note_problem(xp, str(e))
    old = _plain_list(jp) if is_list else _plain(jp)
    if old is not None and raw is None:
        _move(path, old, t, mode, state_owned, keep_old_form, old_name)
    return old


def read(path: Path, *, keep_version: bool = False, tag: str | None = None, mode: int | None = None,
         state_owned: bool = True, keep_old_form: bool = True, old_name=None) -> dict | None:
    """The data of state file `path` (given by its JSON name), or None when there is none or neither form holds an
    object. The XML form wins; a JSON-only file is moved to XML on the way (with `mode` and owner as given: the default
    mode of the copy, the state's owner). `tag` is the root element when it is not the file stem (a month-named file).
    A store that cannot be rewritten (a read by a user with no write permission) reads the old form, records nothing
    and raises nothing. `keep_old_form` and `old_name` as `write`: a JSON form that an older release changed after this
    code wrote it (a rollback) wins once and is written to the XML form."""
    return _read(path, tag, keep_version, False, mode, state_owned, keep_old_form, old_name)


def read_list(path: Path, *, tag: str | None = None, mode: int | None = None, state_owned: bool = True,
              keep_old_form: bool = True, old_name=None) -> list | None:
    """Like `read` for a file that holds a JSON list (reads.json, devices.json): None when neither form holds a list."""
    return _read(path, tag, False, True, mode, state_owned, keep_old_form, old_name)


def _move(path: Path, data, tag: str, mode: int | None, state_owned: bool, keep_old_form: bool = True,
          old_name=None, old_format=None) -> None:
    xp = xml_path(path)
    moving = xp.with_name(xp.name + ".moving")
    m = _DEFAULT_MODE if mode is None else mode
    try:
        if xp.exists():
            return
        _put(moving, dumps_list(tag, data) if isinstance(data, list) else dumps(tag, data), m, state_owned)
        try:
            os.link(moving, xp)
        except FileExistsError:
            return
        if keep_old_form:                       # the JSON form stays as it is when there is one; else it is written
            jp = _json_path(path, old_name)
            try:
                have = jp.read_bytes()
            except FileNotFoundError:
                _keep_old(path, data, m, state_owned, old_name, old_format)
            else:
                if _same(_plain_list(jp) if isinstance(data, list) else _plain(jp), data):
                    _note_sha(jp, have, m, state_owned)
    except (OSError, StateXmlError, TypeError, ValueError) as e:
        if not (isinstance(e, OSError) and e.errno in _NO_WRITE):      # no write permission is not a failure
            note_problem(path, f"could not be converted: {e}")
    finally:
        try:
            moving.unlink()
        except OSError:
            pass


def migrate(path: Path, obj: dict, mode: int | None = None, *, tag: str | None = None, state_owned: bool = True,
            keep_old_form: bool = True, old_name=None, old_format=None) -> None:
    """Write `obj` (what the JSON file `path` holds) as its XML form, only when there is none yet: a reader that moves
    a file must never write over what a writer saved a moment before. A failing move changes nothing (the JSON form
    is read until the next save writes the XML form); no write permission is not recorded as a problem. The JSON form is
    written too when there is none (as `write`)."""
    _move(Path(path), obj, tag or tag_of(path), mode, state_owned, keep_old_form, old_name, old_format)


def migrate_list(path: Path, items: list, mode: int | None = None, *, tag: str | None = None,
                 state_owned: bool = True, keep_old_form: bool = True, old_name=None, old_format=None) -> None:
    """Like `migrate` for a file that holds a JSON list."""
    _move(Path(path), list(items), tag or tag_of(path), mode, state_owned, keep_old_form, old_name, old_format)


def _write_xml(path: Path, data, t: str, mode: int, state_owned: bool, is_list: bool) -> None:
    xp = xml_path(path)
    if xp.exists():                                 # never overwrite what could not be read
        try:
            loads_list(xp.read_bytes(), t) if is_list else loads(xp.read_bytes(), t, keep_version=True)
        except (OSError, StateXmlError):
            quarantine(path)
    _mkdir(xp.parent)
    _put(xp, dumps_list(t, data) if is_list else dumps(t, data), mode, state_owned)
    clear_problem(xp)
    clear_problem(path)


def _save(path: Path, data, tag: str | None, mode: int | None, state_owned: bool, is_list: bool,
          keep_old_form: bool, old_name, old_format) -> None:
    path = Path(path)
    m = _DEFAULT_MODE if mode is None else mode
    _write_xml(path, data, tag or tag_of(path), m, state_owned, is_list)
    if keep_old_form:
        jp = _json_path(path, old_name)
        try:
            _keep_old(path, data, m, state_owned, old_name, old_format)
        except (OSError, TypeError, ValueError) as e:      # the XML form is saved; the old form is a rollback aid
            note_problem(jp, f"old form could not be written: {e}")
        else:
            clear_problem(jp)


def write(path: Path, obj: dict, mode: int | None = None, *, tag: str | None = None, state_owned: bool = True,
          keep_old_form: bool = True, old_name=None, old_format=None) -> None:
    """Save `obj` as state file `path`'s XML form, then its JSON form as the older release wrote it (same `mode` and
    owner; written after the XML, so a stop leaves the XML as the truth) and the hash of that JSON, so a later read can
    tell an older release changed it. An XML file that cannot be read is kept as `<name>.xml.unreadable`. `mode` and
    `state_owned` as `read`. `keep_old_form=False` is for a cache an older release rebuilds by itself. `old_name` is the
    older release's file name when it differs (usage.jsonl); `old_format` the json.dumps keyword arguments, or a function
    that returns the bytes, when the older writer did not use this copy's OLD_FORMAT."""
    _save(path, obj, tag, mode, state_owned, False, keep_old_form, old_name, old_format)


def write_list(path: Path, items: list, mode: int | None = None, *, tag: str | None = None,
               state_owned: bool = True, keep_old_form: bool = True, old_name=None, old_format=None) -> None:
    """Like `write` for a file that holds a JSON list."""
    _save(path, list(items), tag, mode, state_owned, True, keep_old_form, old_name, old_format)


# ---- end of the shared block ----


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
    for p in (xml_path(path), Path(path), _sha_path(Path(path))):
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
