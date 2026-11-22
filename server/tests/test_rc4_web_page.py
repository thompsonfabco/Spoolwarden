"""RC4's page scripts run in Node without a browser (review R574): Leave demo that fails, Remove on Corrections waiting, the not-queued
cards and their reasons, the thumbnails the page's policy lets through, Try again when the reader does not answer, the Corrections link,
the Add spools line of 04 7.5, the push subscription sent again in the language the page is in, the Klipper test's answer, and the
service worker keeping a language's texts. The page's own functions are cut out of the scripts and given stubs for the DOM, `api` and `SW`."""
from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from test_web_app import STATIC

STUBS = r"""
const made = [];
function mk(tag, props, kids) {
  const e = { tag, props: props || {}, kids: kids.flat(Infinity).filter((k) => k != null && k !== false), removed: false, attrs: {}, hidden: !!(props && props.hidden),
    remove() { this.removed = true; }, setAttribute(k, v) { this.attrs[k] = v; }, querySelectorAll() { return []; }, querySelector() { return { focus() {} }; },
    replaceChildren(...k) { this.kids = k.flat(Infinity).filter((x) => x != null && x !== false); }, append(...k) { this.kids.push(...k); },
    get firstChild() { return this.kids[0] || null; } };
  made.push(e);
  return e;
}
const h = (tag, props, ...kids) => mk(tag, props, kids);
const words = (e) => (e == null ? "" : typeof e === "string" ? e : [e.props && e.props.textContent, e.props && e.props.label, e.text, ...(e.kids || []).map(words)].filter(Boolean).join(" "));
const tt = (k, v) => k + (v ? JSON.stringify(v) : "");
class HubError extends Error {
  constructor(o) { super(o.message || o.error || ""); Object.assign(this, o); }
  text(fallback) { return this.unreachable ? "common_hub_no_answer" : this.message || this.error || fallback; }
}
const results = [], asked = [], calls = [];
let reloaded = 0;
const SW = { HubError, t: tt, icon: () => null, busy: () => true, idle: () => {},
  result: (line, kind, text, o) => { results.push([kind, kind ? String(text) : null, o && o.action ? o.action.label : null]); line.state = kind; },
  btn: (label, o = {}) => { const b = mk("button", { label, ...o }, []); b._run = o.run; return b; },
  empty: (o) => mk("empty", { textContent: o.title }, []), banner: (o) => mk("banner", { textContent: o.text }, []),
  ask: async (o) => { try { return await o.run(); } catch (err) { asked.push(err && err.text ? err.text("common_failed") : "common_failed"); return false; } } };
let answers = {};
const api = async (path, opts = {}) => {
  calls.push([path, opts.body === undefined ? "GET" : "POST", opts.body]);
  const a = answers[path];
  if (a instanceof Error) throw a;
  return typeof a === "function" ? a(path, opts) : a;
};
const location = { reload() { reloaded++; }, hash: "" };
const BASE = "/intake";
const needsHub = (b) => b, note = (kind, text) => h("note", { kind, textContent: text });
const recLabel = (k) => k, recWhen = (v) => String(v);
"""

HARNESS = STUBS + r"""
%(says)s
%(leave)s
%(share)s
// the inside of Recently added (historyTab) that the corrections need
const corrLink = h("p", { hidden: true }), corr = h("section", {});
let shareOn = false, records = [];
const hp = (id, what) => `history/${encodeURIComponent(id)}${what ? "/" + what : ""}`;
const photo = (id, n) => `${BASE}/api/${hp(id, "photos/" + n)}`;
const HUB_PAGE = "";
const fill = async (region, thing, fn) => { try { region.kids = [await fn()]; } catch (e) { region.failed = String(e); } };
%(corr)s
%(run)s
(async () => {
  const out = {};
  if (RUN.has("leave")) {
  // HIGH 1: a Leave demo the Hub refuses (502 from the twin) says the Hub's words; nothing recurses
  answers = { "demo/leave": new HubError({ status: 502, error: "unreachable", message: "The Hub did not answer.", data: { error: "unreachable" } }) };
  await leaveDemo(null);
  out.reloadedAfter502 = reloaded;
  out.leaveSaid = asked.slice();
  answers = { "demo/leave": new HubError({ status: 409, error: "not_in_demo", message: "The demo is not on.", data: { error: "not_in_demo" } }) };
  await leaveDemo(null);
  out.reloadedAfter409 = reloaded;
  }
  if (RUN.has("remove")) {
  // HIGH 2: Remove keeps its button: the event has no currentTarget once the Hub answered
  records = [{ id: "r1", photos: 2 }];
  answers = { "label-reader/corrections/c1/remove": { ok: true, removed: "c1" } };
  results.length = 0;
  corrCard({ id: "c1", record: "r1", photos: 2, corrected: { vendor: "Polymaker", name: "PolyTerra" }, predicted: {} }, new Set(["r1"]));
  const rm = made.filter((b) => b.tag === "button" && b.props.action === "remove-correction").pop();
  const ev = { currentTarget: rm };
  const p = rm._run(ev);
  ev.currentTarget = null;                                  // what the browser does once the click is dispatched
  await p;
  out.removeResults = results.slice();
  out.removeButtonGone = rm.removed;
  }
  if (RUN.has("reasons")) {
  // the reasons, empty or not (no dangling ": .")
  out.notTaken = [notTaken(""), notTaken("  "), notTaken("the reader refused")];
  out.notWaiting = [notWaiting(undefined), notWaiting("too small")];
  }
  if (RUN.has("thumbs")) {
  // MED 13: what the page's policy (img-src 'self' blob:) lets through
  out.thumbs = [thumbSrc("data:image/png;base64,iVBORw0KGgo="), thumbSrc("iVBORw0KGgo="), thumbSrc("https://reader.example/t.jpg"),
    thumbSrc("/elsewhere/t.jpg"), thumbSrc(`${BASE}/api/history/r1/photos/1`), thumbSrc("")];
  const card = corrCard({ id: "c2", thumbnail: "https://reader.example/t.jpg", corrected: { name: "X" } }, new Set());
  out.blockedThumb = JSON.stringify(card.kids[0].props);
  }
  if (RUN.has("pages")) {
  // LOW: the not-queued records are listed with the triangle line, and Try again when the reader does not answer
  answers = { "label-reader/corrections": { opt_in: true, count: 0, bytes: 0, entries: [], not_queued: [{ record: "r1", at: "T", name: "Polymaker PolyTerra", reason: "" }] } };
  await loadCorr();
  out.notQueuedPage = words(corr);
  answers = { "label-reader/corrections": new HubError({ status: 503, error: "The reader does not answer.", data: { code: "unreachable" } }) };
  await loadCorr();
  const again = made.filter((b) => b.tag === "button" && b.props.action === "try-again").pop();
  out.readerDown = [words(corr).includes("corr_reader_down"), !!again && again.props.label];
  }
  if (RUN.has("links")) {
  // LOW: the link under the intro: none with sharing off and nothing waiting; the waiting count with sharing off; the on line
  for (const d of [{ actual: false, count: 0 }, { actual: false, count: 2 }, { actual: true, count: 3 }]) {
    answers = { "share-corrections": d };
    await loadShare();
    (out.links = out.links || []).push(corrLink.hidden ? null : words(corrLink));
  }
  answers = { "share-corrections": new HubError({ status: 404, error: "not_found" }) };
  await loadShare();
  out.links.push(corrLink.hidden ? null : words(corrLink));
  }
  console.log(JSON.stringify(out));
})().catch((e) => { console.error("harness: " + (e && e.stack || e)); process.exit(3); });
"""

FOUND_HARNESS = STUBS + r"""
%(share)s
let found = null;
const foundBox = h("div", {}), line = (kind, text) => h("p", { kind, textContent: text }), addSummaryLine = () => null;
const group = (title, ...kids) => h("section", { textContent: title }, ...kids);
%(found)s
(async () => {
  const out = {};
  found = { count: 2, photos: 2, reader: "cloud", model: "m", tier: "", rated: true, summary: null, questions: [], tag: null, scale: null, usage: null, note: "" };
  answers = { "share-corrections": { actual: true, count: 0 } };
  await readShare(); drawFound(); out.on = words(foundBox);
  answers = { "share-corrections": { actual: false, count: 0 } };
  await readShare(); drawFound(); out.off = words(foundBox);
  answers = { "share-corrections": new HubError({ status: 404, error: "not_found" }) };
  await readShare(); drawFound(); out.noTwin = words(foundBox);
  found = { ...found, count: 0 };
  answers = { "share-corrections": { actual: true, count: 0 } };
  await readShare(); drawFound(); out.none = words(foundBox);
  console.log(JSON.stringify(out));
})().catch((e) => { console.error("harness: " + (e && e.stack || e)); process.exit(3); });
"""

PUSH_HARNESS = STUBS + r"""
const store = { "spoolwarden.push_language": "en" };
const localStorage = { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }, removeItem: (k) => { delete store[k]; } };
const document = { documentElement: { lang: "de" } };
let sub = { toJSON: () => ({ endpoint: "https://push.example/1" }) };
const navigator = { serviceWorker: { getRegistration: async () => ({ pushManager: { getSubscription: async () => sub } }) } };
const window = { PushManager: function () {} };
answers = { "printers/push/subscribe": { ok: true } };
%(push)s
(async () => {
  const out = {};
  await followPushLanguage(); out.first = calls.slice(); out.kept = store["spoolwarden.push_language"];
  calls.length = 0; await followPushLanguage(); out.again = calls.slice();
  document.documentElement.lang = "ru"; sub = null; calls.length = 0;
  await followPushLanguage(); out.noSub = [calls.slice(), store["spoolwarden.push_language"]];
  console.log(JSON.stringify(out));
})().catch((e) => { console.error("harness: " + (e && e.stack || e)); process.exit(3); });
"""

KLIPPER_HARNESS = r"""
const field = (v) => ({ _control: { value: v } });
const f = { kAddr: field("voron.local"), kPort: field("7125"), kKey: field("") };
const parsePort = Number, v = (x) => x._control.value.trim();
let answer;
const pj = async (path, body) => { if (answer instanceof Error) throw answer; return answer; };
%(klipper)s
(async () => {
  const out = [];
  for (const a of [{ reachable: false }, { reachable: true }, new Error("no route"), { ok: false }]) { answer = a; out.push(await klipperTest(v)); }
  console.log(JSON.stringify(out));
})();
"""

SW_HARNESS = r"""
const fs = require("fs"), vm = require("vm");
const src = fs.readFileSync(process.argv[2], "utf8").replace("__VERSION__", "t");
const stored = new Map(), handlers = {};
let down = false;
const ctx = { URL, Response: { error: () => "error" },
  self: { registration: { scope: "https://x/intake" }, location: { origin: "https://x" }, skipWaiting() {}, clients: { claim() {} },
          addEventListener: (n, f) => { handlers[n] = f; } },
  caches: { open: async () => ({ put: async (u, r) => { stored.set(String(u), r); } }), keys: async () => [], match: async (u) => stored.get(String(u)) },
  fetch: async (u) => { if (down) throw new TypeError("offline"); const x = String(u && u.url ? u.url : u); return { ok: true, redirected: false, url: x, body: "from the Hub " + x, clone() { return { ...this, body: "copy " + x }; } }; } };
vm.runInNewContext(src, ctx);
const ask = async (path) => { let hit = null; handlers.fetch({ request: { method: "GET", url: "https://x" + path }, respondWith: (p) => { hit = p; } }); return hit ? await hit : "not handled"; };
(async () => {
  const out = {};
  out.online = (await ask("/intake/api/strings/de.js")).body;
  await new Promise((r) => setTimeout(r, 20));
  out.kept = [...stored.keys()];
  down = true;
  out.offline = (await ask("/intake/api/strings/de.js")).body;
  out.otherApi = await ask("/intake/api/hub");
  out.badName = await ask("/intake/api/strings/../hub.js");
  console.log(JSON.stringify(out));
})();
"""


def _cut(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i:text.index(end, i)]


def _app() -> str:
    return (STATIC / "app.js").read_text(encoding="utf-8")


def _node(tmp_path, script, *args):
    node = shutil.which("node")
    if not node:
        pytest.skip("needs Node")
    (tmp_path / "h.js").write_text(script, encoding="utf-8")
    run = subprocess.run([node, str(tmp_path / "h.js"), *args], capture_output=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def _maybe(text: str, start: str, end: str) -> str:
    try:
        return _cut(text, start, end)
    except ValueError:
        return ""                     # a part this run does not call (a script from before it existed runs the rest)


def _page(tmp_path, *run):
    """Runs the named sections only, each on the functions it calls, so that each test proves its own finding."""
    js = _app()
    return _node(tmp_path, HARNESS % {
        "says": _cut(js, "function says(e, o = {}) {", "// Data that loads for a block"),
        "leave": _cut(js, "async function leaveDemo(opener) {", "// The own entries of the Filament Catalog"),
        "share": _maybe(js, "let SHARE = null;", "function historyTab() {"),
        "corr": _cut(js, "  async function loadShare() {", "  function showCorr(on) {"),
        "run": f"const RUN = new Set({json.dumps(list(run))});"})


def test_a_failed_leave_demo_says_the_hubs_words_and_does_not_recurse(tmp_path):
    out = _page(tmp_path, "leave")
    assert out["reloadedAfter502"] == 0                              # the dialog stays: nothing was left
    assert out["leaveSaid"] == ["The Hub did not answer."]          # the Hub's text in the dialog, no RangeError
    assert out["reloadedAfter409"] == 1                             # already off: the banner goes
    assert "() => says(" not in _app()                              # the same pattern nowhere else (catalog remove, mark empty)


def test_remove_on_corrections_waiting_reports_its_success(tmp_path):
    out = _page(tmp_path, "remove")
    assert out["removeResults"][-1] == ["ok", "corr_removed", None] and out["removeButtonGone"] is True
    assert not any(r[0] == "bad" for r in out["removeResults"])


def test_not_queued_records_and_empty_reasons(tmp_path):
    out = _page(tmp_path, "reasons", "pages")
    assert out["notTaken"] == ["corr_not_taken_plain", "corr_not_taken_plain", 'corr_not_taken{"reason":"the reader refused"}']
    assert out["notWaiting"] == ["corr_not_waiting_plain", 'corr_not_waiting{"reason":"too small"}']
    assert "Polymaker PolyTerra" in out["notQueuedPage"] and "corr_not_waiting_plain" in out["notQueuedPage"]
    assert "corr_empty_title" not in out["notQueuedPage"]          # a not-queued record is something to show


def test_thumbnails_are_same_origin_or_blob(tmp_path):
    out = _page(tmp_path, "thumbs")
    data, bare, https, elsewhere, own, empty = out["thumbs"]
    assert data.startswith("blob:") and bare.startswith("blob:")    # a base64 thumbnail becomes a blob: the policy allows it
    assert https is None and elsewhere is None and empty is None    # a picture from elsewhere would be blocked: not shown
    assert own == "/intake/api/history/r1/photos/1"
    assert "thumb--none" in out["blockedThumb"]


def test_the_reader_down_offers_try_again_and_the_link_follows_sharing(tmp_path):
    out = _page(tmp_path, "pages", "links")
    assert out["readerDown"] == [True, "common_try_again"]
    assert out["links"][0] is None                                  # sharing off and nothing waiting: no link
    assert 'corr_link_waiting{"count":2}' in out["links"][1] and 'corr_link_on{"count":3}' in out["links"][2]
    assert out["links"][3] is None                                  # a Hub without the twin: no link


def test_the_add_spools_read_summary_says_corrections_will_wait(tmp_path):
    js = _app()
    out = _node(tmp_path, FOUND_HARNESS % {"share": _cut(js, "let SHARE = null;", "function historyTab() {"),
                                           "found": _cut(js, "  function drawFound() {", "  // ============ By hand")})
    assert out["on"].rstrip().endswith("corr_add_on")              # under the summary's last line, while sharing is on
    assert "corr_add_on" not in out["off"] and "corr_add_on" not in out["noTwin"] and "corr_add_on" not in out["none"]


def test_the_push_subscription_is_sent_again_in_the_language_the_page_is_in(tmp_path):
    js = _app()
    out = _node(tmp_path, PUSH_HARNESS % {"push": _cut(js, "const PUSH_LANG_KEY", "function languageGroup(group) {")})
    assert [c[0] for c in out["first"]] == ["printers/push/subscribe"] and out["first"][0][2]["lang"] == "de" and out["kept"] == "de"
    assert out["again"] == []                                       # once per language, not at every load
    assert out["noSub"] == [[], "de"]                               # no subscription: nothing sent, nothing marked
    assert "resendPush(v ===" not in js                             # the change itself no longer sends the old page language


def test_a_klipper_printer_that_does_not_answer_is_not_added_without_asking(tmp_path):
    pj = (STATIC / "printers.js").read_text(encoding="utf-8")
    out = _node(tmp_path, KLIPPER_HARNESS % {"klipper": _cut(pj, "    async function klipperTest(v) {", "    async function submit(force) {")})
    assert out == [False, True, True, True]                         # {reachable: false} is the printer's answer (R87 1)
    which = _cut(pj, '      if (step === "which") {', '      } else if (step === "details") {')
    assert "family-bambu" not in which and "family-klipper" not in which       # no family choice (R87 00:55)
    assert which.index('"type-bambu"') < which.index('"type-klipper"')          # the two buttons, Bambu Lab first
    assert "printer_guided_type_bambu: \"Type a Bambu Lab printer's address\"" in pj
    assert "printer_guided_type_klipper: \"Type a Klipper printer's address\"" in pj


def test_the_service_worker_keeps_a_languages_texts_for_offline(tmp_path):
    out = _node(tmp_path, SW_HARNESS, str(STATIC / "sw.js"))
    assert out["online"].startswith("from the Hub") and "/intake/api/strings/de.js" in out["kept"]
    assert out["offline"] == "copy https://x/intake/api/strings/de.js"         # the app opens offline in the language last used
    assert out["otherApi"] == "not handled" and out["badName"] == "not handled"
