"""Tests for logging_cfg: level normalization, handler setup, and dual-logger application."""
from __future__ import annotations

import io
import logging

import pytest

from filament_kit import config, logging_cfg


@pytest.fixture(autouse=True)
def _restore_loggers():
    fk = logging.getLogger("filament_kit")
    st = logging.getLogger("intake-station")
    fk_level, st_level = fk.level, st.level
    yield
    fk.setLevel(fk_level)
    st.setLevel(st_level)


def test_defaults_has_info_log_level():
    assert config.DEFAULTS["log_level"] == "info"
    assert logging_cfg.LEVELS == ("error", "warning", "info", "debug")


@pytest.mark.parametrize("raw,expected", [
    (None, "info"),
    ("", "info"),
    ("   ", "info"),
    ("error", "error"),
    ("ERROR", "error"),
    (" Warning ", "warning"),
    ("info", "info"),
    ("DEBUG", "debug"),
])
def test_normalize_valid(raw, expected):
    assert logging_cfg.normalize(raw) == expected


@pytest.mark.parametrize("raw", ["trace", "verbose", "warn", "1", 5, True])
def test_normalize_invalid(raw):
    with pytest.raises(ValueError):
        logging_cfg.normalize(raw)


@pytest.mark.parametrize("raw,expected", [
    (None, logging.INFO),
    ("", logging.INFO),
    ("error", logging.ERROR),
    ("warning", logging.WARNING),
    ("info", logging.INFO),
    ("debug", logging.DEBUG),
])
def test_to_int(raw, expected):
    assert logging_cfg.to_int(raw) == expected


def test_apply_level_updates_both_loggers():
    lvl = logging_cfg.apply_level("debug")
    assert lvl == logging.DEBUG
    assert logging.getLogger("filament_kit").level == logging.DEBUG
    assert logging.getLogger("intake-station").level == logging.DEBUG

    lvl = logging_cfg.apply_level("error")
    assert lvl == logging.ERROR
    assert logging.getLogger("filament_kit").level == logging.ERROR
    assert logging.getLogger("intake-station").level == logging.ERROR


def test_configure_sets_handler_without_duplication():
    name = "filament_kit.test_logging_cfg_unique"
    logger = logging.getLogger(name)
    logger.handlers.clear()
    buf = io.StringIO()
    try:
        lvl = logging_cfg.configure({"log_level": "warning"}, stream=buf, logger_name=name)
        assert lvl == logging.WARNING
        assert len(logger.handlers) == 1
        assert logger.propagate is False
        assert logging.getLogger("filament_kit").level == logging.WARNING
        assert logging.getLogger("intake-station").level == logging.WARNING

        # Calling configure again must not add a second handler
        logging_cfg.configure({"log_level": "debug"}, stream=buf, logger_name=name)
        assert len(logger.handlers) == 1
        assert logging.getLogger("filament_kit").level == logging.DEBUG
        assert logging.getLogger("intake-station").level == logging.DEBUG

        logger.setLevel(logging.INFO)
        logger.info("hello world")
        assert buf.getvalue().strip() == f"INFO {name}: hello world"
    finally:
        logger.handlers.clear()
