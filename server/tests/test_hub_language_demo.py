"""the Hub page's Language (Settings > General and Appearance, the page's `lang`), the demo banner
with Leave demo on every section, and Try the demo in Getting started. The Hub is the stub of hub_language_stub.py (the server steps and are not in this tree); the page is the real Hub page. The same skip rules as the page audit: no Node 22 or no browser is a skip
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


def test_general_language_is_a_select_sent_at_once_with_its_own_result(lang_stub, tmp_path):
    page = go(lang_stub, tmp_path, r"""
unlock();
location.hash = "#settings/general";
await until(() => $('[name="general.language"]') && $('[name="general.language"]').options.length > 1, "the Hub's languages");
const pick = () => $('[name="general.language"]');
const field = pick().closest(".settings__choice");
out.options = [...pick().options].map((o) => [o.value, o.textContent]);
out.initial = pick().value;
out.hint = text($(".field__hint", field));
out.label = text($("label", field));
out.more = [text($('[data-action="language-more"]')), $('[data-action="language-more"]').className.includes("btn--ghost")];
pick().value = "de"; pick().dispatchEvent(new Event("change", { bubbles: true }));
await wait(async () => (await calls("settings/language")).length, "the Hub language sent");
await until(() => (text($(".result__text", field)) || "").startsWith("Language:"), "the result");
out.sent = (await calls("settings/language")).map((c) => c.body.hub);
out.result = text($(".result__text", field));
out.save = (await calls("settings/general")).length;
out.inline = !!$("#set-general .settings__lang-row");
""")
    assert page["options"] == [["en", "English"], ["de", "Deutsch (machine-translated)"]] and page["initial"] == "en"
    assert page["hint"] == "The language of the Hub's own texts and of devices set to Automatic." and page["label"] == "Language"
    assert page["more"] == ["Get more languages", True]
    assert page["sent"] == ["de"] and page["result"] == "Language: Deutsch." and page["save"] == 0 and not page["inline"]


def test_get_more_languages_installs_and_removes_in_words_and_refuses_the_hubs_own(lang_stub, tmp_path):
    page = go(lang_stub, tmp_path, r"""
unlock();
location.hash = "#settings/general";
await until(() => $('[data-action="language-more"]'), "Get more languages");
const open = async () => { $('[data-action="language-more"]').click(); await until(() => $("dialog[open] .settings__lang-row"), "the rows"); };
const rows = () => Object.fromEntries($$("dialog[open] .settings__lang-row").map((r) => [r.dataset.code, [text($(".settings__name", r)), text($(".muted", r)), $$(".status", r).map(text), $$("button", r).map((b) => text(b))]]));
const result = () => text($("dialog[open] .result__text"));
const close = async () => { $("dialog[open] [data-action='confirm']").click(); await sleep(150); };
$('[data-action="language-more"]').click();
out.loading = $$("dialog[open] .skeleton__item").length;
await until(() => $("dialog[open] .settings__lang-row"), "the rows");
out.title = text($("dialog[open] .dialog__title"));
out.rows = rows();
$('dialog[open] [data-action="language-install-ru"]').click();
await until(() => (result() || "").includes("installed"), "the install result");
out.installed = [result(), (await calls("languages/ru/install")).length, rows().ru];
await close();
await stub({ hub: "de" });
await open();
$('dialog[open] [data-action="language-remove-de"]').click();
await until(() => result(), "the refusal");
out.refused = [result(), (await calls("languages/de/remove")).length];
await close();
await stub({ hub: "en" });
await open();
$('dialog[open] [data-action="language-remove-de"]').click();
await until(() => (result() || "").includes("removed"), "the removal");
out.removed = [result(), (await calls("languages/de/remove")).length, rows().de];
await close();
await stub({ install_error: "The pack has no strings. Check the file." });
await stub({ languages: [{ code: "en", name: "English", reviewed: true, installed: true, complete: 100 }] });
await open();
$('dialog[open] [data-action="language-install-ru"]').click();
await until(() => (result() || "").includes("not installed"), "the failed install");
out.failed = [result(), $$("dialog[open] .status").length];
""")
    assert page["loading"] == 3 and page["title"] == "Get more languages"
    assert page["rows"]["de"] == ["Deutsch (machine-translated)", "62 % translated", ["Installed"], ["Remove"]]
    assert page["rows"]["ru"] == ["Русский (machine-translated)", "8 % translated", [], ["Install"]] and "en" not in page["rows"]
    assert page["installed"][0] == "Русский installed." and page["installed"][1] == 1 and page["installed"][2][2] == ["Installed"]
    assert page["refused"] == ["Deutsch is the Hub's language. Choose another one for the Hub first.", 0]
    assert page["removed"][0] == "Deutsch removed." and page["removed"][1] == 1 and page["removed"][2][3] == ["Install"]
    assert page["failed"] == ["Русский was not installed: The pack has no strings.", 0]


def test_the_dialog_says_no_list_nothing_to_get_and_not_in_the_demo(lang_stub, tmp_path):
    page = go(lang_stub, tmp_path, r"""
location.hash = "#settings/general";
await until(() => $('[data-action="language-more"]'), "Get more languages");
const close = async () => { $("dialog[open] [data-action='confirm']").click(); await sleep(150); };
await stub({ available: [], problem: "could not reach the list of languages" });
$('[data-action="language-more"]').click();
await until(() => $('dialog[open] [data-action="language-again"]'), "Try again");
out.problem = [text($("dialog[open] .settings__lang p")), text($('dialog[open] [data-action="language-again"]'))];
await stub({ problem: null });
$('dialog[open] [data-action="language-again"]').click();
await until(() => text($("dialog[open] .settings__lang p")) === "This release has no other languages.", "nothing to get");
out.none = text($("dialog[open] .settings__lang p"));
await close();
await stub({ demo: true });
await refresh();
$('[data-action="language-more"]').click();
await until(() => $("dialog[open] .settings__lang p"), "the demo line");
out.demo = [text($("dialog[open] .settings__lang p")), $$("dialog[open] .settings__lang-row").length];
""")
    assert page["problem"] == ["Could not read the list of languages. Could not reach the list of languages.", "Try again"]
    assert page["none"] == "This release has no other languages."
    assert page["demo"] == ["Not in the demo.", 0]


def test_the_demo_row_tries_leaves_and_reads_the_refusal_by_its_code(lang_stub, tmp_path):
    page = go(lang_stub, tmp_path, r"""
unlock();
await stub({ not_empty: true });
location.hash = "#settings/general";
await until(() => $('[data-action="settings-try-demo"]'), "Try the demo");
const row = () => ($('[data-action="settings-try-demo"], [data-action="settings-leave-demo"]') || {}).closest && $('[data-action="settings-try-demo"], [data-action="settings-leave-demo"]').closest(".settings__choice");
out.off = [text($(".field__hint", row())), text($("button", row())), $("button", row()).className.includes("btn--primary")];
$('[data-action="settings-try-demo"]').click();
await until(() => (text($(".result__text", row())) || "").includes("empty Hub"), "the refusal");
out.refused = [text($(".result__text", row())), $$(".demo-bar").length];
await stub({ not_empty: false });
$('[data-action="settings-try-demo"]').click();
await until(() => $('[data-action="settings-leave-demo"]'), "Leave demo");
await until(() => $(".demo-bar") && text($(".result__text", row())) === "The demo is on.", "the banner and its result");
out.on = [text($(".field__hint", row())), text($("button", row())), text($(".result__text", row()))];
$('[data-action="settings-leave-demo"]').click();
await until(() => $("dialog[open]"), "the question");
$('dialog[open] [data-action="confirm"]').click();
await until(() => $('[data-action="settings-try-demo"]') && !$(".demo-bar") && text($(".result__text", row())) === "The demo is off.", "the demo gone and its result");
out.after = [text($("button", row())), text($(".result__text", row()))];
out.sent = (await calls("demo")).map((c) => c.body.on);
out.save = (await calls("settings/general")).length;
""")
    assert page["off"] == ["Look around the whole product on made-up data. Only on a Hub with no spools and no printers.", "Try the demo", True]
    assert page["refused"] == ["The demo is for an empty Hub. This Hub has real data.", 0]
    assert page["on"] == ["The demo is on.", "Leave demo", "The demo is on."]
    assert page["after"] == ["Try the demo", "The demo is off."] and page["sent"] == [True, True, False] and page["save"] == 0


def test_plural_forms_follow_the_pages_language(lang_stub, tmp_path):
    page = go(lang_stub, tmp_path, r"""
const saved = window.SW_TEXT;
window.SW_TEXT = { ...saved, files: { one: "{count} файл", few: "{count} файла", many: "{count} файлов", other: "{count} файла" },
  cups: { one: "{count} cup", other: "{count} cups" } };
document.documentElement.lang = "ru";
out.ru = [1, 2, 5, 21].map((n) => SW.t("files", { count: n }));
document.documentElement.lang = "en";
out.en = [1, 2].map((n) => SW.t("cups", { count: n }));
document.documentElement.lang = "not a language!";
out.bad = [1, 2].map((n) => SW.t("cups", { count: n }));
document.documentElement.lang = "en";
window.SW_TEXT = saved;
""")
    assert page["ru"] == ["1 файл", "2 файла", "5 файлов", "21 файл"]
    assert page["en"] == ["1 cup", "2 cups"] and page["bad"] == ["1 cup", "2 cups"]


def test_appearance_language_is_automatic_kept_in_this_browser_and_sets_the_pages_lang(lang_stub, tmp_path):
    page = go(lang_stub, tmp_path, r"""
location.hash = "#settings/appearance";
await until(() => $('[name="appearance.language"]'), "the Language choice");
await sleep(500);
const pick = () => $('[name="appearance.language"]');
out.options = [...pick().options].map((o) => [o.value, o.textContent]);
out.hint = text($(".field__hint", pick().closest(".field")));
out.initial = [pick().value, document.documentElement.lang, localStorage.getItem("spoolwarden.language")];
pick().value = "de"; pick().dispatchEvent(new Event("change", { bubbles: true }));
await until(() => text($("#announcer")) === "Language: Deutsch.", "the announcement");
out.said = [text($("#announcer")), text($(".result__text", pick().closest(".settings__choice"))) || ""];
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
    assert page["hint"] == "Automatic uses this browser's language if the Hub has it, otherwise the Hub's language."
    assert page["said"] == ["Language: Deutsch.", ""]          # said to a screen reader; no visible Saved
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


def test_the_banner_stays_on_a_locked_page_and_when_the_state_cannot_be_read(lang_stub, tmp_path):
    page = go(lang_stub, tmp_path, r"""
unlock();
await stub({ demo: true });
await refresh();
await until(() => $(".demo-bar"), "the banner");
location.hash = "#settings/general"; await sleep(300);
SW.setLock("locked");
await sleep(300);
out.locked = [$$(".demo-bar").length, $("main").firstElementChild === $(".demo-bar"), text($(".demo-bar .banner__text"))];
await stub({ fail: { state: 500 } });
await refresh();
await sleep(300);
out.failed = [$$(".demo-bar").length, $("main").firstElementChild === $(".demo-bar"), !!$(".error-state")];
await stub({ fail: {} });
await refresh();
await sleep(300);
out.back = $$(".demo-bar").length;
""")
    assert page["locked"] == [1, True, "Demo data. Nothing here is real."]
    assert page["failed"][:2] == [1, True] and page["back"] == 1


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
await stub({ demo: false });
await refresh();
location.hash = "#settings/general";
await until(() => $('[name="general.language"]') && $('[name="general.language"]').options.length > 1, "the Hub's languages");
await sleep(300);
fits("hub language", $('[name="general.language"]'));
fits("more", $('[data-action="language-more"]'));
fits("try demo", $('[data-action="settings-try-demo"]'));
$('[data-action="language-more"]').click();
await until(() => $("dialog[open] .settings__lang-row"), "the list");
await sleep(300);
for (const r of $$("dialog[open] .settings__lang-row")) { fits("row " + r.dataset.code, r); for (const b of $$("button", r)) fits("button " + r.dataset.code, b); }
out.sheet = !!$("dialog.sheet[open]");
$("dialog[open] [data-action='confirm'], dialog[open] [data-action='close']").click();
await sleep(200);
location.hash = "#settings/appearance";
await until(() => shown($('[name="appearance.language"]')), "the choice");
fits("choice", $('[name="appearance.language"]'));
out.page = pageW();
""", width=width, look=look)
    assert page["sheet"] == (width == 390), page
    assert page["page"]["sw"] <= page["page"]["cw"], page
    assert all(0 <= left and right <= vw + 1 and height > 0 for _, left, right, height, vw in page["boxes"]), page
