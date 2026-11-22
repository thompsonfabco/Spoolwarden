"""RC4 (R85 sections 3 to 5, R87 2 and 3): the web app's language, demo banner, print page and label, the push language and the Bambu Lab
add by address. No network, no Hub: the kit API is a fake that records what it was asked."""
from __future__ import annotations

import io
import json

import pytest

pytest.importorskip("mcp")

from filament_kit.web import push  # noqa: E402
from filament_kit.web.slots import SlotError  # noqa: E402
from test_web_printers import AWAY, H, HOME, ManagingKit, ORIGIN, SERIAL, env, make_app, signed  # noqa: E402,F401
from starlette.testclient import TestClient  # noqa: E402

SVG = b"<svg xmlns='http://www.w3.org/2000/svg' width='62mm' height='29mm'/>"
STRINGS = b"window.SW_TEXT = Object.freeze({\"app_name\": \"Spoolwarden\"});\n"
LANGS = {"hub": "de", "languages": [
    {"code": "de", "name": "Deutsch", "reviewed": False, "installed": True, "complete": 100},
    {"code": "ru", "name": "Русский", "reviewed": True, "installed": False, "complete": 80}]}


class RcKit(ManagingKit):
    def __init__(self):
        super().__init__()
        self.demo = False
        self.languages = LANGS

    def get(self, path):
        if path == "/api/languages":
            self._check()
            self.calls.append(("GET", path))
            return self.languages
        if path == "/api/info":
            self._check()
            self.calls.append(("GET", path))
            return {"name": "Hub", "api": 1, "demo": self.demo}
        return super().get(path)

    def post(self, path, body):
        if path == "/api/demo/leave":
            self.calls.append(("POST", path, body))
            self.demo = False
            return {"demo": False}
        return super().post(path, body)

    def raw(self, path):
        self._check()
        self.calls.append(("RAW", path))
        if path.startswith("/api/strings/"):
            return 200, "text/javascript", io.BytesIO(STRINGS)
        if "/label.svg" in path:
            return 200, "image/svg+xml", io.BytesIO(SVG)
        return super().raw(path)


def page(env, kit, headers=None, cookies=None):
    c = signed(make_app(env, kit))
    h = dict(headers or {})
    if cookies:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return c.get("/intake", headers=h)


def asked(kit, path):
    return any(c[1] == path for c in kit.calls)


# ---------- the page's language ----------

def test_the_page_follows_the_phones_language_when_the_hub_has_the_pack(env):
    kit = RcKit()
    r = page(env, kit, {"Accept-Language": "de-DE,de;q=0.9,en;q=0.5"})
    assert "<html lang=de " in r.text and "/intake/api/strings/de.js'" in r.text and "/shared/strings.js" not in r.text


def test_a_phone_that_asks_for_english_gets_english_and_costs_the_hub_no_question(env):
    kit = RcKit()
    r = page(env, kit, {"Accept-Language": "en-US,en;q=0.9"})
    assert "<html lang=en " in r.text and "/intake/shared/strings.js'" in r.text
    assert not asked(kit, "/api/languages")


def test_a_language_the_hub_has_not_falls_back_to_the_hubs_own(env):
    kit = RcKit()
    r = page(env, kit, {"Accept-Language": "fr-FR"})
    assert "<html lang=de " in r.text                                 # no pack for French: the Hub's language (here German)
    r = page(env, kit, {"Accept-Language": "ru"})                      # Russian is listed but not installed on this Hub
    assert "<html lang=de " in r.text


def test_the_choice_kept_on_the_phone_wins(env):
    kit = RcKit()
    assert "<html lang=en " in page(env, kit, {"Accept-Language": "de"}, {"spoolwarden_lang": "en"}).text
    assert "<html lang=de " in page(env, kit, {"Accept-Language": "en"}, {"spoolwarden_lang": "de"}).text
    assert "<html lang=en " in page(env, kit, {"Accept-Language": "en"}, {"spoolwarden_lang": "auto"}).text


def test_a_hub_that_does_not_answer_gives_english(env):
    kit = RcKit()
    kit.down = True
    r = page(env, kit, {"Accept-Language": "de"})
    assert r.status_code == 200 and "<html lang=en " in r.text


# ---------- the demo ----------

def test_the_demo_banner_is_asked_for_by_the_page(env):
    kit = RcKit()
    assert "data-demo='1'" not in page(env, kit).text
    kit2 = RcKit()
    kit2.demo = True
    assert "data-demo='1'" in page(env, kit2).text


def test_leave_demo_passes_on_and_needs_the_pages_origin(env):
    kit = RcKit()
    kit.demo = True
    c = signed(make_app(env, kit))
    assert c.post("/intake/api/demo/leave", json={}).status_code == 403
    r = c.post("/intake/api/demo/leave", headers=H, json={})
    assert r.status_code == 200 and r.json() == {"demo": False} and ("POST", "/api/demo/leave", {}) in kit.calls


def test_leave_demo_keeps_the_hubs_refusal(env, monkeypatch):
    kit = RcKit()

    def refuse(path, body):
        raise SlotError("the demo is not on", code="not_in_demo", http=409)
    monkeypatch.setattr(kit, "post", refuse)
    r = signed(make_app(env, kit)).post("/intake/api/demo/leave", headers=H, json={})
    assert r.status_code == 409 and "not_in_demo" in r.text


# ---------- languages and labels pass through ----------

def test_the_languages_and_their_texts_come_from_the_hub(env):
    kit = RcKit()
    c = signed(make_app(env, kit))
    assert c.get("/intake/api/languages").json()["hub"] == "de"
    r = c.get("/intake/api/strings/de.js")
    assert r.status_code == 200 and r.content == STRINGS and r.headers["content-type"].startswith("text/javascript")
    assert asked(kit, "/api/strings/de.js")
    for bad in ("..js", "DE.js", "de", "de.json", "toolong-code-x.js"):
        assert c.get("/intake/api/strings/" + bad).status_code == 404, bad


def test_a_label_is_the_hubs_svg_at_one_of_three_sizes(env):
    kit = RcKit()
    c = signed(make_app(env, kit))
    r = c.get("/intake/api/spools/214/label.svg")
    assert r.status_code == 200 and r.content == SVG and r.headers["content-type"] == "image/svg+xml"
    assert "sandbox" in r.headers["content-security-policy"] and asked(kit, "/api/spools/214/label.svg?size=62x29")
    for size in ("50x30", "sheet24"):
        assert c.get(f"/intake/api/spools/214/label.svg?size={size}").status_code == 200
    assert c.get("/intake/api/spools/214/label.svg?size=a4").status_code == 400
    assert signed(make_app(env, kit), AWAY).get("/intake/api/spools/214/label.svg").status_code == 403


def test_the_print_page(env):
    kit = RcKit()
    c = signed(make_app(env, kit))
    r = c.get("/intake/labels?ids=3,14&size=50x30")
    assert r.status_code == 200 and "data-ids='3,14'" in r.text and "data-size='50x30'" in r.text
    assert "/intake/labels.js'" in r.text and "labels__print" in r.text
    assert c.get("/intake/labels?ids=3").text.count("data-size='62x29'") == 1
    for q in ("ids=", "ids=a", "ids=1,,2", "ids=1&size=a4", "", "ids=" + ",".join(["1"] * 101)):
        assert c.get("/intake/labels?" + q).status_code == 400, q
    assert signed(make_app(env, kit), AWAY).get("/intake/labels?ids=3").status_code == 403
    js = c.get("/intake/labels.js")
    assert js.status_code == 200 and b"label.svg" in js.content and b"window.print()" in js.content


# ---------- the push language ----------

def test_a_subscription_keeps_the_phones_language(tmp_path):
    store = push.Store(tmp_path / "push.xml")
    sub = {"endpoint": "https://fcm.googleapis.com/fcm/send/abc", "keys": {"p256dh": "k", "auth": "a"}}
    store.add({**sub, "lang": "de"})
    assert store.language_of(sub["endpoint"]) == "de"
    assert 'lang="de"' in (tmp_path / "push.xml").read_text(encoding="utf-8")
    store.add({**sub, "lang": "<script>"})                             # not a language code: dropped
    assert store.language_of(sub["endpoint"]) is None
    store.add(sub)
    assert store.language_of(sub["endpoint"]) is None and push.language_ok("pt-BR") == "pt-BR" and push.language_ok("x") is None


# ---------- adding a printer by its address ----------

def test_a_bambu_printer_is_added_by_the_address_typed_and_a_klipper_key_is_kept(env):
    kit = ManagingKit()
    c = TestClient(make_app(env, kit, printer_changes=True), base_url=ORIGIN, client=HOME)
    r = c.post("/intake/api/printers/add", headers=H, json={"address": "192.0.2.40", "access_code": "12345678", "name": "Bench",
                                                             "serial": SERIAL, "configured": True})
    assert r.status_code == 200
    r = c.post("/intake/api/printers/add", headers=H, json={"family": "klipper", "host": "voron.example", "port": 7125,
                                                             "api_key": "secret", "junk": 1})
    assert r.status_code == 200 and "secret" not in r.text
    posted = [c[2] for c in kit.calls if c[0] == "POST" and c[1] == "/api/printers"]
    assert posted == [{"address": "192.0.2.40", "access_code": "12345678", "name": "Bench", "serial": SERIAL},
                      {"family": "klipper", "host": "voron.example", "port": 7125, "api_key": "secret"}]


def test_the_add_sheet_sends_the_address_and_no_serial_field():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "filament_kit" / "web" / "static" / "printers.js").read_text(encoding="utf-8")
    assert "address: v(f.bAddr)" in text and "bSerial" not in text and "printer_guided_serial_missing" not in text
    assert "printer_guided_found_at" in text


def test_the_scripts_hold_the_new_places():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "filament_kit" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    for words in ("function languageGroup(", 'api("languages")', "spoolwarden.language", 'api("demo/leave"', 'tt("demo_banner")',
                  'action: "print-label"', 'action: "print-labels"', "/labels?ids=", 'api("share-corrections")',
                  'api("label-reader/corrections")', '"#recent/corrections"', "function loadCorr(", "window.SW_LANG"):
        assert words in text, words
