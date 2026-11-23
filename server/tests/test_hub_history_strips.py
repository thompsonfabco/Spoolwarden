"""Step B2 on the Hub page, in a real headless browser: the history strips of the Checks ("Last 7 days", from `GET health`), Show history and
Show 30 days, the Label Reader tile's line "<n> corrections waiting to be shared", the columns of the Corrections waiting card, and the line
a busy dialog gives for Cancel and Escape. The Hub is the real test Hub of test_hub_page_audit.py; a wrapper answers `health` with a chosen
view (and a refusal) where a test needs one, and the last test of the strips asks the real route over events written to the real store. The
same skip rules as the page audit: no Node 22 or no browser is a skip (a failure with AUT5_REQUIRED=1)."""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import httpx
import pytest

from filament_kit import events

from test_hub_corrections import _add, _drive_corr, _reader, _rid
from test_hub_page_audit import _serve, hub_app  # noqa: F401
from test_hub_presets_ui import T, _drive, hub  # noqa: F401

STAMP = r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}"
DAY = r"\d{4}-\d{2}-\d{2}"


def _since_sentence(got):
    """Whether `got` is "No history before <date>." with a date of the form the page writes."""
    before, after = T("overview_strip_since", date="\u0001").split("\u0001")
    return re.fullmatch(re.escape(before) + DAY + re.escape(after), got or "") is not None


class HealthStub:
    """ASGI wrapper around the real Hub: `GET health?days=N` answers `state["answers"][N]` (a refusal while `state["fail"]` is a status),
    and the real route when the test gave no answer for N; /stub/set patches the state from the page; `calls` lists the days asked."""

    def __init__(self, app):
        self.app = app
        self.state = {"answers": {}, "fail": None, "calls": []}

    async def reply(self, send, status, body):
        raw = json.dumps(body).encode()
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"),
                                                                                  (b"content-length", str(len(raw)).encode()), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": raw})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        if scope["path"] == "/stub/set":
            raw = b""
            while True:
                m = await receive()
                raw += m.get("body", b"")
                if not m.get("more_body"):
                    break
            self.state.update(json.loads(raw or b"{}"))
            return await self.reply(send, 200, {"ok": True})
        if scope["path"] == "/setup/api/health":
            days = parse_qs(scope["query_string"].decode()).get("days", ["7"])[0]
            self.state["calls"].append(days)
            if self.state["fail"]:
                return await self.reply(send, self.state["fail"], {"error": "the stub says no"})
            if days in self.state["answers"]:
                return await self.reply(send, 200, self.state["answers"][days])
        await self.app(scope, receive, send)


@pytest.fixture
def hub_health(hub_app):  # noqa: F811
    """(the wrapper, the page's address)"""
    app = HealthStub(hub_app[0])
    running = _serve(app, hub_app[1])
    try:
        yield app, running.url + "/setup"
    finally:
        running.stop()


PRELUDE = r"""
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(look, what) { for (let i = 0; i < 200; i++) { const v = look(); if (v) return v; await sleep(100); } throw new Error("waited for " + what + "; the page shows: " + document.querySelector("main").innerText.slice(0, 300)); }
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const text = (el) => (el ? el.textContent.trim().replace(/\s+/g, " ") : null);
const stub = (patch) => fetch("/stub/set", { method: "POST", body: JSON.stringify(patch) });
const slot = (k) => $(`[data-strip="${k}"]`);
const strip = (k) => $(".history-strip", slot(k));
const tipOf = (s) => document.getElementById(s.getAttribute("aria-describedby"));
const ready = async () => {
  await until(() => $(".coll--checks") && !$(".chip--lock .skeleton__line") && !$('main [aria-busy="true"]:not(button)'), "the checks");
};
"""


def _page(hub, tmp_path, body, consts=None, width=1280):
    """Runs `body` on the Overview (async, `return out`); `consts` is the test's data as `C` in the page."""
    script = PRELUDE + f"\nconst C = {json.dumps(consts or {})};\nawait ready();\nconst out = {{}};\n" + body + "\nreturn out;\n"
    return _drive(hub[1], tmp_path, script, width=width)


def _iso(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _rows(hub):
    """Two rows of the Checks with a key (the page draws a strip for each)."""
    rows = httpx.get(hub[1] + "/api/overview", timeout=120).json()["rows"]
    keyed = [r for r in rows if r.get("key") and r["state"] in ("ok", "fail")]
    assert len(keyed) >= 2, rows
    return keyed[0], keyed[1]


def _views(now, k1, text1):
    """The health views of the example: a stretch two days ago, one that still lasts, and (30 days) one twenty days ago."""
    to = _iso(now)
    a = {"from": _iso(now - timedelta(days=3)), "to": _iso(now - timedelta(days=2, hours=12)), "state": "look"}
    b = {"from": _iso(now - timedelta(hours=3)), "to": to, "state": "look"}
    c = {"from": _iso(now - timedelta(days=20)), "to": _iso(now - timedelta(days=19)), "state": "look"}

    def view(days, spans, since):
        return {"days": days, "from": _iso(now - timedelta(days=days)), "to": to, "since": since,
                "checks": [{"key": k1, "group": "checks", "text": text1, "now": "look", "spans": spans}],
                "changes": [{"at": b["from"], "key": k1, "text": text1, "from": "ok", "to": "look"},
                            {"at": a["to"], "key": k1, "text": text1, "from": "look", "to": "ok"},
                            {"at": a["from"], "key": k1, "text": "Workshop X1 offline", "from": "ok", "to": "bad"}]}
    return {"7": view(7, [a, b], _iso(now - timedelta(days=20))), "30": view(30, [c, a, b], _iso(now - timedelta(days=25)))}


def _setup(hub_health, tmp_path_unused=None):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    r1, r2 = _rows(hub_health)
    app = hub_health[0]
    app.state["answers"] = _views(now, r1["key"], r1["text"])
    return {"k1": r1["key"], "k2": r2["key"], "t1": r1["text"], "t2": r2["text"]}


# ---------- (a) the strip ----------

def test_a_strip_names_its_history_in_its_label_and_marks_a_stretch_by_height_pattern_and_tick(hub_health, tmp_path):
    c = _setup(hub_health)
    page = _page(hub_health, tmp_path, r"""
await until(() => strip(C.k1) && strip(C.k2), "both strips");
const s = strip(C.k1), tip = tipOf(s), marks = $$(".history-strip__span", s);
out.label = s.getAttribute("aria-label");
out.fine = strip(C.k2).getAttribute("aria-label");
out.wrap = [s.getAttribute("role"), s.getAttribute("tabindex")];
out.tip = { exists: !!tip, role: tip && tip.getAttribute("role"), hidden: tip && tip.hidden };
out.track = $(".history-strip__track", s).getAttribute("aria-hidden");
out.caption = text($(".history-strip__caption", s));
out.header = $$('.coll__head [data-col="history"]').map(text);
out.cell = slot(C.k1).closest('[data-col="history"]') !== null;
const plain = $(".history-strip__plain", s).getBoundingClientRect();
out.heights = { plain: plain.height, marks: marks.map((m) => m.getBoundingClientRect().height) };
out.widths = marks.map((m) => m.getBoundingClientRect().width);
const cs = getComputedStyle(marks[0]), root = getComputedStyle(document.documentElement);
const rgb = (v) => { const e = document.createElement("span"); e.style.color = `var(${v})`; document.body.append(e); const c = getComputedStyle(e).color; e.remove(); return c; };
out.mark = { image: cs.backgroundImage, tick: cs.borderInlineStartWidth, warn: rgb("--warn"), bad: rgb("--bad"), surface: rgb("--surface") };
const bad = marks[0].cloneNode(); bad.dataset.state = "bad"; marks[0].parentNode.append(bad);
out.badImage = getComputedStyle(bad).backgroundImage;
bad.remove();
out.noHistoryBefore = !!$(".history-strip__since", slot(C.k1)) || !!$(".history-strip__none", s);
""", c)
    before, after = T("overview_strip_label_look", check=c["t1"], days=7, count=2, sentence="\u0001").split("\u0001")
    label = page["label"]
    assert label.startswith(before) and label.endswith(after), label
    sentence = label[len(before):len(label) - len(after)]
    assert re.fullmatch(re.escape(c["t1"]) + " " + STAMP + " to " + re.escape(T("overview_strip_now")), sentence), sentence
    assert page["fine"] == T("overview_strip_label_fine", check=c["t2"], days=7)
    assert page["wrap"] == ["img", "0"] and page["tip"] == {"exists": True, "role": "tooltip", "hidden": True} and page["track"] == "true"
    assert page["caption"] == T("overview_strip_caption", days=7) == "Last 7 days"
    assert page["header"] and set(page["header"]) == {"Last 7 days"} and page["cell"] is True
    # told by height, pattern and tick as well as colour: the marked stretch is twice the plain track, hatched, with a 1 px tick at its start
    assert page["heights"]["plain"] > 0 and page["heights"]["marks"] == [page["heights"]["plain"] * 2] * 2
    assert all(w >= 4 for w in page["widths"])
    assert "repeating-linear-gradient" in page["mark"]["image"] and page["mark"]["tick"] == "1px"
    mark = page["mark"]                              # a look is hatched in --warn, a bad one in --bad, both with --surface lines
    assert mark["warn"] in mark["image"] and mark["surface"] in mark["image"] and mark["bad"] in page["badImage"] and mark["warn"] != mark["bad"]
    assert mark["warn"] not in page["badImage"]
    assert page["noHistoryBefore"] is False         # the history reaches back past the range


def test_a_strip_moves_its_tooltip_by_the_arrow_keys_and_hides_it_with_escape(hub_health, tmp_path):
    c = _setup(hub_health)
    page = _page(hub_health, tmp_path, r"""
await until(() => strip(C.k1), "the strip");
const s = strip(C.k1), tip = tipOf(s);
const key = (k) => s.dispatchEvent(new KeyboardEvent("keydown", { key: k, bubbles: true, cancelable: true }));
key("ArrowLeft");                                      // nothing shown yet: the newest stretch
out.first = { hidden: tip.hidden, text: text(tip), times: $$("time.mono", tip).length };
await sleep(150); out.said1 = text($("#announcer"));
key("ArrowLeft"); out.second = text(tip);
await sleep(150); out.said2 = text($("#announcer"));
key("ArrowLeft"); out.stay = text(tip);                // the oldest: it stays
key("ArrowRight"); out.back = text(tip);
s.focus();
key("Escape");
out.esc = { hidden: tip.hidden, focused: document.activeElement === s };
const mark = $$(".history-strip__span", s)[0];
mark.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
out.early = tip.hidden;
await sleep(750);
out.hover = { hidden: tip.hidden, text: text(tip) };
s.dispatchEvent(new MouseEvent("mouseleave"));
await sleep(350);
out.left = tip.hidden;
// a long press on a touch screen: the finger stays on a stretch; a touch ending elsewhere hides the tooltip again
mark.dispatchEvent(new PointerEvent("pointerover", { bubbles: true, pointerType: "touch" }));
await sleep(150); out.touchEarly = tip.hidden;
await sleep(550); out.touch = { hidden: tip.hidden, text: text(tip) };
document.body.dispatchEvent(new PointerEvent("pointerup", { bubbles: true, pointerType: "touch" }));
out.touchAway = tip.hidden;
""", c)
    now_word = T("overview_strip_now")
    newest = re.compile(re.escape(c["t1"]) + " " + STAMP + " to " + re.escape(now_word))
    older = re.compile(re.escape(c["t1"]) + " " + STAMP + " to " + STAMP)
    assert page["first"]["hidden"] is False and newest.fullmatch(page["first"]["text"]) and page["first"]["times"] == 2
    assert page["said1"] == page["first"]["text"]
    assert older.fullmatch(page["second"]) and page["said2"] == page["second"] and page["stay"] == page["second"]
    assert page["back"] == page["first"]["text"]
    assert page["esc"] == {"hidden": True, "focused": True}
    assert page["early"] is True and page["hover"]["hidden"] is False and page["hover"]["text"] == page["second"] and page["left"] is True
    assert page["touchEarly"] is True and page["touch"] == {"hidden": False, "text": page["second"]} and page["touchAway"] is True


# ---------- (b) the card ----------

def test_show_30_days_and_back_reads_the_range_again_and_says_where_the_history_starts(hub_health, tmp_path):
    c = _setup(hub_health)
    c.update(d30=T("overview_history_30"), d7=T("overview_history_7"))
    page = _page(hub_health, tmp_path, r"""
await until(() => strip(C.k1), "the strip");
const btn = $('[data-action="history-days"]');
const days = (n) => until(() => strip(C.k1) && strip(C.k1).getAttribute("aria-label").includes(`last ${n} days`), `the ${n} days`);
out.start = { btn: text(btn), since: !!$(".history-strip__since", slot(C.k1)) };
btn.click();
await days(30);
const s = strip(C.k1);
out.thirty = { btn: text(btn), label: s.getAttribute("aria-label"), caption: text($(".history-strip__caption", s)), header: text($('.coll__head [data-col="history"]')),
  since: text($(".history-strip__since", slot(C.k1))), none: getComputedStyle($(".history-strip__none", s)).borderTopStyle, noneWidth: $(".history-strip__none", s).getBoundingClientRect().width,
  marks: $$(".history-strip__span", s).length };
btn.click();
await days(7);
out.seven = { btn: text(btn), since: !!$(".history-strip__since", slot(C.k1)), none: !!$(".history-strip__none", strip(C.k1)) };
""", c)
    app = hub_health[0]
    assert page["start"] == {"btn": c["d30"], "since": False}
    th = page["thirty"]
    assert th["btn"] == c["d7"] and th["caption"] == "Last 30 days" and th["header"] == "Last 30 days" and "last 30 days" in th["label"]
    assert T("overview_strip_label_look", check=c["t1"], days=30, count=3, sentence="\u0001").split("\u0001")[0] in th["label"] and th["marks"] == 3
    assert _since_sentence(th["since"]), th["since"]
    assert th["none"] == "dashed" and th["noneWidth"] > 0
    assert page["seven"] == {"btn": c["d30"], "since": False, "none": False}
    assert app.state["calls"][0] == "7" and "30" in app.state["calls"] and app.state["calls"][-1] == "7"


def test_a_history_that_cannot_be_read_leaves_the_strips_out_keeps_the_table_and_tries_again(hub_health, tmp_path):
    c = _setup(hub_health)
    hub_health[0].state["fail"] = 500
    page = _page(hub_health, tmp_path, r"""
const note = await until(() => $(".overview__history .banner"), "the line");
out.line = text(note);
out.strips = $$(".history-strip").length;
out.table = $$(".coll--checks .coll__item").length > 0;
out.cells = $$(".coll--checks .history:empty").length === $$(".coll--checks .history").length;
await stub({ fail: null });
const again = $('[data-action="history-again"]');
out.again = text(again);
again.click();
await until(() => strip(C.k1), "the strip");
out.after = { strips: $$(".history-strip").length > 0, line: !!$(".overview__history .banner") };
""", c)
    assert page["line"].startswith(T("overview_history_error", reason="The stub says no.")), page["line"]
    assert page["strips"] == 0 and page["table"] is True and page["cells"] is True
    assert page["again"] == T("common_try_again") and page["after"] == {"strips": True, "line": False}


def test_show_history_opens_the_table_of_the_changes_at_its_address_and_a_phone_gets_a_sheet(hub_health, tmp_path):
    c = _setup(hub_health)
    for width in (1280, 390):
        page = _page(hub_health, tmp_path, r"""
await until(() => strip(C.k1), "the strip");
$('[data-action="history-show"]').click();
const d = await until(() => $("dialog[open] .history__table") && $("dialog[open]"), "the dialog");
out.hash = location.hash;
out.sheet = d.classList.contains("sheet");
out.title = text($(".dialog__title, .sheet__title", d));
out.cols = $$("th", d).map(text);
out.rows = $$("tbody tr", d).map((tr) => $$("td", tr).map(text));
out.foot = $$(".history__foot", d).map(text);
d.close();
await sleep(200);
out.hashAfter = location.hash;
""", c, width=width)
        assert page["hash"] == "#overview/history" and page["hashAfter"] == "#overview" and page["sheet"] is (width == 390)
        assert page["title"] == T("overview_history_title") == "History of the checks"
        assert page["cols"] == [T("overview_history_col_time"), T("overview_col_check"), T("overview_history_col_from"), T("overview_history_col_to")]
        # newest first; the words of the states; a check at its worst is "Needs a look now"
        assert [(r[1], r[2], r[3]) for r in page["rows"]] == [
            (c["t1"], T("status_ok"), T("status_needs_look")), (c["t1"], T("status_needs_look"), T("status_ok")),
            ("Workshop X1 offline", T("status_ok"), T("overview_history_bad"))]
        assert all(re.fullmatch(STAMP, r[0]) for r in page["rows"])
        assert page["foot"] == [T("overview_history_foot", days=7)]


def test_the_table_of_an_empty_history_says_so_and_where_history_starts(hub_health, tmp_path):
    c = _setup(hub_health)
    now = datetime.now(timezone.utc).replace(microsecond=0)
    hub_health[0].state["answers"] = {"7": {"days": 7, "from": _iso(now - timedelta(days=7)), "to": _iso(now), "since": None, "checks": [], "changes": []}}
    page = _page(hub_health, tmp_path, r"""
await until(() => strip(C.k1), "the strip");
out.label = strip(C.k1).getAttribute("aria-label");
out.none = $(".history-strip__none", strip(C.k1)).getBoundingClientRect().width / $(".history-strip__track", strip(C.k1)).getBoundingClientRect().width;
out.since = text($(".history-strip__since", slot(C.k1)));
$('[data-action="history-show"]').click();
const d = await until(() => $("dialog[open] .history__body") && $("dialog[open]"), "the dialog");
out.table = !!$("table", d);
out.lines = $$(".history__body > p", d).map(text);
""", c)
    assert page["label"] == T("overview_strip_label_fine", check=c["t1"], days=7)
    assert page["none"] > 0.99 and _since_sentence(page["since"])
    assert page["table"] is False
    assert page["lines"][0] == T("overview_history_empty", days=7) and _since_sentence(page["lines"][1])


def test_the_real_health_route_draws_the_strip_of_a_check_that_needed_a_look(hub_health, tmp_path):
    r1, _ = _rows(hub_health)
    key, name = r1["key"], r1["text"]
    now = datetime.now(timezone.utc)
    st = events.store()
    st.write("check.failed", {"type": "hub"}, {"type": "hub"}, {"text": name}, key=key, now=now - timedelta(days=2))
    st.write("check.ok", {"type": "hub"}, {"type": "hub"}, {"text": name}, key=key, now=now - timedelta(days=1))
    page = _page(hub_health, tmp_path, r"""
await until(() => strip(C.k1) && strip(C.k1).getAttribute("aria-label").includes("needed a look"), "the strip of the check");
out.label = strip(C.k1).getAttribute("aria-label");
out.since = !!$(".history-strip__since", slot(C.k1));
$('[data-action="history-show"]').click();
const d = await until(() => $("dialog[open] .history__table") && $("dialog[open]"), "the dialog");
out.rows = $$("tbody tr", d).map((tr) => $$("td", tr).map(text));
""", {"k1": key})
    before, after = T("overview_strip_label_look", check=name, days=7, count=1, sentence="\u0001").split("\u0001")
    assert page["label"].startswith(before) and page["label"].endswith(after), page["label"]
    assert re.fullmatch(re.escape(name) + " " + STAMP + " to " + STAMP, page["label"][len(before):len(page["label"]) - len(after)])
    assert page["since"] is True                                   # the history starts two days ago: before it the track is dashed
    assert [(r[1], r[2], r[3]) for r in page["rows"]] == [(name, T("status_needs_look"), T("status_ok")), (name, T("status_ok"), T("status_needs_look"))]


# ---------- (d) the Label Reader tile's line ----------

TILE = r"""
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(look, what) { for (let i = 0; i < 200; i++) { const v = look(); if (v) return v; await sleep(100); } throw new Error("waited for " + what + "; the page shows: " + document.querySelector("main").innerText.slice(0, 300)); }
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const text = (el) => (el ? el.textContent.trim().replace(/\s+/g, " ") : null);
const tile = () => $$(".tile").find((a) => a.getAttribute("href") === "#settings/label-reader");
await until(() => tile() && !$('main [aria-busy="true"]:not(button)'), "the tile");
await sleep(2000);                                     // the line is read after the tile: whether sharing is on, then how many wait
const out = {};
const line = $("[data-corr-line]");
out.line = line ? text(line) : null;
out.inTile = line ? line.closest("a") === tile() : null;
out.href = tile().getAttribute("href");
"""


@pytest.mark.parametrize("case, saved, waiting", [("two", True, 2), ("one", True, 1), ("none waiting", True, 0), ("sharing off", False, 2)])
def test_the_label_reader_tile_links_the_corrections_waiting_only_while_sharing_is_on(hub, fake_reader, monkeypatch, tmp_path, case, saved, waiting):
    data = _reader(fake_reader, monkeypatch, saved=saved)
    for i in range(waiting):
        _add(data, _rid() if i == 0 else f"zz-gone-{i}")
    follow = r"""
if (out.line) {
  $("[data-corr-line]").click();
  await until(() => location.hash === "#settings/label-reader" && $("#set-corrections") && !$("#set-corrections").hidden && $("#set-corrections").getClientRects().length, "the card");
  await sleep(500);
  out.arrived = window.__swCorrArrived === false;
}
"""
    page = _drive(hub, tmp_path, TILE + follow + "\nreturn out;\n")
    if waiting and saved:
        assert page["line"] == T("overview_tile_reader_corrections", count=waiting), page
        assert page["inTile"] is True and page["href"] == "#settings/label-reader" and page["arrived"] is True
    else:
        assert page["line"] is None, page


# ---------- (e) the columns of Corrections waiting ----------

COLUMNS = r"""
const li = rows()[0], box = (c) => li.querySelector("." + c).getBoundingClientRect();
const cols = ["corr__photo", "corr__record", "corr__by", "corr__changes", "corr__remove"];
out.display = getComputedStyle(li).display;
out.left = cols.map((c) => Math.round(box(c).left));
out.top = cols.map((c) => Math.round(box(c).top));
out.row = Math.round(li.getBoundingClientRect().width);
out.removeW = Math.round(box("corr__remove").width);
const f = (a) => footButton(a).getBoundingClientRect();
out.foot = { removeAll: [f("remove-all-corrections").left, f("remove-all-corrections").top], package: [f("download-package").left, f("download-package").top],
  send: [f("send-corrections").left, f("send-corrections").top] };
out.names = { record: text(li.querySelector(".corr__record")), by: text(li.querySelector(".corr__by")), changes: text(li.querySelector(".corr__changes")) };
"""


@pytest.mark.parametrize("width", [1280, 800, 390])
def test_a_correction_is_a_row_of_columns_that_stack_by_width(hub, fake_reader, monkeypatch, tmp_path, width):
    data = _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid(), note="brand is Polymaker")
    _add(data, "zz-gone", vendor="Bambu Lab", was="Bambu", source="cloud")
    page = _drive_corr(hub, tmp_path, COLUMNS, width=width)
    left, top = page["left"], page["top"]
    assert page["display"] == "grid"
    assert "Polymaker" in page["names"]["record"] and "2026-11-15 12:00" in page["names"]["record"]
    assert page["names"]["by"] == T("settings_corr_read_by", engine=T("settings_reader_group_local"))
    assert "Polymakr" in page["names"]["changes"] and T("recent_revision_note", note="brand is Polymaker") in page["names"]["changes"]
    foot = page["foot"]
    if width == 1280:                                  # photo, record, read by, changes, Remove side by side; Remove all at the left of the foot
        assert left == sorted(left) and len(set(left)) == 5 and max(top) - min(top) <= 2
        assert foot["removeAll"][0] < foot["package"][0] < foot["send"][0] and abs(foot["removeAll"][1] - foot["send"][1]) <= 2
    elif width == 800:                                 # Read by under the record's name
        assert left[2] == left[1] and top[2] > top[1] and left[3] > left[1]
    else:                                              # a card: the changes under the name, Remove across the foot, the buttons stacked with Send now first
        assert top[3] > top[2] > top[1] and page["removeW"] >= page["row"] * 0.9
        assert foot["send"][1] < min(foot["package"][1], foot["removeAll"][1]) and foot["package"][1] != foot["removeAll"][1]


# ---------- (f) a busy dialog ----------

def test_a_busy_dialog_ignores_cancel_and_escape_says_so_and_does_not_defer_them(hub_health, tmp_path):
    page = _page(hub_health, tmp_path, r"""
let release;
const asked = SW.ask({ kind: "confirm", title: "Slow thing", text: "It takes a while.", confirm: "Go", run: () => new Promise((r) => { release = r; }) });
const dlg = await until(() => $("dialog[open]"), "the dialog");
$('dialog[open] [data-action="confirm"]').click();
await until(() => dlg._busy, "the dialog to be busy");
const line = () => $(".dialog__result .result__text", dlg);
$('dialog[open] [data-action="cancel"]').click();            // Cancel
await until(line, "the line");
out.cancel = { text: text(line()), state: $(".dialog__result", dlg).dataset.state, open: dlg.open };
$(".dialog__result", dlg).replaceChildren(); $(".dialog__result", dlg).removeAttribute("data-state");
const notCancelled = dlg.dispatchEvent(new Event("cancel", { cancelable: true }));      // Escape asks the dialog's cancel event
await until(line, "the line again");
out.escape = { prevented: notCancelled === false, text: text(line()), open: dlg.open };
release(true);
out.answer = await asked;                                   // the press was ignored, not deferred: the dialog ended by its own answer
out.closed = !dlg.isConnected || !dlg.open;
// not busy: Cancel closes it as before
const second = SW.ask({ kind: "confirm", title: "Quick thing", text: "x", confirm: "Go" });
await until(() => $("dialog[open]"), "the second dialog");
$('dialog[open] [data-action="cancel"]').click();
out.second = await second;
""")
    assert page["cancel"] == {"text": T("common_busy_wait"), "state": "warn", "open": True} and T("common_busy_wait") == "Still working. Wait for it to finish."
    assert page["escape"] == {"prevented": True, "text": T("common_busy_wait"), "open": True}
    assert page["answer"] is True and page["closed"] is True and page["second"] is None
