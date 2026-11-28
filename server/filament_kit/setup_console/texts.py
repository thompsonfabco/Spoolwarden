"""The Hub page's own refusal and message sentences (D10, D13): catalogue texts, resolved for the active Hub language
via `languages.py` and falling back to the English catalogue (`shared_static/strings.js`). `say(key, **values)`; a key
both catalogues lack is a KeyError, so a route test finds it."""
from __future__ import annotations

import json
import re
from pathlib import Path

from .. import languages
from ..station.page import _catalogue as _english_catalogue

_PACK_CACHE: dict[tuple[str, int], dict] = {}


def _pack_filename(code: str) -> str:
    for entry in languages.catalog():
        if entry.get("code") == code and entry.get("file"):
            return str(entry["file"])
    return f"{code}.json"


def _active_catalogue() -> dict:
    """Return the active Hub language's string dict, or {} when English or unavailable."""
    try:
        hub = (languages.status().get("hub") or "en").strip()
    except Exception:
        return {}
    if not hub or hub == "en":
        return {}
    pack_path: Path | None = languages._safe_strings_file(_pack_filename(hub))
    if pack_path is None:
        return {}
    try:
        mtime_ns = pack_path.stat().st_mtime_ns
    except OSError:
        return {}
    cache_key = (str(pack_path), mtime_ns)
    cached = _PACK_CACHE.get(cache_key)
    if cached is not None:
        return cached
    try:
        data = json.loads(pack_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    _PACK_CACHE[cache_key] = data
    return data


def say(key: str, /, **values) -> str:
    """The catalogue's text of `key` in the active Hub language (falling back to English) with {product}, {command}
    and `values` filled in. A key missing from both catalogues raises KeyError."""
    en_cat = _english_catalogue()
    active_cat = _active_catalogue()
    raw = active_cat.get(key)
    if not isinstance(raw, str):
        raw = en_cat.get(key)
    if not isinstance(raw, str):
        raise KeyError(key)
    product = active_cat.get("app_name") if isinstance(active_cat.get("app_name"), str) else en_cat.get("app_name", "Spoolwarden")
    command = active_cat.get("command_name") if isinstance(active_cat.get("command_name"), str) else en_cat.get("command_name", "spoolwarden")
    subs = {"product": product, "command": command, **{k: str(v) for k, v in values.items()}}
    return re.sub(r"\{(\w+)\}", lambda m: subs.get(m.group(1), m.group(0)), raw)


__all__ = ["say"]
