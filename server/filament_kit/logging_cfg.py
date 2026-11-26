"""Logging configuration: normalize log levels and apply them to the Hub and station loggers."""
from __future__ import annotations

import logging
import sys

LEVELS = ("error", "warning", "info", "debug")

_LEVEL_MAP = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
}

_LOGGERS = ("filament_kit", "intake-station")
_FORMAT = "%(levelname)s %(name)s: %(message)s"


def normalize(value) -> str:
    """Strip and lowercase `value`; default to `'info'` when None or empty; raise ValueError otherwise."""
    if value is None:
        return "info"
    if not isinstance(value, str):
        raise ValueError(f"invalid log level: {value!r}")
    s = value.strip().lower()
    if not s:
        return "info"
    if s not in _LEVEL_MAP:
        raise ValueError(f"invalid log level: {value!r}")
    return s


def to_int(value) -> int:
    """Map a log level string (or None/empty) to its `logging` integer constant."""
    return _LEVEL_MAP[normalize(value)]


def apply_level(level: str) -> int:
    """Set the logging level on both `filament_kit` and `intake-station` loggers."""
    lvl = to_int(level)
    for name in _LOGGERS:
        logging.getLogger(name).setLevel(lvl)
    return lvl


def configure(cfg: dict | None = None, *, stream=None, logger_name: str = "filament_kit") -> int:
    """Configure a StreamHandler on `logger_name` without duplicating handlers and apply the configured level."""
    raw = cfg.get("log_level") if isinstance(cfg, dict) else None
    lvl = apply_level(raw)
    target = logging.getLogger(logger_name)
    if not target.handlers:
        handler = logging.StreamHandler(stream if stream is not None else sys.stderr)
        handler.setFormatter(logging.Formatter(_FORMAT))
        target.addHandler(handler)
        target.propagate = False
    return lvl
