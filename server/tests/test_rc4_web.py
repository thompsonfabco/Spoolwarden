"""the web app's language, demo banner, print page and label, the push language and the Bambu Lab
add by address. No network, no Hub: the kit API is a fake that records what it was asked."""
from __future__ import annotations

import io
import json
import sys

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
        if path == "/api/hub":
            self._check()
            self.calls.append(("GET", path))
            return {"name": "Hub", "demo": self.demo}
        return super().get(path)

    def post(self, path, body):
        if path == "/api/demo/leave":
            self._check()
            self.calls.append(("POST", path, body))
            if not self.demo:                                           # as the Hub's route: 409 when the demo is off
                raise SlotError("The demo is not on.", code="not_in_demo", http=409)
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
    assert "<html lang=de " in r.text and "/intake/api/strings/de.js'" in r.text
    # English first, the language after it (its texts replace English when they load)
    assert r.text.index("/intake/shared/strings.js'") < r.text.index("/intake/api/strings/de.js'") < r.text.index("/intake/shared/components.js'")


def test_a_non_english_page_keeps_a_working_catalogue_when_the_hub_goes_down(env):
    """The page chose German while the Hub answered; with the Hub down its German script answers 502, and the page
    still loads the English catalogue before it, so no screen shows bare keys."""
    kit = RcKit()
    c = signed(make_app(env, kit))
    h = {"Accept-Language": "de"}
    assert "<html lang=de " in c.get("/intake", headers=h).text
    kit.down = True
    r = c.get("/intake", headers=h)
    assert r.status_code == 200 and "<html lang=de " in r.text            # the choice the Hub gave last
    assert r.text.index("/intake/shared/strings.js'") < r.text.index("/intake/api/strings/de.js'")
    assert c.get("/intake/api/strings/de.js").status_code == 502          # this one fails; English is already in
    assert c.get("/intake/shared/strings.js").status_code == 200
    labels = c.get("/intake/labels?ids=3", headers=h).text
    assert labels.index("/intake/shared/strings.js'") < labels.index("/intake/api/strings/de.js'")


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


def test_leave_demo_keeps_the_hubs_refusal(env):
    kit = RcKit()                                                       # the demo is off: the Hub's route answers 409
    r = signed(make_app(env, kit)).post("/intake/api/demo/leave", headers=H, json={})
    assert r.status_code == 409 and r.json() == {"error": "not_in_demo", "message": "The demo is not on."}


def test_leaving_drops_the_banner_a_page_load_cached_while_the_hub_was_leaving(env):
    """A page load during the leave read the Hub while the demo was still on; the reload after Leave demo has no banner."""
    kit = RcKit()
    kit.demo = True
    c = signed(make_app(env, kit))
    assert "data-demo='1'" in c.get("/intake").text
    leave = RcKit.post

    def slow_leave(path, body):
        if path == "/api/demo/leave":
            assert "data-demo='1'" in c.get("/intake").text             # another tab loads while the Hub is still leaving
        return leave(kit, path, body)
    kit.post = slow_leave
    assert c.post("/intake/api/demo/leave", headers=H, json={}).json() == {"demo": False}
    assert "data-demo='1'" not in c.get("/intake").text


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


FIVE = ("62x29", "50x30", "a4-63x34", "a4-70x37", "letter-66x25")
BAD_SIZE = "The size must be 62x29, 50x30, a4-63x34, a4-70x37 or letter-66x25."


def test_a_label_is_the_hubs_svg_at_one_of_the_five_sizes(env):
    kit = RcKit()
    c = signed(make_app(env, kit))
    r = c.get("/intake/api/spools/214/label.svg")
    assert r.status_code == 200 and r.content == SVG and r.headers["content-type"] == "image/svg+xml"
    assert "sandbox" in r.headers["content-security-policy"] and asked(kit, "/api/spools/214/label.svg?size=62x29")
    for size in FIVE:
        assert c.get(f"/intake/api/spools/214/label.svg?size={size}").status_code == 200, size
        assert asked(kit, f"/api/spools/214/label.svg?size={size}")
    for bad in ("sheet24", "a4", "A4-63X34"):                          # sheet24 never shipped and is dropped
        r = c.get(f"/intake/api/spools/214/label.svg?size={bad}")
        assert r.status_code == 400 and r.json() == {"error": "bad_request", "message": BAD_SIZE}, bad      # the twins' shape
    assert signed(make_app(env, kit), AWAY).get("/intake/api/spools/214/label.svg").status_code == 403


def test_the_twins_new_refusals_are_sentences_in_the_twins_shape(env, monkeypatch):
    kit = RcKit()
    c = signed(make_app(env, kit))
    assert c.get("/intake/api/strings/DE.js").json() == {"error": "not_found", "message": "There is no such language."}
    monkeypatch.setattr(sys.modules[__name__], "SVG", b"<svg/>" + b" " * (1 << 20))
    r = c.get("/intake/api/spools/214/label.svg")
    assert r.status_code == 413 and r.json() == {"error": "too_large", "message": "The Hub's answer is too large to pass on."}


def test_the_print_page(env, monkeypatch):
    from filament_kit import config, pairing
    monkeypatch.setattr(pairing, "short_hostname", lambda: "workshop")
    cfg = config.load()
    cfg["port"] = 443
    config.save(cfg)
    kit = RcKit()
    c = signed(make_app(env, kit))
    r = c.get("/intake/labels?ids=3,14&size=50x30")
    assert r.status_code == 200 and "data-ids='3,14'" in r.text and "data-size='50x30'" in r.text
    assert "/intake/labels.js'" in r.text and "labels__print" in r.text
    # the line about the printed address names the Hub's home address the codes hold, not the page's origin
    assert "data-address='https://workshop.local'" in r.text
    assert "/intake/labels/page.css?size=50x30'" in r.text and "data-demo" not in r.text
    assert c.get("/intake/labels?ids=3").text.count("data-size='62x29'") == 1
    for size in FIVE:
        assert c.get(f"/intake/labels?ids=3&size={size}").status_code == 200, size
    for q in ("ids=", "ids=a", "ids=1,,2", "ids=1&size=a4", "ids=1&size=sheet24", "", "ids=" + ",".join(["1"] * 101),
              "ids=0", "ids=3,0"):                                     # whole numbers of 1 or more, as the refusal says
        assert c.get("/intake/labels?" + q).status_code == 400, q
    assert c.get("/intake/labels?ids=0").text.startswith("Give the spool numbers")
    assert c.get("/intake/labels?ids=1&size=sheet24").text == BAD_SIZE
    assert signed(make_app(env, kit), AWAY).get("/intake/labels?ids=3").status_code == 403
    js = c.get("/intake/labels.js")
    assert js.status_code == 200 and b"label.svg" in js.content and b"window.print()" in js.content
    kit.demo = True
    demo = signed(make_app(env, kit)).get("/intake/labels?ids=3").text
    assert "data-demo='1'" in demo                                       # the demo banner on the print page too


def test_the_print_page_states_its_paper(env):
    """A sheet's paper is stated with margin 0; a roll prints each label on a page of the label's size."""
    c = signed(make_app(env, RcKit()))
    want = {"62x29": "62mm 29mm", "50x30": "50mm 30mm", "a4-63x34": "A4", "a4-70x37": "A4", "letter-66x25": "letter"}
    for size, paper in want.items():
        r = c.get(f"/intake/labels/page.css?size={size}")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/css")
        assert r.text.strip() == f"@page {{ size: {paper}; margin: 0; }}", size
    r = c.get("/intake/labels/page.css?size=sheet24")
    assert r.status_code == 400 and r.json()["message"] == BAD_SIZE


def _rule(css: str, selector: str) -> dict:
    import re
    body = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css).group(1)
    return {k.strip(): v.strip() for k, _, v in (d.partition(":") for d in body.split(";")) if v.strip()}


def test_a_sheet_lays_its_labels_by_the_tables_margins_and_pitches():
    """The design, components's table: left and top margin, pitch across and down, label size, 3 across, page size, no padding
    of the page's own at print; the print page's script offers the five sizes in a select with the line for a sheet."""
    from pathlib import Path
    static = Path(__file__).resolve().parents[1] / "filament_kit" / "web" / "static"
    css = (static / "app.css").read_text(encoding="utf-8")
    table = {"a4-63x34": (210, 297, 63.5, 33.9, 7.25, 12.9, 66.0, 33.9), "a4-70x37": (210, 297, 70, 37, 0, 0.5, 70, 37),
             "letter-66x25": (215.9, 279.4, 66.675, 25.4, 4.7625, 12.7, 69.85, 25.4)}
    mm = lambda v: 0.0 if v == "0" else float(v.removesuffix("mm"))      # noqa: E731
    for size, (pw, ph, w, h, left, top, across, down) in table.items():
        r = _rule(css, f".labels__page--{size}")
        pad = (r["padding"].split() + ["0", "0", "0"])[:4]
        if len(r["padding"].split()) == 3:
            pad[3] = pad[1]
        assert (mm(r["width"]), mm(r["height"])) == (pw, ph), size
        assert (mm(pad[0]), mm(pad[3])) == (top, left), size
        assert r["grid-template-columns"] == f"repeat(3, {w:g}mm)" and r["grid-auto-rows"] == f"{h:g}mm", size
        assert abs(mm(r.get("column-gap", "0")) - (across - w)) < 1e-9, size        # the pitch across
        assert down == h                                                              # rows touch: the pitch down is the label
    assert _rule(css, ".labels__page")["row-gap"] == "0"
    js = (static / "labels.js").read_text(encoding="utf-8")
    assert 'type: "select"' in js and "SW.segmented" not in js and 'tx("labels_size")' in js
    for size in FIVE:
        assert f'value: "{size}"' in js
    assert "per: 24" in js and "per: 30" in js and "labels_actual_size" in js
    assert "Print at actual size (100%), not fitted to the page." in js
    assert "root.dataset.address || location.origin" in js and 'setAttribute("aria-label"' in js and 'setAttribute("role", "img")' in js
    assert "sheet24" not in js and "sheet24" not in css


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


class LangEventKit:
    """GET api/events as the Hub answers it, and with `lang` the same event in that language; `fail` languages raise."""

    def __init__(self, events, fail=()):
        self.events, self.fail, self.asked = list(events), set(fail), []

    def get(self, path):
        from urllib.parse import parse_qs, urlparse
        q = {k: v[0] for k, v in parse_qs(urlparse(path).query).items()}
        lang = q.get("lang")
        self.asked.append(lang)
        if lang in self.fail:
            raise SlotError("the kit server is not reachable", http=None)
        out = [dict(e, **({"title": f"[{lang}] {e['title']}", "text": f"[{lang}] {e['text']}"} if lang else {}))
               for e in self.events if e["id"] > int(q.get("since", 0))][:int(q.get("limit", 100))]
        return {"events": out, "next": out[-1]["id"] if out else int(q.get("since", 0)), "oldest": 1}


def test_an_alert_reaches_each_phone_in_its_own_language(tmp_path):
    """The stored language is used: the feed asks the Hub for the event in each subscription's language, once per language,
    and a phone whose language the Hub cannot give gets the Hub's own words."""
    store = push.Store(tmp_path / "push.xml")
    for n, lang in (("a", "de"), ("b", "de"), ("c", None), ("d", "ru")):
        store.add({"endpoint": f"https://fcm.googleapis.com/{n}", "keys": {"p256dh": "k", "auth": "a"}, "lang": lang})
    got = {}

    def sender(info, payload, topic=None, ttl=None):
        got[info["endpoint"].rsplit("/", 1)[1]] = json.loads(payload)
        return 201
    ev = {"id": 7, "kind": "print.failed", "severity": "alert", "alert": True, "condition": None, "title": "Print failed", "text": "On X1."}
    kit = LangEventKit([ev], fail={"ru"})
    feed = push.Feed(kit, store, sender, "https://x.example/intake/", wait=0)
    feed.last = 6
    feed.tick()
    assert got["a"]["title"] == got["b"]["title"] == "[de] Print failed" and got["a"]["body"] == "[de] On X1."
    assert got["c"]["title"] == "Print failed" and got["d"]["title"] == "Print failed"   # no language, or the Hub could not: its own words
    assert kit.asked.count("de") == 1 and kit.asked.count("ru") == 1                     # one question per language


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
                  'action: "print-label"', "/labels?ids=", 'api("share-corrections")',
                  'api("label-reader/corrections")', '"#recent/corrections"', "function loadCorr(", "window.SW_LANG"):
        assert words in text, words
    # one label at a time in the web app; Print labels for several is the Hub page's
    assert 'action: "print-labels"' not in text and "labels_button_many" not in text


def test_the_activity_twin_refuses_in_the_twins_shape(env):
    """A bad `newest`, `limit` or since+newest is {error: code, message: sentence}, not {error: sentence}."""
    c = signed(make_app(env, RcKit()))
    for q in ("newest=abc", "newest=0", "newest=5&since=3", "limit=ten"):
        r = c.get("/intake/api/activity?" + q)
        assert r.status_code == 400 and r.json()["error"] == "bad_request" and r.json()["message"].endswith("."), (q, r.text)
