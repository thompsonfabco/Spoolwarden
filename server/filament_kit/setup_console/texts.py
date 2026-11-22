"""The Hub page's own refusal and message sentences (D10, D13): catalogue texts, read from the generated copy of
strings/en.json the page loads too (shared_static/strings.js). `say(key, **values)`; a key the catalogue lacks is a
KeyError, so a route test finds it."""
from __future__ import annotations

from ..station.page import say

__all__ = ["say"]
