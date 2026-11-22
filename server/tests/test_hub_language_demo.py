"""RC3 (R85 sections 3.5, 3.7, 3.11, 4): the Hub page's Language (Settings > General and Appearance, the page's `lang`), the demo banner
with Leave demo on every section, and Try the demo in Getting started. The Hub is the stub of hub_language_stub.py (the server steps RC2
and RC6 are not in this tree); the page is the real Hub page. The same skip rules as the page audit: no Node 22 or no browser is a skip
(a failure with AUT5_REQUIRED=1)."""
from __future__ import annotations

import pytest

from hub_language_stub import NOT_EMPTY, drive, hub_app, lang_stub  # noqa: F401

WAIT = """
const wait = async (f, what) => { for (let i = 0; i < 100; i++) { if (await f()) return; await sleep(100); } throw new Error("waited for " + what); };
"""


def go(stub, tmp_path, body, **kw):
    return drive(stub, tmp_path, WAIT + body, **kw)


LOOKS = ["console", "workbench", "palette", "panes"]
SECTIONS = ["overview", "printers", "spools", "analytics", "recent", "devices", "presets", "features", "settings", "flash", "updates", "logs"]


def test_general_lists_english_the_installed_languages_and_the_ones_to_get(lang_stub, tmp_path):
    page = go(lang_stub, tmp_path, r"""
unlock();
location.hash = "#settings/general";
await until(() => $(".settings__lang-row"), "the language list");
const rows = () => $$(".settings__lang-row").map((r) => [r.dataset.code, text($(".settings__name", r)), $$("button", r).map((b) => b.dataset.action)]);
out.rows = rows();
out.heads = $$(".settings__lang .settings__heading").map(text);
out.inUse = text($('.settings__lang-row[data-code="en"] .status'));
out.calls = (await calls()).map((c) => c.name);
""")
    assert page["heads"] == ["Language", "Get more languages"]
    rows = {r[0]: r for r in page["rows"]}
    assert rows["en"][1:] == ["English", []] and "In use" in page["inUse"]
    assert rows["de"][1] == "Deutsch (machine-translated)" and rows["de"][2] == ["language-use-de", "language-remove-de"]
    assert rows["ru"][1] == "Русский (machine-translated)" and rows["ru"][2] == ["language-get-ru"]
    assert "languages" in page["calls"]


def test_get_remove_and_use_send_the_routes_of_the_contract(lang_stub, tmp_path):
    page = go(lang_stub, tmp_path, r"""
unlock();
location.hash = "#settings/general";
await until(() => $('[data-action="language-use-de"]'), "the list");
$('[data-action="language-use-de"]').click();
await wait(async () => (await calls("settings/language")).length, "the Hub language sent");
out.use = (await calls("settings/language")).map((c) => c.body);
await until(() => (text($('.settings__lang-row[data-code="de"] .status')) || "").includes("In use"), "Deutsch in use");
$('[data-action="language-get-ru"]').click();
await sleep(600);
out.get = (await calls("languages/ru/install")).map((c) => c.body);
await stub({ hub: "en" });
$('[data-action="language-use-en"]').click();
await until(() => $('[data-action="language-remove-de"]'), "Remove on Deutsch");
$('[data-action="language-remove-de"]').click();
await until(() => $("dialog[open]"), "the confirmation");
out.ask = text($("dialog[open] .dialog__title"));
$('dialog[open] [data-action="confirm"]').click();
await wait(async () => (await calls("languages/de/remove")).length, "the removal sent");
out.removed = (await calls("languages/de/remove")).length;
""")
    assert page["use"][0]["hub"] == "de" and page["use"][0]["code"]
    assert len(page["get"]) == 1
    assert page["ask"] == "Remove Deutsch?" and page["removed"] == 1


def test_a_list_that_could_not_be_fetched_says_so_and_the_group_stays_usable(lang_stub, tmp_path):
    page = go(lang_stub, tmp_path, r"""
await stub({ available: [], problem: "could not reach the list of languages" });
location.hash = "#settings/general";
await until(() => $(".settings__lang .settings__heading"), "the language part");
out.problem = $$(".settings__lang p.muted").map(text);
out.save = !!$('#set-general button[type="submit"]');
""")
    assert "Could not reach the list of languages." in page["problem"] and page["save"]


def test_nothing_more_on_offer_is_said_in_words(lang_stub, tmp_path):
    page = go(lang_stub, tmp_path, r"""
await stub({ available: [] });
location.hash = "#settings/general";
await until(() => $(".settings__lang .settings__heading"), "the language part");
out.none = $$(".settings__lang p.muted").map(text);
""")
    assert "No more languages are on offer." in page["none"]


def test_appearance_language_is_automatic_kept_in_this_browser_and_sets_the_pages_lang(lang_stub, tmp_path):
    page = go(lang_stub, tmp_path, r"""
location.hash = "#settings/appearance";
await until(() => $('[name="appearance.language"]'), "the Language choice");
await sleep(500);
const pick = () => $('[name="appearance.language"]');
out.options = [...pick().options].map((o) => [o.value, o.textContent]);
out.initial = [pick().value, document.documentElement.lang, localStorage.getItem("spoolwarden.language")];
pick().value = "de"; pick().dispatchEvent(new Event("change", { bubbles: true }));
await sleep(200);
out.german = [document.documentElement.lang, localStorage.getItem("spoolwarden.language")];
pick().value = ""; pick().dispatchEvent(new Event("change", { bubbles: true }));
await sleep(200);
out.auto = [document.documentElement.lang, localStorage.getItem("spoolwarden.language")];
// a kept language the Hub does not have (removed since) is not used: the page falls back
localStorage.setItem("spoolwarden.language", "fr");
await setLangApply(true);
out.gone = document.documentElement.lang;
// Automatic follows the Hub's language when the browser's own is not installed
localStorage.removeItem("spoolwarden.language");
Object.defineProperty(navigator, "language", { get: () => "fr-FR", configurable: true });
await stub({ hub: "de" });
await setLangApply(true);
out.hubLang = document.documentElement.lang;
// the choice survives storage that refuses
const real = Storage.prototype.setItem;
Storage.prototype.setItem = () => { throw new Error("refused"); };
location.hash = "#devices"; await sleep(200); location.hash = "#settings/appearance";
await until(() => $('[name="appearance.language"]'), "the choice again");
pick().value = "en"; pick().dispatchEvent(new Event("change", { bubbles: true }));
Storage.prototype.setItem = real;
out.survived = !!pick();
""")
    assert page["options"] == [["", "Automatic"], ["en", "English"], ["de", "Deutsch (machine-translated)"]]
    assert page["initial"] == ["", "en", None]
    assert page["german"] == ["de", "de"] and page["auto"] == ["en", None]
    assert page["gone"] == "en" and page["hubLang"] == "de" and page["survived"]


def test_the_demo_banner_is_on_every_section_and_not_otherwise(lang_stub, tmp_path):
    page = go(lang_stub, tmp_path, r"""
out.before = $$(".demo-bar").length;
await stub({ demo: true });
location.hash = "#devices"; await sleep(300);
await refresh();
const secs = %s;
out.seen = [];
for (const s of secs) {
  location.hash = "#" + s;
  await sleep(400);
  const bar = $(".demo-bar");
  out.seen.push([s, $$(".demo-bar").length, bar && text($(".banner__text", bar)), bar && text($("button", bar)), bar && $("main").firstElementChild === bar]);
}
await stub({ demo: false });
await refresh();
out.after = $$(".demo-bar").length;
""" % SECTIONS)
    assert page["before"] == 0 and page["after"] == 0
    assert page["seen"] == [[s, 1, "Demo data. Nothing here is real.", "Leave demo", True] for s in SECTIONS]


def test_leave_demo_asks_once_and_deletes_only_after_it(lang_stub, tmp_path):
    page = go(lang_stub, tmp_path, r"""
unlock();
await stub({ demo: true });
await refresh();
await until(() => $(".demo-bar"), "the banner");
$('.demo-bar [data-action="leave-demo"]').click();
await until(() => $("dialog[open]"), "the question");
out.ask = [text($("dialog[open] .dialog__title")), text($("dialog[open] .dialog__text"))];
$('dialog[open] [data-action="cancel"]').click();
await sleep(400);
out.afterCancel = [$$(".demo-bar").length, (await calls("demo")).length];
$('.demo-bar [data-action="leave-demo"]').click();
await until(() => $("dialog[open]"), "the question again");
$('dialog[open] [data-action="confirm"]').click();
await until(() => !$(".demo-bar"), "the banner gone");
out.sent = (await calls("demo")).map((c) => c.body.on);
out.dialogs = $$("dialog[open]").length;
""")
    assert page["ask"] == ["Leave the demo?", "Everything in it is deleted."]
    assert page["afterCancel"] == [1, 0] and page["sent"] == [False] and page["dialogs"] == 0


def test_try_the_demo_starts_it_and_a_hub_with_data_refuses_in_words(lang_stub, tmp_path):
    page = go(lang_stub, tmp_path, r"""
unlock();
await stub({ not_empty: true });
location.hash = "#overview"; await sleep(300);
await refresh();
await until(() => $('[data-action="start-show"], [data-action="start-demo"]'), "Getting started");
if ($('[data-action="start-show"]')) $('[data-action="start-show"]').click();
await until(() => $('[data-action="start-demo"]'), "Try the demo in Getting started");
out.lead = [text($(".start__demo .muted")), text($(".start__demo button"))];
$('[data-action="start-demo"]').click();
await until(() => text($(".start__demo .result")), "the refusal");
out.refused = text($(".start__demo .result"));
out.barRefused = $$(".demo-bar").length;
await stub({ not_empty: false });
$('[data-action="start-demo"]').click();
await until(() => $(".demo-bar"), "the demo banner");
out.sent = (await calls("demo")).map((c) => c.body.on);
out.tryAfter = $$('[data-action="start-demo"]').length;
""")
    assert page["lead"] == ["Just looking?", "Try the demo"]
    assert page["refused"].startswith(NOT_EMPTY) and page["barRefused"] == 0
    assert page["sent"] == [True, True] and page["tryAfter"] == 0


@pytest.mark.parametrize("width", [1280, 390])
@pytest.mark.parametrize("look", LOOKS)
def test_banner_language_and_demo_row_fit_in_each_look(lang_stub, tmp_path, look, width):
    page = go(lang_stub, tmp_path, r"""
await stub({ demo: true });
await refresh();
await until(() => $(".demo-bar"), "the banner");
out.boxes = [];
const fits = (name, el) => { const r = el.getBoundingClientRect(); out.boxes.push([name, Math.round(r.left), Math.round(r.right), Math.round(r.height), innerWidth]); };
fits("banner", $(".demo-bar .banner"));
fits("leave", $('.demo-bar [data-action="leave-demo"]'));
location.hash = "#settings/general";
await until(() => $(".settings__lang-row"), "the list");
await sleep(300);
for (const r of $$(".settings__lang-row")) fits("row " + r.dataset.code, r);
location.hash = "#settings/appearance";
await until(() => shown($('[name="appearance.language"]')), "the choice");
fits("choice", $('[name="appearance.language"]'));
out.page = pageW();
""", width=width, look=look)
    assert page["page"]["sw"] <= page["page"]["cw"], page
    assert all(0 <= left and right <= vw + 1 and height > 0 for _, left, right, height, vw in page["boxes"]), page
