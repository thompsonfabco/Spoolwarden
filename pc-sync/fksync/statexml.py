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
import hashlib
import json
import logging
import math
import os
import re
import stat
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
OLD_FORMAT = {"indent": 2, "sort_keys": True}     # how the older Sync wrote its config and state (store.py, sync.py)


def _put(path: Path, data: bytes, mode: int, state_owned: bool) -> None:      # state_owned is the Hub's: unused here
    atomic_write(path, data, mode)


def _mkdir(path: Path) -> None:
    Path(path).mkdir(parents=True, exist_ok=True)


def _plain(path: Path):
    try:
        data = json.loads(_read_regular(Path(path)).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _plain_list(path: Path):
    try:
        data = json.loads(_read_regular(Path(path)).decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, list) else None


KEEP_UNREADABLE = 5       # copies of a set-aside file kept: `.unreadable` (newest), `.unreadable.1` ... `.unreadable.4` (oldest)


def quarantine(path: Path) -> bool:
    """Keep the XML form of `path` (its JSON name, or the XML name) as `<name>.xml.unreadable` so the next write does
    not destroy it, and forget its problem. An earlier copy is not replaced: it is renamed `<name>.xml.unreadable.1`
    (an older one `.2`, ...), the newest copy always takes `.unreadable`. At most KEEP_UNREADABLE copies are kept; the
    oldest one beyond that is deleted and noted as a problem. True when there was a file to keep."""
    xp = xml_path(path)
    if not xp.is_file():
        return False
    names = [xp.with_name(xp.name + ".unreadable" + (f".{i}" if i else "")) for i in range(KEEP_UNREADABLE)]
    dropped = None
    try:
        if names[-1].exists():
            names[-1].unlink()
            dropped = names[-1]
        for i in range(KEEP_UNREADABLE - 1, 0, -1):
            if names[i - 1].exists():
                os.replace(names[i - 1], names[i])
        os.replace(xp, names[0])
    except OSError:
        return False
    clear_problem(xp)
    if dropped is not None:
        note_problem(dropped, f"the oldest set-aside copy was deleted (only {KEEP_UNREADABLE} are kept)")
    return True


# ---- shared block: identical in both copies (a test holds them to it); OLD_FORMAT, _put, _mkdir are each copy's own ----

def _json_path(path: Path, old_name) -> Path:
    """The old (JSON) form of state file `path`: its own name, or `old_name` (a file name next to it, or a path) when the
    old release used another name (usage.jsonl, station_server.json)."""
    return Path(path) if not old_name else Path(path).parent / old_name


def _old_bytes(data, old_format) -> bytes:
    """`data` as the older release wrote it. `old_format` is the keyword arguments of json.dumps (the default OLD_FORMAT of
    this copy), or a function that returns the bytes (a file that was not one JSON document). A newline follows when an
    indent was used, as the older writers did. A file written by a function is not read back: a change an older release
    makes to it (lines appended to usage.jsonl) is not noticed, and the next save writes it again."""
    if callable(old_format):
        return old_format(data)
    kw = OLD_FORMAT if old_format is None else old_format
    text = json.dumps(data, **kw)
    return (text + "\n" if kw.get("indent") is not None else text).encode("utf-8")


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha_path(jp: Path) -> Path:
    return jp.with_name(jp.name + ".sha256")


def _read_regular(path: Path) -> bytes:
    """The bytes of a regular file, read through ONE descriptor opened without following a link (O_NOFOLLOW), so a link
    swapped in during the call is never followed; a link, or anything that is not a regular file (a pipe, a folder), is
    refused with OSError and noted as a problem. A missing file raises FileNotFoundError as `read_bytes` does. Windows
    has no O_NOFOLLOW: there it falls back to the plain open (the regular-file check still applies)."""
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError as e:
        if e.errno in (errno.ELOOP, getattr(errno, "EMLINK", errno.ELOOP)):      # O_NOFOLLOW met a link
            note_problem(path, "is a link, not a regular file: not read")
        raise
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            note_problem(path, "is not a regular file: not read")
            raise OSError(errno.EINVAL, "not a regular file", str(path))
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                return b"".join(chunks)
            chunks.append(b)
    finally:
        os.close(fd)


def _recorded(jp: Path) -> str | None:
    """The hash of the JSON form as this code last wrote it, or None (nothing recorded yet)."""
    try:
        return _read_regular(_sha_path(jp)).decode("ascii").strip() or None
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
    deleted the file (the way it says "none"): the XML form is removed (kept only if the removal fails, with the hash and a noted problem), the hash cleared
    and `_GONE` returned. A store that cannot be rewritten still returns the data."""
    jp = _json_path(path, old_name)
    want = _recorded(jp)
    if want is None:
        return None
    try:
        raw = _read_regular(jp)
    except FileNotFoundError:
        try:                                    # a deletion is a deletion: no copy is kept (it may hold a secret)
            xml_path(path).unlink(missing_ok=True)
        except OSError as e:                    # not removed: keep the hash, so the next read tries again
            note_problem(path, f"deleted by an older release, but the XML form could not be removed: {e}")
            return _GONE                        # still none: the stale XML must not answer for a deleted file
        try:
            _sha_path(jp).unlink()
        except OSError:
            pass
        clear_problem(path)
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
        raw = _read_regular(xp)
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
                have = _read_regular(jp)
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
            raw = _read_regular(xp)
            loads_list(raw, t) if is_list else loads(raw, t, keep_version=True)
        except StateXmlError:                       # only a file that is really broken is set aside
            quarantine(path)
        except OSError as e:                        # a read that failed (EACCES, EIO) says nothing of the file
            note_problem(xp, f"cannot read: {e.strerror or e}")
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
            logging.getLogger(__name__).warning("%s: old form could not be written: %s", jp, e)
            note_problem(jp, f"old form could not be written: {e}")
        else:
            clear_problem(jp)


def write(path: Path, obj: dict, mode: int | None = None, *, tag: str | None = None, state_owned: bool = True,
          keep_old_form: bool = True, old_name=None, old_format=None) -> None:
    """Save `obj` as state file `path`'s XML form, then its JSON form as the older release wrote it (same `mode` and
    owner; written after the XML, so a stop leaves the XML as the truth) and the hash of that JSON, so a later read can
    tell an older release changed it. An XML file that is broken (not XML, wrong root, bad value) is kept as `<name>.xml.unreadable` (earlier copies `.1`, `.2`, ... up to five). `mode` and
    `state_owned` as `read`. `keep_old_form=False` is for a cache an older release rebuilds by itself. `old_name` is the
    older release's file name when it differs (usage.jsonl); `old_format` the json.dumps keyword arguments, or a function
    that returns the bytes, when the older writer did not use this copy's OLD_FORMAT."""
    _save(path, obj, tag, mode, state_owned, False, keep_old_form, old_name, old_format)


def write_list(path: Path, items: list, mode: int | None = None, *, tag: str | None = None,
               state_owned: bool = True, keep_old_form: bool = True, old_name=None, old_format=None) -> None:
    """Like `write` for a file that holds a JSON list."""
    _save(path, list(items), tag, mode, state_owned, True, keep_old_form, old_name, old_format)


# ---- end of the shared block ----


def write_both(path: Path, obj: dict, mode: int = 0o600) -> None:
    """XML first, then the JSON form of the file (an older Sync reads that one after a rollback): what `write` does."""
    write(path, obj, mode)


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
    for p in (xml_path(path), Path(path), _sha_path(Path(path))):
        with contextlib.suppress(FileNotFoundError):
            p.unlink()
