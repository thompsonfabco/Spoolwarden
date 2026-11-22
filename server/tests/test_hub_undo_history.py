"""HD7d: the Undo toasts, a spool's last ten events and the Download as CSV menus of the Hub page, in a real
headless browser against a stub Hub: the routes the server steps HD1/HD5/HD6 will add (`POST events/{id}/undo`, `GET activity`,
`GET export/{set}.csv`, and `event` in the answers of the changes) are not in the tree, so the page's fetch is answered by a stub in
the page and every request is recorded. The Spools section is answered completely by the stub; the printer's live view is the one
of test_hub_printer_live.py with its own stub; Settings > Storage locations, Slicer presets and Recently added are the real page
with only the file download answered. The same skip rules as the page audit: no Node 22 or no browser is a skip (a failure with
AUT5_REQUIRED=1)."""
from __future__ import annotations

import json

import pytest

from test_hub_page_audit import hub_app, _serve  # noqa: F401
from test_hub_presets_ui import AsHttps, _drive, T  # noqa: F401
from test_hub_printer_live import CSS as LIVE_CSS, JS as LIVE_JS, PRELUDE as LIVE_PRELUDE, SERIAL, hub as live_hub  # noqa: F401
from test_hub_spools import PRELUDE as SPOOLS_PRELUDE

LOOKS = ["console", "workbench", "palette", "panes"]
UNDO_REFUSED = "This changed again since, so it cannot be undone."
NOTE = "The printer still shows what it was set to."


@pytest.fixture
def hub(hub_app):  # noqa: F811
    """The real page with the Spools section added (its routes are the stub's, in the page)."""
    running = _serve(AsHttps(hub_app[0]), hub_app[1])
    try:
        yield running.url + "/setup"
    finally:
        running.stop()


# what the stub Hub of this file answers, in the page: the new routes, every request recorded in EV.calls with the setup code it carried
LIB = r"""
const EV = { calls: [], n: 0, noEvent: false, undo: null, note: null, downloads: [], handle: null, mine: /^$/ };
const evCalls = (re) => EV.calls.filter((c) => re.test(c.method + " " + c.path));
HTMLAnchorElement.prototype.click = function () { if (this.hasAttribute("download")) { EV.downloads.push(this.getAttribute("download")); return; } return HTMLElement.prototype.click.call(this); };
const jsonRes = (status, data) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
const stubCapabilities = { activity: true, event_undo: true, exports: ["spoolman-spools", "spoolman-filaments", "locations", "lines", "recent", "usage"] };
await until(() => S?.history, "the shell capabilities");
Object.assign(S.history, stubCapabilities);
const toasts = () => $$(".toast");
const toastText = (el) => text($(".toast__text", el));
const undoBtn = (el) => $$(".btn", el).find((b) => text(b) === "Undo");
const newEvent = () => (EV.noEvent ? {} : { event: { id: "ev-" + (++EV.n), undo: { possible: true } } });
{
  const inner = window.fetch.bind(window);
  window.fetch = async (url, init) => {
    const u = String(url);
    if (!u.startsWith("/setup/api/")) return inner(url, init);
    const path = u.slice(11), method = (init && init.method) || "GET";
    const rec = { method, path, body: init && init.body ? JSON.parse(init.body) : null, code: init && init.headers && init.headers["X-Setup-Code"] != null ? init.headers["X-Setup-Code"] : null };
    if (/^export\//.test(path)) {
      EV.calls.push(rec);
      return new Response("a,b\n1,2\n", { status: 200, headers: { "Content-Type": "text/csv; charset=utf-8", "Content-Disposition": `attachment; filename="${path.replace(/^export\/|\?.*$/g, "")}"` } });
    }
    if (/^events\/[^/]+\/undo$/.test(path)) {
      EV.calls.push(rec);
      const [s, d] = EV.undo ? EV.undo(rec) : [200, { undone: true, event: { id: "ev-u" }, ...(EV.note ? { note: EV.note } : {}) }];
      if (s === 200 && EV.onUndo) EV.onUndo(rec);
      return jsonRes(s, d);
    }
    if (EV.mine.test(path) && !["spools/low", "spools?limit=1"].includes(path)) {
      EV.calls.push(rec); const [s, d] = EV.handle(rec); return jsonRes(s, d);
    }
    const r = await inner(url, init);
    if (path === "state" && r.ok) {
      const state = await r.json();
      return jsonRes(200, { ...state, history: { ...state.history, ...stubCapabilities } });
    }
    if (/^printers\/[^/]+\/slots\/[^/]+\/(load|unload|clear)$/.test(path) && rec.body && !rec.body.dry_run && r.ok) {      // the answers of the slot routes gain `event`
      EV.calls.push(rec);
      return jsonRes(200, { ...(await r.json()), ...newEvent() });
    }
    return r;
  };
}
const insideView = (el) => { const r = el.getBoundingClientRect(), vw = document.documentElement.clientWidth; return r.width > 0 && r.left >= -1 && r.right <= vw + 1; };
const noSideways = () => document.documentElement.scrollWidth <= document.documentElement.clientWidth;
// a menu's Download as CSV: pressed while locked (the unlock dialog opens, nothing is asked), then unlocked (the file is asked with the code)
const csvFlow = async (trigger, action) => {
  SW.setLock("locked"); await sleep(200);
  const base = EV.calls.length, files = EV.downloads.length, o = {};
  trigger().click();
  await until(() => $(".menu__item"), "the menu");
  o.items = $$(".menu__item").map(text);
  o.inside = $$(".menu__item").every(insideView);
  $(`.menu__item[data-action="${action}"]`).click();
  const d = await until(() => $("dialog[open]"), "the unlock dialog");
  o.lockedAsks = !!$("input[type=password]", d);
  o.lockedCalls = EV.calls.length - base;
  $('[data-action="cancel"]', d).click();
  await until(() => !$("dialog[open]"), "the dialog to close");
  await unlock();
  trigger().click();
  await until(() => $(".menu__item"), "the menu again");
  $(`.menu__item[data-action="${action}"]`).click();
  await until(() => EV.downloads.length > files, "the file");
  o.calls = EV.calls.slice(base).map((c) => `${c.method} ${c.path} ${c.code}`);
  o.files = EV.downloads.slice(files);
  return o;
};
"""

# ---- Spools: the whole section is the stub's

SPOOLS_STUB = r"""
const S2 = { id: 2, vendor: "Sunlu", name: "PETG Blue", material: "PETG", color_hex: "1E50A0", net_g: 1000, tare_g: 200, remaining_g: 900, location: "Rack 2", tag: null, state: "opened", filament_id: 2, lot: null, loaded: null, low: false };
const S3 = { ...S2, id: 3, name: "PETG Red" };
const ONE = { id: 1, vendor: "Polymaker", name: "PolyTerra PLA", material: "PLA", color_hex: "2B2B2E", net_g: 1000, tare_g: 200, remaining_g: 500, location: "Rack 1", tag: null, state: "opened", filament_id: 1, lot: null, loaded: null, low: false };
const ST = { now: { ...ONE }, prev: null };
const EVENTS = Array.from({ length: 12 }, (_, i) => ({ id: "h" + i, at: `2026-11-1${(i % 9) + 1}T10:0${i % 10}:00`, kind: "spool.weighed", actor: i % 2 ? { type: "cli", id: "", name: "" } : { type: "device", id: "d1", name: "Weigh Station" },
  text: `Event number ${i}`, title: "Weighed", undone: i === 3, subject: { type: "spool", id: "1" } }));
EV.mine = /^(spools|settings\/storage|activity)/;
EV.handle = ({ method, path, body }) => {
  let x;
  if (/^spools\?/.test(path)) return [200, { spools: [ST.now, S2, S3], total: 3, offset: 0, has_more: false }];
  if (path === "spools/last-tag") return [200, { tag: { uid: "4:de:ad:be" } }];
  if (path === "settings/storage") return [200, { locations: [{ name: "Rack 1" }, { name: "Shelf B" }] }];
  if ((x = /^activity\?subject=spool%3A(\d+)&newest=10$/.exec(path))) return x[1] === "1" ? [200, { events: EVENTS, next: null, oldest: null }] : x[1] === "2" ? [200, { events: [], next: null, oldest: null }] : [500, { error: "no history today" }];
  if ((x = /^spools\/(\d+)(\/link)?$/.exec(path))) {
    if (method === "GET") return [200, { spool: x[1] === "1" ? ST.now : x[1] === "2" ? S2 : S3, last_use: null }];
    const before = { ...ST.now }, after = { ...ST.now };
    if (x[2]) after.tag = body.tag; else if ("gross" in body) after.remaining_g = body.gross - 200; else if (body.empty) { after.state = "empty"; after.remaining_g = 0; } else after.location = body.location || "Shelf B";
    if (body.dry_run) return [200, { dry_run: true, before, spool: after }];
    ST.prev = before; ST.now = after;
    return [200, { before, spool: after, ...newEvent() }];
  }
  return [404, { error: "no such stub " + path }];
};
EV.onUndo = () => { if (ST.prev) ST.now = ST.prev; };
const detail = (id) => $(`#spool-detail-${id}`);
const open = async (id) => { $(".coll__title", rowOf(id)).click(); await until(() => shown(detail(id)), "the detail of " + id); await sleep(300); };
const box = (id, n) => $$(".spools-act .result", detail(id))[n];
const press = (id, label) => { for (const r of $$(".spools-act .result", detail(id))) SW.result(r, null); $$(".spools-act .btn", detail(id)).find((b) => text(b) === label).click(); };
const field = (id, name) => detail(id).querySelector(`[name="${name}"]`);
const set = (c, v) => { c.value = v; c.dispatchEvent(new Event("input", { bubbles: true })); c.dispatchEvent(new Event("change", { bubbles: true })); };
const apply = async (id, n) => { await until(() => box(id, n).dataset.state === "info" && $(".btn", box(id, n)), "the preview"); $(".btn", box(id, n)).click(); };
const newest = async (count) => { await until(() => toasts().length === count, "toast number " + count); return toasts()[0]; };
const facts = (id) => Object.fromEntries($$(".kv__row", detail(id)).map((r) => [text($(".kv__key", r)), text($(".kv__value", r))]));
const left = (id) => facts(id)["Left"];
"""


def _spools(hub, tmp_path, body, width=1280, look="console"):
    script = (SPOOLS_PRELUDE + LIB + SPOOLS_STUB + f'const out = {{}};\nawait ready("{look}");\n' + body + "\nreturn out;\n")
    return _drive(hub, tmp_path, script, width=width)


def test_each_spool_change_shows_a_toast_with_undo_and_undo_puts_it_back_or_says_why_not_or_asks_for_the_code(hub, tmp_path):
    page = _spools(hub, tmp_path, r"""
await open(1);
await unlock();
// Update weight
set(field(1, "gross"), "540"); press(1, "Update weight"); await apply(1, 0);
let t1 = await newest(1);
out.weight = { text: toastText(t1), undo: !!undoBtn(t1), left: left(1), body: evCalls(/^POST spools\/1$/).map((c) => [c.body.dry_run, c.body.gross]) };
// Undo: "Put back." and the spool as it was
undoBtn(t1).click();
await until(() => evCalls(/events\//).length === 1, "the undo");
out.undoCall = evCalls(/events\//).map((c) => [c.method, c.path, c.body]);
const t2 = await newest(1);
out.put = { text: toastText(t2), undo: !!undoBtn(t2) };
await until(() => left(1) === "500 g / 1000 g", "the spool read again");
out.afterUndo = left(1);
// Move
set(field(1, "location"), "Shelf B"); press(1, "Move"); await apply(1, 1);
await until(() => toasts().some((t) => /^Moved /.test(toastText(t))), "the move toast");
let t3 = toasts().find((t) => /^Moved /.test(toastText(t)));
out.move = { text: toastText(t3), undo: !!undoBtn(t3) };
// Link the last tapped tag
press(1, "Link the last tapped tag"); await apply(1, 2);
await until(() => toasts().some((t) => /^Linked /.test(toastText(t))), "the link toast");
const t4 = toasts().find((t) => /^Linked /.test(toastText(t)));
out.link = { text: toastText(t4), undo: !!undoBtn(t4), tag: evCalls(/^POST spools\/1\/link$/).map((c) => [c.body.dry_run, c.body.tag]) };
// the answer names no event (an older Hub): the toast says it and has no Undo
EV.noEvent = true;
set(field(1, "gross"), "400"); press(1, "Update weight"); await apply(1, 0);
await until(() => toasts().some((t) => /^Updated /.test(toastText(t)) && !undoBtn(t)), "a toast without Undo");
out.noEvent = toastText(toasts().find((t) => /^Updated /.test(toastText(t)) && !undoBtn(t)));
EV.noEvent = false;
// the Hub refuses (409): its words are the toast
for (const t of toasts()) t.remove();
set(field(1, "gross"), "340"); press(1, "Update weight"); await apply(1, 0);
let t5 = await newest(1);
EV.undo = () => [409, { error: "This changed again since, so it cannot be undone.", code: "changed_since", message: "NOT THIS", text: "NOT THIS" }];
undoBtn(t5).click();
await until(() => toasts().some((t) => /cannot be undone/.test(toastText(t))), "the refusal");
out.refused = toastText(toasts()[0]);
out.refusedKind = toasts()[0].dataset.state;
EV.undo = null;
// the page locks again before Undo is pressed: the unlock dialog asks, nothing is sent until the code is given
for (const t of toasts()) t.remove();
set(field(1, "gross"), "300"); press(1, "Update weight"); await apply(1, 0);
t5 = await newest(1);
const before = evCalls(/events\//).length;
SW.setLock("locked"); await sleep(200);
undoBtn(t5).click();
const dlg = await until(() => $("dialog[open]"), "the unlock dialog");
out.locked = { asks: !!$("input[type=password]", dlg), calls: evCalls(/events\//).length - before };
$('[data-action="cancel"]', dlg).click();
await until(() => !$("dialog[open]"), "the dialog to close");
out.lockedAfter = evCalls(/events\//).length - before;
// the ten seconds: the toast offers Undo, the close button is beside it
await unlock();
set(field(1, "gross"), "260"); press(1, "Update weight"); await apply(1, 0);
t5 = await newest(1);
out.closeBtn = !!$$(".btn", t5).find((b) => b.getAttribute("aria-label") === "Close" || /close/i.test(b.getAttribute("aria-label") || ""));
out.live = $(".toasts").getAttribute("aria-live");
""")
    assert page["weight"] == {"text": "Updated Polymaker PolyTerra PLA (1): 340 g left.", "undo": True, "left": "340 g / 1000 g", "body": [[True, 540], [False, 540]]}
    assert [c[:2] for c in page["undoCall"]] == [["POST", "events/ev-1/undo"]] and list(page["undoCall"][0][2]) == ["code"] and page["undoCall"][0][2]["code"]
    assert page["put"] == {"text": "Put back.", "undo": False} and page["afterUndo"] == "500 g / 1000 g"
    assert page["move"] == {"text": "Moved Polymaker PolyTerra PLA (1) to Shelf B.", "undo": True}
    assert page["link"] == {"text": "Linked tag 4:de:ad:be to Polymaker PolyTerra PLA (1).", "undo": True, "tag": [[True, "4:de:ad:be"], [False, "4:de:ad:be"]]}
    assert page["noEvent"] == "Updated Polymaker PolyTerra PLA (1): 200 g left."
    assert page["refused"] == UNDO_REFUSED and page["refusedKind"] == "warn"
    assert page["locked"] == {"asks": True, "calls": 0} and page["lockedAfter"] == 0
    assert page["closeBtn"] is True and page["live"] == "polite"


def test_mark_empty_asks_first_and_then_offers_undo_and_the_note_of_an_undo_follows_put_back(hub, tmp_path):
    page = _spools(hub, tmp_path, r"""
await open(1);
await unlock();
press(1, "Mark empty");
await until(() => $$(".spools-act .result", detail(1)).some((r) => r.dataset.state === "info" && $(".btn", r)), "the preview");
$$(".spools-act .result", detail(1)).find((r) => r.dataset.state === "info").querySelector(".btn").click();
const d = await until(() => $("dialog[open]"), "the question");
$('[data-action="confirm"]', d).click();
await until(() => !$("dialog[open]"), "the dialog to close");
const t1 = await newest(1);
out.empty = { text: toastText(t1), undo: !!undoBtn(t1), body: evCalls(/^POST spools\/1$/).map((c) => [c.body.dry_run, c.body.empty]) };
EV.note = "The printer still shows what it was set to.";
undoBtn(t1).click();
await until(() => toasts().some((t) => /Put back/.test(toastText(t))), "the put back");
out.note = toastText(toasts()[0]);
""")
    assert page["empty"] == {"text": "Marked Polymaker PolyTerra PLA (1) empty.", "undo": True, "body": [[True, True], [False, True]]}
    assert page["note"] == "Put back. " + NOTE


def test_a_spools_detail_lists_its_last_ten_events_read_when_it_opens_and_again_after_a_change(hub, tmp_path):
    page = _spools(hub, tmp_path, r"""
await open(1);
await until(() => $$(".spools-events .kv__row", detail(1)).length === 10, "the events");
const rowsOf = () => $$(".spools-events .kv__row", detail(1)).map((r) => [text($(".kv__key", r)), text($(".kv__value", r))]);
out.title = $$(".detail__heading", detail(1)).map(text);
out.rows = rowsOf();
out.reads = evCalls(/^GET activity/).map((c) => c.path);
await open(2);
await until(() => text($(".spools-events", detail(2))), "the empty note");
out.none = text($(".spools-events", detail(2)));
await open(3);
await until(() => text($(".spools-events", detail(3))), "the error note");
out.error = text($(".spools-events", detail(3)));
out.readsAll = evCalls(/^GET activity/).map((c) => c.path);
// a change reads the list again
await unlock();
const n = evCalls(/^GET activity/).length;
set(field(1, "gross"), "540"); press(1, "Update weight"); await apply(1, 0);
await until(() => evCalls(/^GET activity/).length > n, "the events read again");
out.after = evCalls(/^GET activity/).slice(n).map((c) => c.path);
""")
    assert page["title"][:3] == ["State", "Latest events", "Change this spool"]
    assert len(page["rows"]) == 10 and page["rows"][0] == ["2026-11-13 10:01", "Event number 11 · The command line"] and page["rows"][1][1] == "Event number 10 · Weigh Station"
    assert page["rows"][8][1].endswith("(undone)") and page["rows"][9][1] == "Event number 2 · Weigh Station"      # the newest ten, newest first (D1, the design, Hub page)
    assert page["reads"] == ["activity?subject=spool%3A1&newest=10"]
    assert page["none"] == "Nothing has been recorded for this spool yet." and page["error"] == "Could not read the events."
    assert page["after"] == ["activity?subject=spool%3A1&newest=10"]


def test_the_spools_menu_downloads_spoolmans_two_files_after_the_code_and_asks_nothing_while_locked(hub, tmp_path):
    page = _spools(hub, tmp_path, r"""
out.more = $(".spools-more").getAttribute("aria-label");
$(".spools-more").click(); await until(() => $(".menu__heading"), "the menu");
out.heading = text($(".menu__heading"));
$(".spools-more").click(); await sleep(200);
out.spools = await csvFlow(() => $(".spools-more"), "download-spoolman-spools");
out.filaments = await csvFlow(() => $(".spools-more"), "download-spoolman-filaments");
const t = toasts();
out.toasts = t.length;
""")
    code = page["spools"]["calls"][0].rsplit(" ", 1)[1]
    assert page["more"] == "More actions for spools"
    assert page["spools"]["items"] == ["Spools (Spoolman's file)", "Filaments (Spoolman's file)"] and page["heading"] == "Download as CSV"
    assert page["spools"]["lockedAsks"] and page["spools"]["lockedCalls"] == 0 and page["filaments"]["lockedCalls"] == 0
    assert page["spools"]["calls"] == [f"GET export/spoolman-spools.csv {code}"] and page["spools"]["files"] == ["spoolman-spools.csv"]
    assert page["filaments"]["calls"] == [f"GET export/spoolman-filaments.csv {code}"] and page["filaments"]["files"] == ["spoolman-filaments.csv"]
    assert code and code != "null" and page["toasts"] == 0


def test_a_refused_download_says_so_in_a_toast(hub, tmp_path):
    page = _spools(hub, tmp_path, r"""
await unlock();
const inner = window.fetch;
window.fetch = async (u, i) => (String(u).includes("export/") ? jsonRes(403, { error: "the file is not for this page" }) : inner(u, i));
$(".spools-more").click();
await until(() => $(".menu__item"), "the menu");
$('.menu__item[data-action="download-spoolman-spools"]').click();
await until(() => toasts().length, "the toast");
out.text = toastText(toasts()[0]); out.kind = toasts()[0].dataset.state; out.files = EV.downloads.length;
""")
    assert page == {"text": "Could not download the file: The file is not for this page.", "kind": "bad", "files": 0}


# ---- the other four lists: the real page, only the file is answered

OTHERS = [("storage", "#settings/storage", ".storage__barmore", "locations", "export/locations.csv"),
          ("presets", "#presets", ".presets-headmore", "lines", "export/lines.csv"),
          ("recent", "#recent", ".recent-listmore", "recent", "export/recent.csv")]


@pytest.mark.parametrize("name,where,trigger,set_,path", OTHERS, ids=[o[0] for o in OTHERS])
def test_each_list_menu_downloads_its_csv_after_the_code_and_asks_nothing_while_locked(hub, tmp_path, name, where, trigger, set_, path):
    script = SPOOLS_PRELUDE + LIB + f"""
const out = {{}};
await until(() => window.SW && window.api && $("main") && !$(".chip--lock .skeleton__line"), "the page");
document.documentElement.setAttribute("data-look", "console");
location.hash = {json.dumps(where)};
await until(() => $({json.dumps(trigger)}), "the menu button");
await sleep(300);
out.label = $({json.dumps(trigger)}).getAttribute("aria-label");
out.flow = await csvFlow(() => $({json.dumps(trigger)}), "download-csv");
return out;
"""
    page = _drive(hub, tmp_path, script)
    code = page["flow"]["calls"][0].rsplit(" ", 1)[1]
    assert page["flow"]["items"] == ["Download as CSV"] and page["flow"]["inside"]
    assert page["flow"]["lockedAsks"] and page["flow"]["lockedCalls"] == 0
    assert page["flow"]["calls"] == [f"GET {path} {code}"] and page["flow"]["files"] == [f"{set_}.csv"] and code and code != "null"
    assert page["label"].startswith("More actions for ")


# ---- the four looks

@pytest.mark.parametrize("look", LOOKS)
@pytest.mark.parametrize("width", [1280, 390])
def test_the_toast_the_events_and_the_menus_hold_in_every_look_without_sideways_scrolling(hub, tmp_path, look, width):
    page = _spools(hub, tmp_path, r"""
const vw = document.documentElement.clientWidth, o = out;
o.vw = vw;
await unlock();
const sideways = [];
const check = (what) => { if (!noSideways()) sideways.push(what); };
check("list");
o.spoolsMore = insideView($(".spools-more"));
$(".spools-more").click(); await until(() => $(".menu__item"), "the menu");
o.spoolsMenu = $$(".menu__item").map((b) => insideView(b));
check("menu");
$(".spools-more").click(); await sleep(200);
await open(1);
await until(() => $$(".spools-events .kv__row", detail(1)).length === 10, "the events");
o.events = $$(".spools-events .kv__row", detail(1)).every((r) => r.getBoundingClientRect().right <= vw + 1);
check("detail");
set(field(1, "gross"), "540"); press(1, "Update weight"); await apply(1, 0);
const t = await newest(1);
await sleep(300);
o.toast = { shown: shown(t), inside: insideView(t), undo: !!undoBtn(t) && insideView(undoBtn(t)), close: $$(".btn", t).every(insideView), tall: Math.round(t.getBoundingClientRect().height) };
check("toast");
for (const [hash, trigger] of [["#settings/storage", ".storage__barmore"], ["#presets", ".presets-headmore"], ["#recent", ".recent-listmore"]]) {
  location.hash = hash;
  await until(() => $(trigger), "the menu button of " + hash);
  await sleep(300);
  o[hash] = { button: insideView($(trigger)) };
  $(trigger).click(); await until(() => $(".menu__item"), "the menu of " + hash);
  o[hash].menu = $$(".menu__item").every(insideView);
  check(hash);
  $(trigger).click(); await sleep(200);
}
o.sideways = sideways;
""", width=width, look=look)
    assert page["sideways"] == [], page["sideways"]
    assert page["spoolsMore"] and all(page["spoolsMenu"]) and page["events"]
    assert page["toast"]["shown"] and page["toast"]["inside"] and page["toast"]["undo"] and page["toast"]["close"], page["toast"]
    for k in ("#settings/storage", "#presets", "#recent"):
        assert page[k] == {"button": True, "menu": True}, (k, page[k])


# ---- the printer's live view: the slot actions and Filament used

def _live(body, width=1280, look="console"):
    css = json.dumps(LIVE_CSS.read_text(encoding="utf-8"))
    js = LIVE_JS.read_text(encoding="utf-8")
    return {"script": LIVE_PRELUDE.replace("__CSS__", css) + js + LIB + f'\nsetLook("{look}");\nconst out = {{}};\n' + body + "\nreturn out;\n", "width": width}


def _drive_live(hub, tmp_path, body, width=1280, look="console"):
    d = _live(body, width, look)
    return _drive(hub, tmp_path, d["script"], width=d["width"])


def test_the_slot_actions_show_a_toast_with_undo_and_a_load_that_waits_or_has_no_event_has_none(live_hub, tmp_path):
    page = _drive_live(live_hub, tmp_path, r"""
unlock();
mount();
await until(() => rows().length === 3, "the slots");
const newest = async (count) => { await until(() => toasts().length === count, "toast number " + count); return toasts()[0]; };
// Unload on A1
btn(row("A1"), "unload").click();
await answer("confirm");
let t1 = await newest(1);
out.unload = { text: toastText(t1), undo: !!undoBtn(t1) };
EV.note = "The printer still shows what it was set to.";
undoBtn(t1).click();
await until(() => toasts().some((t) => /^Put back/.test(toastText(t))), "the put back");
out.put = toastText(toasts()[0]);
out.undoCall = evCalls(/events\//).map((c) => [c.path, Object.keys(c.body)]);
for (const t of toasts()) t.remove();
// Clear slot on A1
btn(row("A1"), "clear").click();
await answer("confirm");
t1 = await newest(1);
out.clear = { text: toastText(t1), undo: !!undoBtn(t1) };
// the refusal
EV.undo = () => [409, { error: "This changed again since, so it cannot be undone.", code: "changed_since", message: "NOT THIS", text: "NOT THIS" }];
undoBtn(t1).click();
await until(() => toasts().some((t) => /cannot be undone/.test(toastText(t))), "the refusal");
out.refused = [toastText(toasts()[0]), toasts()[0].dataset.state];
EV.undo = null;
for (const t of toasts()) t.remove();
// Load into A3: a spool is picked, the preview is read, then Load
btn(row("A3"), "load").click();
let d = await dialog();
await until(() => $$(".plive__pick", d).length === 2, "the spools");
$$(".plive__pick", d)[0].click();
await next("confirm");
await answer("confirm");
t1 = await newest(1);
out.load = { text: toastText(t1), undo: !!undoBtn(t1) };
for (const t of toasts()) t.remove();
// a load that waits for the spool (A2): the toast says so, with Undo while the Hub names an event
btn(row("A2"), "load").click();
d = await dialog();
await until(() => $$(".plive__pick", d).length === 2, "the spools");
$$(".plive__pick", d)[1].click();
await next("confirm");
await answer("confirm");
t1 = await newest(1);
out.wait = { text: toastText(t1), undo: !!undoBtn(t1) };
for (const t of toasts()) t.remove();
// an older Hub: no event, no Undo
EV.noEvent = true;
btn(row("A1"), "clear").click();
await answer("confirm");
t1 = await newest(1);
out.noEvent = { text: toastText(t1), undo: !!undoBtn(t1) };
EV.noEvent = false;
for (const t of toasts()) t.remove();
// the page locks again before Undo: the unlock dialog asks, nothing is sent
btn(row("A1"), "clear").click();
await answer("confirm");
t1 = await newest(1);
const before = evCalls(/events\//).length;
SW.setLock("locked"); await sleep(200);
undoBtn(t1).click();
d = await dialog();
out.locked = { asks: !!$("input[type=password]", d), calls: evCalls(/events\//).length - before };
$('[data-action="cancel"]', d).click();
""")
    assert page["unload"] == {"text": T("printer_unload_line", name="Polymaker PolyTerra PLA", slot="A1"), "undo": True}
    assert page["put"] == "Put back. " + NOTE and page["undoCall"][0][0].startswith("events/ev-1/undo") and page["undoCall"][0][1] == ["code"]
    assert page["clear"] == {"text": "Slot A1 cleared.", "undo": True}
    assert page["refused"] == [UNDO_REFUSED, "warn"]
    assert page["load"] == {"text": "Loaded Polymaker PolyTerra PLA into A3.", "undo": True}
    assert page["wait"] == {"text": "Waiting for the spool to load in A2.", "undo": True}
    assert page["noEvent"] == {"text": "Slot A1 cleared.", "undo": False}
    assert page["locked"] == {"asks": True, "calls": 0}


def test_filament_used_has_a_menu_that_downloads_the_printers_usage_csv_after_the_code(live_hub, tmp_path):
    page = _drive_live(live_hub, tmp_path, r"""
mount();
await until(() => $$(".plive__used").length === 4, "the usage");
out.label = $(".plive__usagemore").getAttribute("aria-label");
out.flow = await csvFlow(() => $(".plive__usagemore"), "download-csv");
""")
    code = page["flow"]["calls"][0].rsplit(" ", 1)[1]
    assert page["label"] == "More actions for filament used" and page["flow"]["items"] == ["Download as CSV"] and page["flow"]["inside"]
    assert page["flow"]["lockedAsks"] and page["flow"]["lockedCalls"] == 0
    assert page["flow"]["calls"] == [f"GET export/usage.csv?printer={SERIAL} {code}"] and page["flow"]["files"] == ["usage.csv"] and code and code != "null"


@pytest.mark.parametrize("look", LOOKS)
@pytest.mark.parametrize("width", [1280, 390])
def test_the_live_view_toast_and_usage_menu_hold_in_every_look(live_hub, tmp_path, look, width):
    page = _drive_live(live_hub, tmp_path, r"""
unlock();
for (const b of $$(".toasts")) b.style.display = "";
const { host } = mount();
await until(() => rows().length === 3 && $$(".plive__used").length === 4, "the view");
const vw = document.documentElement.clientWidth, o = out;
btn(row("A1"), "clear").click();
await answer("confirm");
await until(() => toasts().length === 1, "the toast");
await sleep(300);
const t = toasts()[0];
o.toast = { inside: insideView(t), undo: !!undoBtn(t) && insideView(undoBtn(t)), close: $$(".btn", t).every(insideView), shown: shown(t) };
const more = $(".plive__usagemore");
o.more = insideView(more);
more.click(); await until(() => $(".menu__item"), "the menu");
o.menu = $$(".menu__item").every(insideView);
more.click(); await sleep(200);
o.sideways = !noSideways();
o.host = host.scrollWidth <= host.clientWidth + 1;
""", width=width, look=look)
    assert page["sideways"] is False and page["host"] is True, page
    assert page["toast"] == {"inside": True, "undo": True, "close": True, "shown": True} and page["more"] and page["menu"]
