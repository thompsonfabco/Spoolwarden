"""Language packs: catalog (`strings/languages.xml`) and translation parity, Hub console routes under
`/setup/api/languages*`, `/setup/api/settings/language`, `/setup/api/strings/{name}`, kit API routes
`/api/languages` and `/api/strings/{name}`, and the Settings > General language UI in a real headless browser."""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from filament_kit import config, pairing
from test_hub_page_audit import hub_app  # noqa: F401
from test_hub_presets_ui import _drive, hub  # noqa: F401
from test_hub_settings import PRELUDE
from test_setup_page import H, app, client, env, helper, manager, post  # noqa: F401

ROOT = Path(__file__).resolve().parents[2]
STRINGS_DIR = ROOT / "strings"
PLACEHOLDER_RE = re.compile(r"\{([a-zA-Z0-9_]+)\}")


def _placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER_RE.findall(text))


def test_languages_xml_catalog_and_translation_parity():
    xml_path = STRINGS_DIR / "languages.xml"
    assert xml_path.is_file(), f"missing {xml_path}"
    tree = ET.parse(xml_path)
    langs = {
        el.attrib["code"]: el.attrib
        for el in tree.getroot().findall(".//language")
        if "code" in el.attrib
    }
    assert {"en", "de", "ru"} <= set(langs)

    en = json.loads((STRINGS_DIR / "en.json").read_text(encoding="utf-8"))
    for code in ("de", "ru"):
        pack = STRINGS_DIR / f"{code}.json"
        if not pack.is_file():
            continue
        data = json.loads(pack.read_text(encoding="utf-8"))
        assert set(data) == set(en), f"{code}.json keys differ from en.json"
        for key, en_val in en.items():
            if isinstance(en_val, str) and isinstance(data[key], str):
                assert _placeholders(data[key]) == _placeholders(en_val), (
                    f"{code}.json placeholder mismatch for {key!r}: "
                    f"{_placeholders(data[key])} != {_placeholders(en_val)}"
                )


def test_setup_language_routes_and_strings_bundle(app):
    c = client(app)

    r = c.get("/setup/api/languages")
    assert r.status_code == 200
    status = r.json()
    assert status["hub"] == "en"
    assert isinstance(status["languages"], list) and isinstance(status["available"], list)

    r = post(c, "settings/language", {"hub": "de"})
    assert r.status_code == 200
    assert r.json()["hub"] == "de"

    r = post(c, "languages/de/remove", {})
    assert r.status_code in (200, 400, 409)

    r = post(c, "languages/de/install", {})
    assert r.status_code == 200

    for code in ("de", "ru"):
        bundle = c.get(f"/setup/api/strings/{code}.js")
        assert bundle.status_code == 200
        assert "text/javascript" in bundle.headers["content-type"]
        assert bundle.headers.get("x-content-type-options") == "nosniff"
        assert bundle.text.strip()

    assert c.get("/setup/api/strings/zz.js").status_code == 404
    assert c.get("/setup/api/strings/de").status_code == 404

    # Reset Hub language back to English
    assert post(c, "settings/language", {"hub": "en"}).status_code == 200


def test_kit_language_routes(app, monkeypatch):
    c = client(app)
    cert_path = config.paths().etc / "cert.pem"
    if not cert_path.exists():
        cert_path.parent.mkdir(parents=True, exist_ok=True)
        cert_path.write_text("stub", encoding="utf-8")
    monkeypatch.setattr(pairing, "fingerprint", lambda _: "0" * 64)
    tok = pairing.redeem_code(pairing.issue().code)
    headers = {"Authorization": f"Bearer {tok}"}

    assert c.get("/api/languages").status_code == 401
    r = c.get("/api/languages", headers=headers)
    assert r.status_code == 200
    body = r.json()
    assert "hub" in body and "languages" in body

    bundle = c.get("/api/strings/de.js", headers=headers)
    assert bundle.status_code == 200
    assert "text/javascript" in bundle.headers["content-type"]
    assert bundle.headers.get("x-content-type-options") == "nosniff"

    missing = c.get("/api/strings/zz.js", headers=headers)
    assert missing.status_code == 404


def test_settings_general_languages_dialog_and_select(hub, tmp_path):
    page = _drive(hub, tmp_path, PRELUDE + """
await loaded();
const more = await until(() => $('[data-action="language-more"]'), "Get more languages button");
more.click();
const dialog = await until(() => $(".settings__lang"), "languages dialog");
await until(() => $(".settings__lang-list") || $(".settings__lang p.muted"), "languages list");
const out = {
  dialogText: text(dialog),
  rows: [...dialog.querySelectorAll(".settings__lang-row")].map((r) => ({
    code: r.dataset.code,
    text: text(r),
  })),
  options: [...$('select[name="general.language"]').options].map((o) => ({
    value: o.value,
    label: o.textContent.trim(),
  })),
};
return out;
""")
    assert "Could not read the list of languages." not in page["dialogText"]
    by_code = {r["code"]: r["text"] for r in page["rows"]}
    assert "de" in by_code and "Deutsch" in by_code["de"]
    assert "ru" in by_code and "Русский" in by_code["ru"]

    opt_values = {o["value"] for o in page["options"]}
    assert "en" in opt_values
    assert "de" in opt_values
    assert "ru" in opt_values
