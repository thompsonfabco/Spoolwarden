"""Print a label and Print labels on the Hub page (R85 5.3): a spool's detail in Spools, the selected spools in Spools (03 16.2, 02 6.1), and a
Recently added record's spools. Each opens the Hub's print page (`GET labels?ids=<n,n,...>&size=62x29`, an open route, built by RC8) in a new tab. The route is
a stub here: the page's `window.open` is replaced and records what it was asked for, and every request the page makes is recorded with the
setup code it carried (none for these entries). Covered in all four looks at 1280 and 390 px: the selection (a checkbox per row, the header
checkbox, the bar with "<n> selected", Print labels sending exactly the selected ids, Clear selection, Shift+click and Escape, the selection
kept by Show more and dropped by a new filter, the old Print labels button of the list gone), Print a label among the spool's Actions, the
right ids and size asked for, the route asked for without a code (the page stays locked), a record whose spools have no number offers
nothing, and nothing scrolls sideways. The same skip rules as the page audit: no Node 22 or no browser is a skip (a failure with AUT5_REQUIRED=1)."""
from __future__ import annotations

import httpx
import pytest

from test_hub_page_audit import hub_app  # noqa: F401  (a fixture)
from test_hub_presets_ui import _drive, T
from test_hub_recent import recent_hub  # noqa: F401  (a fixture)
from test_hub_spools import PRELUDE, hub, stock, _go  # noqa: F401  (fixtures and the page driver's prelude)

# the stub: window.open records its call (and opens nothing), fetch records what the page asked for and the code it carried
STUB = r"""
const OPENED = [], ASKED = [];
window.open = (url, target, features) => { OPENED.push({ url: String(url), target, features }); return null; };
{
  const inner = window.fetch.bind(window);
  window.fetch = (url, init) => { ASKED.push({ url: String(url), code: init && init.headers && init.headers["X-Setup-Code"] != null ? init.headers["X-Setup-Code"] : null }); return inner(url, init); };
}
const labelsAsked = () => OPENED.map((o) => { const u = new URL(o.url, location.href); return { path: u.pathname, ids: u.searchParams.get("ids").split(",").map(Number), size: u.searchParams.get("size"), target: o.target, features: o.features }; });
const noCode = () => ASKED.filter((a) => a.url.includes("labels")).length === 0 && ASKED.every((a) => a.code == null);
"""


@pytest.mark.parametrize("look", ["console", "workbench", "palette", "panes"])
@pytest.mark.parametrize("width", [1280, 390])
def test_the_selection_prints_exactly_the_selected_spools_in_every_look(hub, stock, tmp_path, look, width):
    page = _go(hub, tmp_path, STUB + r"""
const vw = document.documentElement.clientWidth;
const pick = (id) => $(".spools-pick", rowOf(id));
const click = (el, shift) => el.dispatchEvent(new MouseEvent("click", { bubbles: true, cancelable: true, shiftKey: !!shift }));
const bar = () => $(".spools-selbar");
const state = () => ({ bar: shown(bar()), count: text($(".spools-selcount")), all: $(".spools-pickall").checked, mixed: $(".spools-pickall").indeterminate,
  on: rows().filter((r) => $(".spools-pick", r).checked).map((r) => Number(r.dataset.id)), marked: rows().filter((r) => r.dataset.selected === "true").length });
const box = (el) => { const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height) }; };
const label = (el) => text(el.closest("label").querySelector(".check__label"));
out.ids = rows().map((r) => Number(r.dataset.id));
// the old button of the list is gone, and with nothing selected there is no bar
out.old = { head: $$(".spools-head .btn").map(text), bar: shown(bar()) };
out.row = { shown: shown(pick(3)), label: label(pick(3)), title: text($(".coll__title", rowOf(3))), box: box(pick(3)), titleShown: shown($(".coll__title", rowOf(3))), first: rowOf(3).querySelector(".coll__row > .coll__cell").dataset.col };
out.head = { shown: shown($(".spools-pickall")), label: label($(".spools-pickall")), box: box($(".spools-pickall")) };
// three checkboxes: the bar, the count, Print labels with exactly those ids
click(pick(2)); click(pick(4)); click(pick(6));
out.three = state();
out.barShown = { print: shown($(".spools-selbar .spools-print")), printText: text($(".spools-selbar .spools-print")), clear: shown($(".spools-clearsel")), clearText: text($(".spools-clearsel")) };
if (vw <= 600) {
  const tab = parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--tab")), b = bar().getBoundingClientRect();
  out.barAboveNav = tab > 0 && b.bottom <= innerHeight - tab + 1 && b.top >= 0 && getComputedStyle(bar()).position === "fixed";
}
out.group = bar().getAttribute("aria-label");
$(".spools-selbar .spools-print").click();
out.printed = labelsAsked();
// Clear selection
$(".spools-clearsel").click();
out.cleared = state();
out.focus = document.activeElement === $(".spools-pickall");
// the header checkbox: all rows of the page, mixed when some, none again
click($(".spools-pickall"));
out.allOn = state();
OPENED.length = 0;
$(".spools-selbar .spools-print").click();
out.printedAll = labelsAsked();
click(pick(3));
out.mixed = state();
click($(".spools-pickall"));
out.allAgain = state();
click($(".spools-pickall"));
out.none = state();
// Shift+click selects a range; Escape clears
click(pick(2)); click(pick(5), true);
out.range = state().on;
pick(5).focus();
pick(5).dispatchEvent(new KeyboardEvent("keydown", { key: "Escape", bubbles: true, cancelable: true }));
out.escaped = state();
out.overflow = document.documentElement.scrollWidth > vw;
out.noCode = noCode();
out.locked = $(".chip--lock").dataset.state;
""", width=width, look=look)
    assert "Print labels" not in page["old"]["head"] and page["old"]["bar"] is False, page["old"]    # the list's own Print labels button is gone
    assert page["row"]["shown"] and page["row"]["label"] == "Select " + page["row"]["title"] and page["row"]["first"] == "select"
    assert page["row"]["box"]["w"] >= 16 and page["row"]["box"]["h"] >= 16 and page["row"]["titleShown"], page["row"]   # the column is not squeezed
    assert page["head"]["shown"] and page["head"]["label"] == "Select all shown" and page["head"]["box"]["w"] >= 16, page["head"]
    assert page["three"] == {"bar": True, "count": "3 selected", "all": False, "mixed": True, "on": [2, 4, 6], "marked": 3}, page["three"]
    assert page["barShown"] == {"print": True, "printText": "Print labels", "clear": True, "clearText": "Clear selection"}
    assert page["group"] == "Selected spools" and page.get("barAboveNav", True) is True
    assert page["printed"] == [{"path": "/setup/api/labels", "ids": [2, 4, 6], "size": "62x29", "target": "_blank", "features": "noopener"}]
    assert page["cleared"] == {"bar": False, "count": "0 selected", "all": False, "mixed": False, "on": [], "marked": 0} and page["focus"]
    assert len(page["ids"]) == 50 and page["allOn"]["on"] == page["ids"] and page["allOn"]["all"] and page["allOn"]["count"] == "50 selected" and page["allOn"]["marked"] == 50
    assert page["printedAll"] == [{"path": "/setup/api/labels", "ids": page["ids"], "size": "62x29", "target": "_blank", "features": "noopener"}]
    assert page["mixed"]["on"] == [i for i in page["ids"] if i != 3] and page["mixed"]["mixed"] and not page["mixed"]["all"] and page["mixed"]["count"] == "49 selected"
    assert page["allAgain"]["all"] and page["allAgain"]["count"] == "50 selected"          # from mixed, the header selects the rest
    assert page["none"]["on"] == [] and page["none"]["bar"] is False                       # pressed again with all on: clears them
    assert page["range"] == [2, 3, 4, 5]                                                   # Shift+click: from the last one pressed
    assert page["escaped"]["on"] == [] and page["escaped"]["bar"] is False
    assert page["noCode"] and page["locked"] == "locked" and page["overflow"] is False     # open route: no code asked, none sent


@pytest.mark.parametrize("look", ["console", "workbench", "palette", "panes"])
@pytest.mark.parametrize("width", [1280, 390])
def test_the_selection_survives_show_more_and_a_new_filter_drops_it(hub, stock, tmp_path, look, width):
    page = _go(hub, tmp_path, STUB + r"""
const pick = (id) => $(".spools-pick", rowOf(id));
const count = () => text($(".spools-selcount"));
pick(2).click(); pick(3).click();
const more = $$(".spools-foot .btn").find((b) => text(b) === "Show more");
more.click();
await settle(() => rows().length > 50, "more rows");
out.afterMore = { count: count(), bar: shown($(".spools-selbar")), on: rows().filter((r) => $(".spools-pick", r).checked).map((r) => Number(r.dataset.id)), header: $(".spools-pickall").checked };
const last = rows().length - 1;
const lastId = Number(rows()[last].dataset.id);
$(".spools-pick", rows()[last]).click();
out.afterLast = { count: count(), header: $(".spools-pickall").checked };
$(".spools-selbar .spools-print").click();
out.printed = labelsAsked();
out.lastId = lastId;
chip("low").click();
await sleep(900);
out.afterFilter = { rows: rows().map((r) => Number(r.dataset.id)), bar: shown($(".spools-selbar")), on: rows().filter((r) => $(".spools-pick", r).checked).length, header: $(".spools-pickall").checked };
// the filtered list: the header checkbox and Print labels give exactly the rows shown
$(".spools-pickall").click();
$(".spools-selbar .spools-print").click();
out.filtered = labelsAsked()[1];
""", width=width, look=look)
    assert page["afterMore"] == {"count": "2 selected", "bar": True, "on": [2, 3], "header": False}, page["afterMore"]
    assert page["afterLast"]["count"] == "3 selected" and page["afterLast"]["header"] is False
    assert page["printed"][0]["ids"] == [2, 3, page["lastId"]] and page["printed"][0]["size"] == "62x29"
    assert page["afterFilter"]["rows"] == [5, 6] and page["afterFilter"]["bar"] is False and page["afterFilter"]["on"] == 0 and page["afterFilter"]["header"] is False
    assert page["filtered"]["ids"] == [5, 6] and page["filtered"]["size"] == "62x29"


@pytest.mark.parametrize("look", ["console", "workbench", "palette", "panes"])
@pytest.mark.parametrize("width", [1280, 390])
def test_print_a_label_is_among_the_spools_actions_in_every_look(hub, stock, tmp_path, look, width):
    page = _go(hub, tmp_path, STUB + r"""
const vw = document.documentElement.clientWidth;
$(".coll__title", rowOf(5)).click();
await until(() => shown($("#spool-detail-5")), "the detail");
await sleep(400);
const d = $("#spool-detail-5");
const one = $(".spools-print-one", d);
out.order = $$(".detail__section .btn", d).map(text);
out.apart = !one.closest(".detail__remove") && $(".detail__remove .btn--danger", d) !== null;
out.labelrow = !!$(".spools-labelrow", d);
out.same = one.closest(".detail__section") === $(".btn--primary", d).closest(".detail__section");
out.one = { text: text(one), shown: shown(one), name: one.getAttribute("aria-label") || text(one), inDialog: !!one.closest("dialog") };
one.click();
out.asked = labelsAsked();
out.noCode = noCode();
out.locked = $(".chip--lock").dataset.state;
out.overflow = document.documentElement.scrollWidth > vw;
""", width=width, look=look)
    assert page["order"] == [T("spool_update_weight"), T("spool_move"), "Link the last tapped tag", "Print a label", T("spool_mark_empty")], page["order"]
    assert page["apart"] and page["same"] and page["labelrow"] is False
    assert page["one"]["text"] == "Print a label" and page["one"]["shown"] and page["one"]["name"].startswith("Print a label: ")
    assert page["asked"] == [{"path": "/setup/api/labels", "ids": [5], "size": "62x29", "target": "_blank", "features": "noopener"}]
    assert page["noCode"] and page["locked"] == "locked" and page["overflow"] is False


def test_the_empty_list_offers_no_selection(hub, spoolman_url, tmp_path):
    httpx.patch(spoolman_url + "/api/v1/spool/1", json={"archived": True}).raise_for_status()
    page = _go(hub, tmp_path, r"""
out.print = shown($(".spools-print"));
out.bar = shown($(".spools-selbar"));
out.boxes = $$(".spools-pick, .spools-pickall").length;
out.rows = rows().length;
""")
    assert page["rows"] == 0 and page["print"] is False and page["bar"] is False and page["boxes"] == 0


@pytest.mark.parametrize("look", ["console", "workbench", "palette", "panes"])
@pytest.mark.parametrize("width", [1280, 390])
def test_a_recently_added_record_offers_print_labels_for_its_spools_in_every_look(recent_hub, tmp_path, look, width):
    url, ids = recent_hub
    spools = ids["spools"]
    page = _drive(url, tmp_path, PRELUDE + STUB + f"""
const out = {{}};
document.documentElement.setAttribute("data-look", {look!r});
const vw = document.documentElement.clientWidth;
for (const name of ["cloud", "hand"]) {{
  location.hash = "#recent/" + {ids!r}[name];
  await until(() => $(".recent__bar"), "the record " + name);
  await sleep(500);
  const b = $(".recent__print");
  OPENED.length = 0;
  b.click();
  out[name] = {{ text: text(b), shown: shown(b), asked: labelsAsked(), overflow: document.documentElement.scrollWidth > vw }};
}}
out.noCode = noCode();
out.locked = $(".chip--lock").dataset.state;
// a record whose spools have no number yet: nothing to print
const inner = window.fetch;
window.fetch = async (u, i) => {{
  const r = await inner(u, i);
  if (!String(u).includes("intake/history")) return r;
  const text = (await r.text()).replace(/"spool_id":\\s*\\d+/g, '"spool_id": null');
  return new Response(text, {{ status: r.status, headers: r.headers }});
}};
location.hash = "#recent";
await sleep(300);
location.hash = "#recent/" + {ids!r}["long"];
await until(() => $(".recent__bar"), "the record without numbers");
await sleep(500);
out.noNumber = {{ button: !!$(".recent__print"), title: text($(".detail__title")) }};
return out;
""", width=width)
    assert page["cloud"]["text"] == "Print labels" and page["cloud"]["shown"] and len(spools["cloud"]) == 2
    assert page["cloud"]["asked"] == [{"path": "/setup/api/labels", "ids": spools["cloud"], "size": "62x29", "target": "_blank", "features": "noopener"}]
    assert page["hand"]["asked"] == [{"path": "/setup/api/labels", "ids": spools["hand"], "size": "62x29", "target": "_blank", "features": "noopener"}]
    assert page["cloud"]["overflow"] is False and page["hand"]["overflow"] is False
    assert page["noCode"] and page["locked"] == "locked"
    assert page["noNumber"]["button"] is False and page["noNumber"]["title"]
