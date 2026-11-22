"""R88 in the web app, run in Node without a browser: CO5 (the Checked out chip and list, the Home line, Return with its result and
Undo; R88 1.6, 01:55, 02:00 point 5, 02:05; 04 5.4, 5.5) and VO4 (Settings > Weigh Station voice: Speak, Voice read only, Speaker, Speed,
Volume, Test; R88 01:35 point 5, 01:50, 02:00 point 2; 04 9.2). The page's own functions (with their stub texts, `tt`) are cut out of
app.js and given a small DOM, `api` and `SW`."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "filament_kit" / "web" / "static"

STUBS = r"""
class El {
  constructor(tag, props, kids) {
    this.tag = tag; this.attrs = {}; this.ls = {}; this.kids = []; this.cls = new Set(); this.hidden = false; this.value = ""; this.textContent = "";
    this.dataset = {}; this.isConnected = true;
    const self = this;
    this.classList = { toggle: (c, on) => { if (on === undefined ? !self.cls.has(c) : on) self.cls.add(c); else self.cls.delete(c); },
      contains: (c) => self.cls.has(c), add: (c) => self.cls.add(c) };
    for (const [k, v] of Object.entries(props || {})) {
      if (v === undefined || v === null && k === "on") continue;
      if (k === "on") for (const [ev, fn] of Object.entries(v)) this.addEventListener(ev, fn);
      else if (k === "class") String(v).split(/\s+/).filter(Boolean).forEach((c) => this.cls.add(c));
      else if (["textContent", "hidden", "value", "disabled", "checked", "id"].includes(k)) this[k] = v;
      else this.attrs[k] = v;
    }
    this.append(...kids);
  }
  append(...k) { this.kids.push(...k.flat(Infinity).filter((x) => x != null && x !== false)); }
  replaceChildren(...k) { this.kids = []; this.append(...k); }
  setAttribute(k, v) { this.attrs[k] = v; }
  getAttribute(k) { return this.attrs[k] ?? null; }
  addEventListener(ev, fn) { (this.ls[ev] = this.ls[ev] || []).push(fn); }
  async fire(ev) {
    const e = { currentTarget: this, target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } };
    for (const fn of this.ls[ev] || []) await fn(e);
    return e;
  }
  all() { return [this, ...this.kids.filter((k) => k instanceof El).flatMap((k) => k.all())]; }
  querySelector(sel) { const m = /^\[data-voice=(\w+)\]$/.exec(sel); return m ? this.all().find((e) => e.attrs["data-voice"] === m[1]) || null : null; }
}
class Node {}
const h = (tag, props, ...kids) => new El(tag, props, kids);
const words = (e) => (e == null ? "" : typeof e === "string" ? e : e instanceof El && !e.hidden
  ? [e.textContent, ...e.kids.map(words)].filter(Boolean).join(" ") : "");
const shown = (e) => e.all().filter((x) => !x.hidden);
class HubError extends Error {
  constructor(o) { super(o.message || o.error || ""); Object.assign(this, o); }
  text(fallback) { return this.unreachable ? "common_hub_no_answer" : this.message || this.error || fallback; }
}
const calls = [], toasts = [], results = [];
let answers = {};
const SW = { HubError, t: (k) => k, icon: () => null, swatch: () => null, uid: (p) => p + Math.random().toString(36).slice(2, 7),
  busy: () => true, idle: () => {},
  result: (line, kind, text, o) => { results.push([kind, kind ? String(text) : null]); line.state = kind; line.said = kind ? String(text) : null; },
  toast: (text, kind, o) => { toasts.push([String(text), kind || "ok", o && o.action ? o.action.label : null]); },
  btn: (label, o = {}) => h("button", { class: "btn " + (o.cls || ""), "data-action": o.action, on: o.run ? { click: o.run } : null }, label),
  field: (o) => { const c = h("input", { value: o.value, disabled: !!o.disabled, ...(o.attrs || {}) }); const f = h("div", {}, o.label, c, o.hint || null); f._control = c; return f; },
  fieldError: (f, text) => { f.error = text; },
  toggle: (o) => { const t = h("div", { "data-toggle": "1" }, o.label); t._o = o; t.disabled = !!o.disabled; t._control = h("input", { type: "checkbox" }); return t; } };
const api = async (path, opts = {}) => {
  calls.push([path, opts.method || (opts.body === undefined ? "GET" : "POST"), opts.body]);
  const a = answers[path];
  if (a instanceof Error) throw a;
  if (a === undefined) throw new HubError({ status: 404, error: "not_found", data: { error: "not_found" } });
  return typeof a === "function" ? a(path, opts) : a;
};
const location = { reload() {}, hash: "" };
const needsHub = (b) => { b.dataset.needsHub = "1"; return b; }, resultLine = () => h("p", { class: "result" });
const tick = () => new Promise((r) => setTimeout(r, 0));
"""

CO_HARNESS = STUBS + r"""
%(tt)s
%(says)s
%(undo)s
%(co)s
(async () => {
  const out = {};
  out.gate = [knowsCheckedOut({ spools: [] }), knowsCheckedOut({ filters: ["sealed", "low"], spools: [] }),
    knowsCheckedOut({ filters: ["sealed", "opened", "loaded", "low", "checked_out"], spools: [] }), knowsCheckedOut(null)];
  out.homes = [homeLine({ home: "Shelf A" }), homeLine({ home: null }), returnLabel({ home: "Shelf A" }), returnLabel({})];
  out.results = [returnedText({ spool: { location: "Shelf A" }, event_id: 4 }), returnedText({ spool: { view: { location: "Dry box 1" } } }),
    returnedText({ spool: { location: null } }), returnedText({})];
  out.since = [sinceText("2026-11-22T01:05:00"), sinceText(null), sinceText(""), sinceText("not a time")];
  const box = h("div", {});
  let toggled = [], opened = [], known = 0;
  const co = checkedOutFilter({ box, open: (id) => opened.push(id), toggle: (on) => { toggled.push(on); co.st.on = on; if (on) co.draw(); }, known: () => known++ });
  // an older Hub: a list without `filters`, then no list at all
  answers = { "spools?filter=checked_out": { spools: [{ id: 1, view: { id: 1, checked_out: true, home: "Shelf A" } }] } };
  await co.load(); out.older = [co.chip.cls.has("hidden"), co.st.known];
  answers = {};
  await co.load(); out.missing = [co.chip.cls.has("hidden"), co.st.known];
  // a Hub that knows it, with nothing checked out: the chip shows 0, the list says so
  answers = { "spools?filter=checked_out": { filters: ["sealed", "opened", "loaded", "low", "checked_out"], spools: [] } };
  await co.load(); out.zero = [co.chip.cls.has("hidden"), co.chip.textContent];
  await co.chip.fire("click"); out.empty = words(box); out.toggled = toggled.slice();
  // two spools checked out: one with a home, one without
  const rows = [
    { id: 7, view: { id: 7, vendor: "Polymaker", name: "PolyTerra PLA", color_hex: "112233", location: "AMS 1 slot 2",
      loaded: { printer_id: "x1", printer: "Workshop X1", slot: "AMS 1 slot 2" }, checked_out: true, home: "Shelf A", checked_out_since: "2026-11-21T18:30:00" } },
    { id: 9, view: { id: 9, vendor: "Bambu Lab", name: "PETG HF", location: "T0", loaded: null, checked_out: true, home: null, checked_out_since: null } }];
  answers["spools?filter=checked_out"] = { filters: ["checked_out"], spools: rows, total: 5, offset: 0, has_more: true };
  await co.load();
  out.two = [co.chip.textContent, words(box)];
  answers["spools?filter=checked_out"] = { filters: ["checked_out"], spools: rows };       // an answer without paging: the rows
  await co.load();
  out.noTotal = co.chip.textContent;
  answers["spools?filter=checked_out"] = { filters: ["checked_out"], spools: rows, total: 2 };
  const backs = () => box.all().filter((b) => b.attrs["data-action"] === "return-spool");
  out.labels = backs().map((b) => words(b));
  // Return on the first row: sent at once, the result with Undo (the answer's event), the list read again
  answers["spools/7/return"] = { spool: { id: 7, location: "Shelf A" }, event_id: 41 };
  calls.length = 0; toasts.length = 0;
  await backs()[0].fire("click"); await tick();
  out.returned = [calls.map((c) => c[0] + " " + c[1]), toasts.slice()];
  // a refusal shows under the row, in the Hub's words
  answers["spools/9/return"] = new HubError({ status: 409, error: "not_checked_out", message: "This spool is not checked out.", data: { error: "not_checked_out" } });
  results.length = 0; toasts.length = 0;
  await backs()[1].fire("click"); await tick();
  out.refused = [results.filter((r) => r[0] === "bad"), toasts.slice()];
  // no place in the answer and no event the Hub can undo: the plain "Returned." without Undo
  answers["spools/9/return"] = { spool: { id: 9, location: null } };
  answers["activity?subject=spool:9&limit=1"] = { events: [] };
  toasts.length = 0;
  await backs()[1].fire("click"); await tick();
  out.plain = toasts.slice();
  // the whole row opens the spool sheet
  await box.all().find((b) => b.cls.has("spools__row")).fire("click");
  out.opened = opened;
  out.known = known > 0;
  console.log(JSON.stringify(out));
})().catch((e) => { console.error("harness: " + (e && e.stack || e)); process.exit(3); });
"""

VO_HARNESS = STUBS + r"""
let HUB_PAGE = "https://hub.local/";
let hubGone = false;                                          // the shell's "Cannot reach the Hub" state
const hubBack = [], hubGroup = (g, at) => { g.hubGroup = typeof at === "function"; return g; };
const note = (kind, text) => h("p", { "data-state": kind }, text);
%(tt)s
%(says)s
%(vo)s
const group = (title, ...kids) => h("section", {}, h("h2", { textContent: title }), ...kids);
// the one answer shape of R88 04:15: {station, remote, speech, voice, speaker, speaker_range, speed, volume}
const VOICE = { station: true, remote: false, speech: true, voice: "Piper, many speakers", speaker: 0, speaker_range: [0, 903], speed: 100, volume: 50 };
async function panel(get) {
  answers = { voice: get };
  const sec = voiceGroup(group); await tick(); await tick();
  return sec;
}
const input = (sec, k) => sec.querySelector(`[data-voice=${k}]`);
(async () => {
  const out = {};
  out.absent = [(await panel(undefined)).hidden, (await panel({ ...VOICE, station: false })).hidden];
  let sec = await panel({ ...VOICE });
  out.home = { hidden: sec.hidden, words: words(sec), speed: input(sec, "speed").attrs, volume: input(sec, "volume").attrs,
    test: sec.all().some((b) => b.attrs["data-action"] === "voice-test"), disabled: sec.all().some((x) => x.disabled) };
  out.oneSpeaker = !input(await panel({ ...VOICE, speaker_range: null }), "speaker");
  // Speak applies at once
  sec = await panel({ ...VOICE });
  answers["voice"] = (p, o) => (o.method === "PATCH" ? { ...VOICE, ...o.body } : VOICE);
  calls.length = 0;
  const speak = sec.all().find((x) => x.attrs["data-toggle"]);
  out.speak = [await speak._o.apply(false), calls.slice()];
  // a slider sends its value when it is let go; a refusal shows at the row and the slider goes back
  calls.length = 0; input(sec, "speed").value = "150"; await input(sec, "speed").fire("change");
  out.speed = [calls.slice(), input(sec, "speed").value];
  answers["voice"] = new HubError({ status: 400, error: "bad_value", message: "Speed must be 50 to 200 in steps of 10.", field: "speed", data: { error: "bad_value", field: "speed" } });
  results.length = 0; input(sec, "volume").value = "80"; await input(sec, "volume").fire("change");
  out.volumeRefused = [input(sec, "volume").value, results.filter((r) => r[0] === "bad")];
  // Speaker: a number out of the voice's range is not sent and the field goes back
  calls.length = 0; const sp = input(sec, "speaker"); sp.value = "904"; await sp.fire("change");
  const spField = sec.all().find((x) => x._control === sp);
  out.speaker = [calls.length, sp.value, spField.error];
  // Test plays with the values on the screen, saved or not; its four results
  sp.value = "12"; input(sec, "speed").value = "120";
  const testBtn = sec.all().find((b) => b.attrs["data-action"] === "voice-test");
  out.tests = [];
  for (const a of [{}, new HubError({ status: 409, error: "busy", message: "x", data: { error: "busy" } }),
    new HubError({ status: 409, error: "off", message: "x", data: { error: "off" } }),
    new HubError({ status: 502, error: "no_answer", message: "x", data: { error: "no_answer" } }),
    new HubError({ status: 403, error: "forbidden", message: "Not allowed from here.", data: { error: "forbidden" } })]) {
    answers["voice/test"] = a; calls.length = 0; toasts.length = 0;
    await testBtn.fire("click");
    out.tests.push([toasts[0], calls[0]]);
  }
  // the public address: read only, no Test, and the line that says where to change it
  HUB_PAGE = "";
  sec = await panel({ ...VOICE });
  out.away = { words: words(sec), test: sec.all().some((b) => b.attrs["data-action"] === "voice-test"),
    locked: ["speed", "volume", "speaker"].map((k) => input(sec, k).disabled), speak: sec.all().find((x) => x.attrs["data-toggle"]).disabled };
  // R88 04:20: the Voice row's line follows `remote`
  HUB_PAGE = "https://hub.local/";
  out.remote = [words(await panel({ ...VOICE, remote: true })), words(await panel({ ...VOICE }))];
  // R622 2: the Hub away with values read before: the panel at once from them, every control marked, nothing sent
  sec = await panel({ ...VOICE });
  hubGone = true; answers = {}; calls.length = 0;
  sec = voiceGroup(group);                                   // no tick: at once
  const marked = (x) => !!(x && x.dataset && x.dataset.needsHub);
  const sw = sec.all().find((x) => x.attrs["data-toggle"]);
  out.awayLast = { hidden: sec.hidden, asked: calls.length, group: sec.all().some((x) => x.hubGroup === true),
    marks: [marked(sw._control), ...["speaker", "speed", "volume"].map((k) => marked(input(sec, k))), marked(sec.all().find((b) => b.attrs["data-action"] === "voice-test"))] };
  input(sec, "speed").value = "180"; await input(sec, "speed").fire("change");
  const sp2 = input(sec, "speaker"); sp2.value = "5"; await sp2.fire("change");
  const click = await sw._control.fire("click");
  out.awaySent = [calls.length, input(sec, "speed").value, sp2.value, click.defaultPrevented];
  // never read (an older answer was a 404) and the Hub away: the panel with the line, read again when the Hub answers
  hubGone = false; await panel(undefined);
  hubGone = true; calls.length = 0;
  sec = voiceGroup(group);
  out.awayNone = { hidden: sec.hidden, words: words(sec), asked: calls.length };
  hubGone = false; answers = { voice: { ...VOICE } };
  for (const f of hubBack) f();
  await tick(); await tick();
  out.back = { hidden: sec.hidden, speed: !!input(sec, "speed"), hooks: hubBack.length };
  // the twin answers 502 unreachable (the Hub away behind the web unit): the values last read, with the line
  sec = await panel(new HubError({ status: 502, error: "unreachable", message: "The Hub did not answer.", data: { error: "unreachable" } }));
  out.twin502 = { hidden: sec.hidden, words: words(sec) };
  console.log(JSON.stringify(out));
})().catch((e) => { console.error("harness: " + (e && e.stack || e)); process.exit(3); });
"""


HOME_HARNESS = STUBS + r"""
%(tt)s
%(says)s
const id = 7, homeAns = h("p", {}), homeBtn = h("button", {}), busied = [];
let view = { id: 7, checked_out: true, home: "Shelf A" }, changed = false;
const applyView = (v) => { view = v; }, paintCo = () => {};
SW.busy = (b) => { busied.push(b); return true; };
%(home)s
(async () => {
  const out = {};
  const as = async (answer, chosen) => { view = { id: 7, checked_out: true, home: "Shelf A" }; answers = { "spools/7": answer }; await setHome(chosen); return [homeAns.said, view.home ?? null]; };
  out.took = await as({ view: { id: 7, checked_out: true, home: "Dry box 2" } }, "Dry box 2");   // the twin took it: the answer's home
  out.ignored = await as({ view: { id: 7, checked_out: true } }, "Dry box 2");                     // the answer carries no home
  out.noView = await as({}, "Dry box 2");
  out.refused = await as(new HubError({ status: 400, error: "bad_value", message: "There is no such place.", data: { error: "bad_value" } }), "Nowhere");
  out.busy = busied.includes(homeBtn);
  console.log(JSON.stringify(out));
})().catch((e) => { console.error("harness: " + (e && e.stack || e)); process.exit(3); });
"""

FIND_HARNESS = STUBS + r"""
%(tt)s
%(says)s
const draws = [], busyStates = [];
const co = { st: { on: false }, paint() {}, draw() { draws.push("co"); }, load() {} };
let low = false, lastQ = "", findStamp = 0, asked = false, firstLoad = null, rows = [], cut = false, page = 1, foundAt = 0, sealedOnly = true;
const paintLow = () => {}, loadLow = () => {}, findLine = h("p", {}), resultsBox = h("div", {});
const searchField = { _busy: (v) => busyStates.push(v) }, drawResults = () => draws.push("search");
SW.loading = () => ({ done() {} });
%(find)s
(async () => {
  let answer;
  answers = { "find?q=pla&sealed=1": () => new Promise((r) => { answer = r; }) };
  const p = find("pla");
  await tick();
  setCo(true);                                               // the chip pressed while the search is on its way
  answer({ filaments: [] });
  await p; await tick();
  console.log(JSON.stringify({ draws, busy: busyStates[busyStates.length - 1] }));
})().catch((e) => { console.error("harness: " + (e && e.stack || e)); process.exit(3); });
"""


def _cut(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i:text.index(end, i)]


def _app() -> str:
    return (STATIC / "app.js").read_text(encoding="utf-8")


def _node(tmp_path, script):
    node = shutil.which("node")
    if not node:
        pytest.skip("needs Node")
    (tmp_path / "h.js").write_text(script, encoding="utf-8")
    run = subprocess.run([node, str(tmp_path / "h.js")], capture_output=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def _parts(js: str) -> dict:
    return {"tt": _cut(js, "function tt(key, vars) {", "const alertHost"),
            "says": _cut(js, "function says(e, o = {}) {", "// Data that loads for a block"),
            "undo": _cut(js, "async function undoEvent(id) {", "function eventRow(e) {")}


def _co(tmp_path):
    js = _app()
    return _node(tmp_path, CO_HARNESS % {**_parts(js), "co": _cut(js, "// ----- Checked out (R88", "// ----- Weigh Station voice")})


def _vo(tmp_path):
    js = _app()
    return _node(tmp_path, VO_HARNESS % {**_parts(js), "vo": _cut(js, "// ----- Weigh Station voice", "// ----- the language (R85 3.7")})


# ---- CO5 ----

def test_checked_out_is_shown_only_when_the_hubs_filters_name_it(tmp_path):
    out = _co(tmp_path)
    assert out["gate"] == [False, False, True, False]                 # an answer without `filters` is an older Hub (R88 01:55 point 2)
    assert out["older"] == [True, False] and out["missing"] == [True, False]
    assert out["zero"] == [False, "Checked out (0)"]                  # shown also at 0 (04 5.5)
    assert out["toggled"] == [True] and out["empty"] == "No spool is checked out."
    assert out["known"] is True                                       # an open sheet is told the gate may have changed


def test_the_rows_say_where_the_spool_is_its_home_and_since_when(tmp_path):
    out = _co(tmp_path)
    chip, listed = out["two"]
    assert chip == "Checked out (5)" and out["noTotal"] == "Checked out (2)"   # the list's `total` (R88 02:30)
    assert "In Workshop X1 – AMS 1 slot 2" in listed and "Home: Shelf A" in listed and "since 2026-11-21 18:30" in listed
    assert "Home: not recorded" in listed and listed.count("since ") == 1    # an entry without a time shows none (R88 1.2)
    assert out["labels"] == ["Return to Shelf A", "Return"]           # R88 02:05 point 2
    assert out["homes"] == ["Home: Shelf A", "Home: not recorded", "Return to Shelf A", "Return"]
    assert out["since"] == ["2026-11-22 01:05", "", "", ""]
    assert out["opened"] == [7]


def test_return_says_where_it_went_with_undo_and_the_hubs_refusal(tmp_path):
    out = _co(tmp_path)
    assert out["results"] == ["Returned to Shelf A.", "Returned to Dry box 1.", "Returned.", "Returned."]   # R88 02:05 point 3
    sent, toasts = out["returned"]
    assert sent[0] == "spools/7/return POST" and "spools?filter=checked_out GET" in sent[1:]      # at once; the count is read again
    assert toasts == [["Returned to Shelf A.", "ok", "common_undo"]]                              # Undo through the answer's event_id
    bad, none = out["refused"]
    assert bad == [["bad", "This spool is not checked out."]] and none == []                     # under the row, in the Hub's words
    assert out["plain"] == [["Returned.", "ok", None]]                                           # no place, nothing to undo


def test_the_spool_sheet_has_the_home_line_change_home_and_return():
    js = _app()
    sheet = _cut(js, "  function openSheet(id, opener, how = \"push\") {", "  const locsList = ")
    assert "const out = co.st.known && !!view && !!view.checked_out;" in sheet          # only while checked out, and only with the gate
    assert "homeText.textContent = homeLine(view); retBtn.textContent = returnLabel(view);" in sheet
    assert 'body: { home: l, dry_run: false }' in sheet and 'tt("checked_out_home_now", { location: now })' in sheet
    assert '{ label: tt("checked_out_home_title") }' in sheet                           # the menu "Home" of the storage locations
    assert "returnSpool(id, " in sheet and "body: h(\"div\", { class: \"spools__sheet\" }, sum, homeBox," in sheet
    assert "paintCo();" in _cut(sheet, "function paintActions() {", "// Update weight")
    tab = _cut(js, "function openTab() {", "// ---------- Presets")
    assert "lowBtn, co.chip)" in tab and "if (co.st.on) { co.load(); return; }" in tab
    assert "co.st.on = false; co.paint();" in _cut(tab, "function setLow(on)", "\n")      # one chip on at a time


def test_change_home_says_only_the_home_the_hubs_answer_gives(tmp_path):
    """R622 MEDIUM 1: "Home is now <location>." only from the answer's `view.home`; an answer without it says nothing false."""
    js = _app()
    out = _node(tmp_path, HOME_HARNESS % {**_parts(js), "home": _cut(js, "    async function setHome(l) {", "    async function sendBack(button) {")})
    assert out["took"] == ["Home is now Dry box 2.", "Dry box 2"]
    assert out["ignored"] == [None, None] and out["noView"] == [None, "Shelf A"]     # no line, and the home not made up
    assert out["refused"] == ["There is no such place.", "Shelf A"]                  # the Hub's refusal
    assert out["busy"] is True                                                        # R622 LOW: a second press waits


def test_a_search_on_its_way_does_not_replace_the_checked_out_list(tmp_path):
    """R622 LOW: pressing the chip makes a search answer still coming too late to draw."""
    js = _app()
    out = _node(tmp_path, FIND_HARNESS % {**_parts(js), "find": _cut(js, "  function setCo(on) {", "  function rowButton(f, s) {")})
    assert out["draws"] == ["co"] and out["busy"] is False


def test_the_checked_out_list_says_when_it_shows_one_page(tmp_path):
    out = _co(tmp_path)
    assert "2 of 5 shown." in out["two"][1]                          # R622 LOW: the chip counts `total`, the list one page


# ---- VO4 ----

def test_the_voice_panel_follows_the_hub(tmp_path):
    out = _vo(tmp_path)
    assert out["absent"] == [True, True]                             # no route (404), or no Weigh Station: no panel
    home = out["home"]
    assert home["hidden"] is False and home["test"] is True and home["disabled"] is False
    for text in ("settings_group_voice", "Speak",                     # the catalogue's "Weigh Station voice" (SW.t is a stub here)
                 "Voice: Piper, many speakers", "Change the voice on the Hub page.", "Speaker",
                 "This voice has 904 speakers. Try a few with Test.", "Speed", "100 %", "Volume", "Test"):
        assert text in home["words"], text
    assert "Change this at home." not in home["words"]
    assert {k: home["speed"][k] for k in ("min", "max", "step")} == {"min": "50", "max": "200", "step": "10"}
    assert {k: home["volume"][k] for k in ("min", "max")} == {"min": "0", "max": "100"}
    assert out["oneSpeaker"] is True                                 # speaker_range null: no Speaker row


def test_each_row_applies_when_let_go_and_goes_back_when_refused(tmp_path):
    out = _vo(tmp_path)
    said, sent = out["speak"]
    assert said == "common_saved" and sent == [["voice", "PATCH", {"speech": False}]]
    sent, value = out["speed"]
    assert sent == [["voice", "PATCH", {"speed": 150}]] and value == "150"
    value, bad = out["volumeRefused"]
    assert value == "50" and bad == [["bad", "Speed must be 50 to 200 in steps of 10."]]
    assert out["speaker"] == [0, "0", "Enter a whole number from 0 to 903."]


def test_test_plays_the_values_on_the_screen_and_says_the_four_results(tmp_path):
    out = _vo(tmp_path)
    toasts = [t[0] for t in out["tests"]]
    assert [t[0] for t in toasts] == ["Played on the Weigh Station.", "The Weigh Station is in use. Try again when it is idle.",
                                      "Speaking is switched off.", "The Weigh Station did not answer.", "Not allowed from here."]
    assert [t[1] for t in toasts] == ["ok", "bad", "bad", "bad", "bad"]
    assert out["tests"][0][1] == ["voice/test", "POST", {"speaker": 12, "speed": 120, "volume": 50}]


def test_the_public_address_shows_the_panel_read_only(tmp_path):
    out = _vo(tmp_path)
    away = out["away"]
    assert away["test"] is False and away["locked"] == [True, True, True] and away["speak"] is True
    assert away["words"].rstrip().endswith("Change this at home.")


def test_the_voice_rows_line_follows_remote(tmp_path):
    remote, local = _vo(tmp_path)["remote"]
    assert "Chosen on the Weigh Station's own computer: spoolwarden config." in remote and "Change the voice on the Hub page." not in remote
    assert "Change the voice on the Hub page." in local and "spoolwarden config" not in local


def test_the_voice_panel_is_drawn_and_marked_while_the_hub_is_away(tmp_path):
    """R622 MEDIUM 2 (04 9.2; R88 01:35 points 4 and 5)."""
    out = _vo(tmp_path)
    a = out["awayLast"]
    assert a["hidden"] is False and a["asked"] == 0 and a["group"] is True       # at once, from the values last read
    assert a["marks"] == [True, True, True, True, True]                         # Speak, Speaker, Speed, Volume, Test
    assert out["awaySent"] == [0, "100", "0", True]                              # nothing sent; each control stays as it was
    n = out["awayNone"]
    assert n["hidden"] is False and "shell_hub_cannot_be_reached" in n["words"] and n["asked"] == 0
    assert out["back"] == {"hidden": False, "speed": True, "hooks": 1}           # read again when the Hub answers
    assert out["twin502"]["hidden"] is False and "shell_hub_cannot_be_reached" in out["twin502"]["words"] and "Speed" in out["twin502"]["words"]


def test_the_panel_is_in_the_settings_after_the_alerts():
    body = _cut(_app(), "function settingsBody(wide) {", "function addToHome(opener) {")
    assert body.index("alertsGroup(group),") < body.index("voiceGroup(group),") < body.index('SW.t("settings_this_hub")')
