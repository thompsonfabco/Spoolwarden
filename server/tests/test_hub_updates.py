"""The Updates section of the Hub page in a real headless browser: Check now and Update to <latest> are the
section header's buttons (as Overview and Slicer presets have theirs), and the Features pointer in the Checking row is an
inline text link. Served with the data of test_hub_page_audit.py. The same skip rules as the page audit."""
from __future__ import annotations

from filament_kit import updates

from test_hub_page_audit import UPDATES, hub_app  # noqa: F401
from test_hub_presets_ui import _drive, hub  # noqa: F401

SCRIPT = """const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(look, what) { for (let i = 0; i < 100; i++) { const v = look(); if (v) return v; await sleep(100); } throw new Error("waited for " + what); }
const $ = (s, root = document) => root.querySelector(s);
location.hash = "#updates";
await until(() => $(".updates__cards .kv"), "the Version list");
const label = (b) => [...b.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join("");
const link = $('.updates__cards .kv__value a[href="#features"]');
return { head: [...document.querySelectorAll(".page-head > .page-head__actions > .btn")].map((b) => [label(b), b.classList.contains("btn--primary")]),
  strays: document.querySelectorAll("main .page-head__actions").length, cards: !!$(".updates__cards .page-head__actions"),
  link: link ? { classes: link.className, inline: getComputedStyle(link).display, inText: link.parentElement.matches(".kv__value > span") && link.parentElement.textContent.includes("switched off") } : null };
"""


def test_check_now_and_update_are_the_section_header_buttons_and_features_is_an_inline_link(hub, monkeypatch, tmp_path):
    monkeypatch.setattr(updates, "view", lambda cfg: {**UPDATES, "module_on": False})
    page = _drive(hub, tmp_path, SCRIPT)
    assert page["head"] == [["Check now", False], ["Update to 0.5.3", True]]
    assert page["strays"] == 1 and not page["cards"]                         # one actions row, in the header
    link = page["link"]
    assert link and link["classes"] == "" and link["inline"] == "inline"      # a link, not a button, and in the text line
    assert link["inText"]                                                     # in the same run of text, not an item of its own


def test_label_reader_updates_card_shows_installed_and_newer_and_starts_update(hub, monkeypatch, tmp_path):
    from filament_kit import local_intake_install as lii
    monkeypatch.setattr(updates, "view", lambda cfg: {**UPDATES, "module_on": False})
    monkeypatch.setattr(lii, "installed", lambda: {"version": "0.1.0", "tier": "cpu", "port": 9091, "at": 1700000000, "previous": None})
    monkeypatch.setattr(lii, "saved_plan", lambda now=None: {
        "at": 1700000100,
        "fresh": True,
        "text": ["Update 0.1.0 -> 0.2.0"],
        "plan": {"version": "0.2.0", "installed": "0.1.0", "tier": "cpu", "upgrade": True, "downgrade": False, "conflict": None, "notes": "Faster"},
    })
    page = _drive(hub, tmp_path, """const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(look, what) { for (let i = 0; i < 100; i++) { const v = look(); if (v) return v; await sleep(100); } throw new Error("waited for " + what); }
const $ = (s, root = document) => root.querySelector(s);
const text = (el) => (el ? el.textContent.trim().replace(/\\s+/g, " ") : null);
const jobs = [], real = window.fetch;
window.fetch = (u, o) => {
  if (o && o.method === "POST" && String(u).endsWith("/setup/api/job")) {
    const body = JSON.parse(o.body);
    jobs.push(body);
    return Promise.resolve(Response.json({ started: true, job: body.name }));
  }
  return real(u, o);
};
SW.setLock("unlocked", { code: "__CODE__" });
location.hash = "#updates";
const card = await until(() => $(".updates__reader"), "the Label Reader card");
const rows = [...card.querySelectorAll(".kv__row")].map((r) => [text($(".kv__key", r)), text($(".kv__value", r))]);
const btns = [...card.querySelectorAll(".row .btn")].map((b) => text(b));
const updateBtn = [...card.querySelectorAll(".row .btn")].find((b) => text(b).includes("Update Label Reader"));
updateBtn.click();
const dlg = await until(() => $("dialog[open]"), "update confirm dialog");
$(".btn--primary", dlg).click();
await until(() => jobs.length > 0, "update job posted");
return { rows, btns, jobs };
""")
    assert ["Installed", "0.1.0"] in page["rows"], page
    assert ["Latest release", "0.2.0 is available."] in page["rows"], page
    assert page["btns"] == ["Check Label Reader", "Update Label Reader to 0.2.0"], page
    assert page["jobs"] == [{"name": "local_intake_install", "code": "__CODE__".replace("__CODE__", page["jobs"][0]["code"])}], page
