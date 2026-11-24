"""the catalogue holds exactly the texts of the address and first-run phase that the server builds (the drift test,
as for the events and alerts texts), and the constants of and say what their catalogue keys say."""
import importlib
import json
import re
from pathlib import Path

import pytest

from filament_kit import address_texts
from filament_kit.address_texts import MESSAGES, MIRRORED, say

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = json.loads((ROOT / "strings" / "en.json").read_text(encoding="utf-8"))


def _named(text: str) -> str:
    """The catalogue's {product} and {command} as the code says them."""
    return text.replace("{product}", address_texts.PRODUCT).replace("{command}", address_texts.COMMAND)


def test_the_server_texts_are_the_catalogue_texts():
    drift = {k: (v, CATALOGUE.get(k)) for k, v in MESSAGES.items() if CATALOGUE.get(k) != v}
    assert not drift, f"server text and catalogue differ for: {drift}"
    assert len(MESSAGES) >= 130


def test_no_text_holds_the_products_or_the_commands_name():
    """The catalogue holds the names once (app_name, command_name): a text takes them as {product} and {command}."""
    for key, text in MESSAGES.items():
        for part in (text.values() if isinstance(text, dict) else [text]):
            assert address_texts.PRODUCT.lower() not in part.lower(), key


def test_a_plural_text_has_both_forms_and_the_count():
    for key, text in MESSAGES.items():
        if isinstance(text, dict):
            assert set(text) == {"one", "other"} and all("{count}" in t for t in text.values()), key
    assert say("start_line_printer_done", count=1) == "1 printer."
    assert say("start_line_printer_done", count=3) == "3 printers."


@pytest.mark.parametrize("key", sorted(MIRRORED))
def test_the_constant_of_af1_and_af2_is_its_catalogue_text(key):
    module, name = MIRRORED[key]
    constant = getattr(importlib.import_module(f"filament_kit.{module}"), name)
    assert key in CATALOGUE, key
    # `{text!r}` (a typed word shown with its quotes) is `'{text}'` in the catalogue, which has no conversions
    assert _named(MESSAGES[key]) == constant.replace("{text!r}", "'{text}'"), f"{module}.{name}"
    assert CATALOGUE[key] == MESSAGES[key]


def test_every_say_in_the_code_names_a_text():
    here = Path(address_texts.__file__).parent
    own = "".join((here / n).read_text(encoding="utf-8") for n in ("address_cli.py", "getting_started.py"))
    shared = "".join((here / n).read_text(encoding="utf-8") for n in ("status.py", "cli.py", "klipper_cli.py"))
    names = set(re.findall(r'\bsay\("([a-z_]+)"', own)) | set(re.findall(r'\bsay\(f"([a-z_]+)', own))
    names |= set(re.findall(r'\baddress_texts\.say\("([a-z_]+)"', shared))
    assert names, "no say() found"
    for name in names:
        assert name in MESSAGES or any(k.startswith(name) for k in MESSAGES), name


def test_the_fields_of_a_text_are_known():
    known = {"port", "text", "address", "name", "family", "number", "count", "done", "total", "version", "reason",
             "step", "command", "product"}
    for key, text in MESSAGES.items():
        for part in (text.values() if isinstance(text, dict) else [text]):
            assert set(re.findall(r"\{(\w+)\}", part)) <= known, key
