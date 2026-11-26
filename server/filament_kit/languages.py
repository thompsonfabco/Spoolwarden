"""Language pack catalog, Hub language state, and localized strings.js generation.

State is stored in `config.paths().state / "languages.xml"`:

    <language_state version="1" hub="en" removed=""/>

Written atomically under `locks.ProcessLock` without following symlinks, matching
`voice_state.py`. Bundled languages whose `.json` file exists in `strings/` are
installed by default unless explicitly listed in `removed`.
"""
from __future__ import annotations

import errno
import json
import logging
import os
import re
import stat
from pathlib import Path
from xml.sax.saxutils import quoteattr

import defusedxml.ElementTree as DET
from defusedxml import DefusedXmlException

from . import config, locks, pairing

log = logging.getLogger("filament_kit.languages")

STATE_FILE = "languages.xml"
STATE_VERSION = "1"
STATE_MODE = 0o644
PLURAL_FORMS = ("zero", "one", "two", "few", "many", "other")
_LANG_CODE_RE = re.compile(r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$")

_LOCK = locks.ProcessLock(lambda: config.paths().state / "languages.lock")


class LanguageError(Exception):
    """Error raised when a language operation is invalid or unavailable."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


def _strings_dir() -> Path:
    raw = os.environ.get("FK_STRINGS_DIR")
    base = Path(raw) if raw else (Path(__file__).resolve().parents[2] / "strings")
    return base.resolve()


def _safe_strings_file(filename: str) -> Path | None:
    if not filename or "/" in filename or "\\" in filename:
        return None
    sdir = _strings_dir()
    candidate = (sdir / filename).resolve()
    if not candidate.is_relative_to(sdir) or candidate == sdir:
        return None
    return candidate


def _read_xml_nofollow(path: Path, limit: int = 262144) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_BINARY", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as e:
        if e.errno == errno.ELOOP:
            raise ValueError(f"{path.name} is a symbolic link") from None
        raise
    with os.fdopen(fd, "rb") as fh:
        return fh.read(limit)


def _parse_int(raw: str | None, default: int = 0) -> int:
    if raw is None:
        return default
    raw = raw.strip()
    if re.fullmatch(r"[0-9]{1,3}", raw):
        return max(0, min(100, int(raw)))
    return default


def catalog() -> list[dict]:
    """Parse `strings/languages.xml` and return the language catalog entries."""
    sdir = _strings_dir()
    xml_path = (sdir / "languages.xml").resolve()
    if not xml_path.is_relative_to(sdir):
        return []
    try:
        data = _read_xml_nofollow(xml_path)
    except (OSError, ValueError) as e:
        log.warning("languages catalog not read: %s", e)
        return []
    if data is None:
        return []
    try:
        root = DET.fromstring(data)
    except (DET.ParseError, DefusedXmlException, UnicodeDecodeError) as e:
        log.warning("languages catalog invalid XML: %s", e)
        return []
    if root.tag != "languages":
        return []

    entries: list[dict] = []
    for elem in root.findall("language"):
        code = (elem.get("code") or "").strip()
        if not code:
            continue
        name = (elem.get("name") or code).strip()
        native = (elem.get("native") or name).strip()
        filename = (elem.get("file") or f"{code}.json").strip()
        reviewed = (elem.get("reviewed") or "false").strip().lower() == "true"
        complete = _parse_int(elem.get("complete"), 0)
        builtin = (elem.get("builtin") or "false").strip().lower() == "true"
        entries.append({
            "code": code,
            "name": name,
            "native": native,
            "file": filename,
            "reviewed": reviewed,
            "complete": complete,
            "builtin": builtin,
        })
    return entries


def _state_file() -> Path:
    return config.paths().state / STATE_FILE


def _read_state() -> tuple[str, set[str]]:
    """Return `(hub, removed)` from `state/languages.xml`, falling back to `('en', set())`."""
    f = _state_file()
    try:
        data = _read_xml_nofollow(f, limit=65536)
    except (OSError, ValueError) as e:
        log.warning("language state not read, defaults used: %s", e)
        return "en", set()
    if data is None:
        return "en", set()
    try:
        root = DET.fromstring(data)
    except (DET.ParseError, DefusedXmlException, UnicodeDecodeError) as e:
        log.warning("language state invalid XML, defaults used: %s", e)
        return "en", set()
    if root.tag != "language_state":
        return "en", set()
    hub = (root.get("hub") or "en").strip() or "en"
    raw_removed = (root.get("removed") or "").strip()
    removed = {c.strip() for c in raw_removed.split(",") if c.strip() and c.strip() != "en"}
    return hub, removed


def _write_state(hub: str, removed: set[str]) -> None:
    f = _state_file()
    try:
        if stat.S_ISLNK(os.lstat(f).st_mode):
            raise OSError(errno.ELOOP, f"{f.name} is a symbolic link")
    except FileNotFoundError:
        pass
    pairing.make_state_dir(f.parent)
    removed_str = ",".join(sorted(c for c in removed if c and c != "en"))
    xml_text = (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<language_state version={quoteattr(STATE_VERSION)} '
        f'hub={quoteattr(hub)} removed={quoteattr(removed_str)}/>\n'
    )
    pairing._write_atomic(f, xml_text.encode("utf-8"), STATE_MODE, state_owned=True)


def _has_pack_file(entry: dict) -> bool:
    path = _safe_strings_file(entry["file"])
    return path is not None and path.is_file()


def _build_status(entries: list[dict], hub: str, removed: set[str]) -> dict:
    available: list[dict] = []
    installed_codes: set[str] = set()
    for entry in entries:
        code = entry["code"]
        installed = (code == "en") or (_has_pack_file(entry) and code not in removed)
        if installed:
            installed_codes.add(code)
        available.append({
            "code": code,
            "name": entry["native"],
            "reviewed": entry["reviewed"],
            "installed": installed,
            "complete": entry["complete"],
        })
    effective_hub = hub if hub in installed_codes else "en"
    languages = [item for item in available if item["installed"]]
    return {
        "hub": effective_hub,
        "languages": languages,
        "available": available,
    }


def status() -> dict:
    """Return current Hub language status (`hub`, `languages`, `available`)."""
    entries = catalog()
    hub, removed = _read_state()
    return _build_status(entries, hub, removed)


def _find_entry(entries: list[dict], code: str) -> dict | None:
    for entry in entries:
        if entry["code"] == code:
            return entry
    return None


def set_hub_language(code: str) -> dict:
    """Set the Hub's default language to `code` if installed (or `'en'`)."""
    if not isinstance(code, str) or not code.strip():
        raise LanguageError(400, "invalid_language", "Language code is required")
    code = code.strip()
    entries = catalog()
    entry = _find_entry(entries, code)
    if entry is None:
        raise LanguageError(404, "language_not_found", f"Unknown language: {code}")

    with _LOCK:
        _, removed = _read_state()
        installed = (code == "en") or (_has_pack_file(entry) and code not in removed)
        if not installed:
            raise LanguageError(400, "language_not_installed", f"Language {code} is not installed")
        _write_state(code, removed)
        return _build_status(entries, code, removed)


def install_language(code: str) -> dict:
    """Install (or re-enable) a language pack from `strings/`."""
    if not isinstance(code, str) or not code.strip():
        raise LanguageError(400, "invalid_language", "Language code is required")
    code = code.strip()
    entries = catalog()
    entry = _find_entry(entries, code)
    if entry is None:
        raise LanguageError(404, "language_not_found", f"Unknown language: {code}")
    if code != "en" and not _has_pack_file(entry):
        raise LanguageError(404, "language_file_missing", f"Language pack file for {code} is missing")

    with _LOCK:
        hub, removed = _read_state()
        removed.discard(code)
        _write_state(hub, removed)
        return _build_status(entries, hub, removed)


def remove_language(code: str) -> dict:
    """Remove an installed non-English, non-active-Hub language pack."""
    if not isinstance(code, str) or not code.strip():
        raise LanguageError(400, "invalid_language", "Language code is required")
    code = code.strip()
    if code == "en":
        raise LanguageError(400, "cannot_remove_builtin", "English cannot be removed")
    entries = catalog()
    entry = _find_entry(entries, code)
    if entry is None:
        raise LanguageError(404, "language_not_found", f"Unknown language: {code}")

    with _LOCK:
        hub, removed = _read_state()
        if code == hub:
            raise LanguageError(409, "language_is_hub", f"Cannot remove the active Hub language ({code})")
        removed.add(code)
        _write_state(hub, removed)
        return _build_status(entries, hub, removed)


def _normalize_strings(raw: dict) -> dict:
    out: dict = {}
    for key, value in raw.items():
        if isinstance(value, dict):
            plural_obj = {q: str(value[q]) for q in PLURAL_FORMS if q in value and value[q] is not None}
            if plural_obj:
                out[key] = plural_obj
        elif value is not None:
            out[key] = str(value)
    return out


def strings_js(code: str) -> bytes:
    """Return `window.SW_TEXT` override script bytes for an installed non-English language."""
    if not isinstance(code, str) or not code.strip():
        raise LanguageError(404, "language_not_found", "Language not found")
    code = code.strip()
    if code == "en":
        raise LanguageError(404, "language_not_found", "English is built in")

    entries = catalog()
    entry = _find_entry(entries, code)
    if entry is None:
        raise LanguageError(404, "language_not_found", f"Unknown language: {code}")

    _, removed = _read_state()
    if code in removed:
        raise LanguageError(404, "language_not_installed", f"Language {code} is not installed")

    pack_path = _safe_strings_file(entry["file"])
    if pack_path is None or not pack_path.is_file():
        raise LanguageError(404, "language_file_missing", f"Language pack file for {code} is missing")

    try:
        raw_data = json.loads(pack_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
        raise LanguageError(404, "language_file_invalid", f"Language pack for {code} could not be read: {e}") from None

    if not isinstance(raw_data, dict):
        raise LanguageError(404, "language_file_invalid", f"Language pack for {code} is not a JSON object")

    normalized = _normalize_strings(raw_data)
    payload = json.dumps(normalized, ensure_ascii=False)
    script = f"window.SW_TEXT = Object.freeze(Object.assign({{}}, window.SW_TEXT || {{}}, {payload}));\n"
    return script.encode("utf-8")


def language_for(request) -> str:
    """Resolve the active language for a request: cookie -> Accept-Language -> Hub setting -> 'en'."""
    cookies = getattr(request, "cookies", None) or {}
    headers = getattr(request, "headers", None) or {}
    want = (cookies.get("spoolwarden_lang") or "").strip()
    accept_header = headers.get("accept-language") or headers.get("Accept-Language") or ""
    accepted = [
        c
        for c in (p.split(";")[0].strip().lower() for p in accept_header.split(","))
        if c and c != "*"
    ]
    if want == "en" or (want in ("", "auto") and accepted[:1] and accepted[0].split("-")[0] == "en"):
        return "en"

    st = status()
    have = {
        item["code"].lower(): item["code"]
        for item in st.get("languages", [])
        if item.get("installed") and isinstance(item.get("code"), str) and _LANG_CODE_RE.fullmatch(item["code"].lower())
    }
    if want.lower() in have:
        return have[want.lower()]
    for code in accepted:
        if code in have:
            return have[code]
        base = code.split("-")[0]
        if base == "en":
            return "en"
        if base in have:
            return have[base]
    hub = (st.get("hub") or "").lower()
    return have.get(hub, "en")
