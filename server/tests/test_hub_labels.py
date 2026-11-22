"""Print a label and Print labels on the Hub page (R85 5.3): a spool's detail in Spools, the shown spools in Spools, and a Recently added
record's spools. Each opens the Hub's print page (`GET labels?ids=<n,n,...>&size=62x29`, an open route, built by RC8) in a new tab. The route is
a stub here: the page's `window.open` is replaced and records what it was asked for, and every request the page makes is recorded with the
setup code it carried (none for these entries). Covered in all four looks at 1280 and 390 px: the entries appear, the right ids and size are
asked for, the route is asked for without a code (the page stays locked), a record whose spools have no number offers nothing, and nothing
scrolls sideways. The same skip rules as the page audit: no Node 22 or no browser is a skip (a failure with AUT5_REQUIRED=1)."""
from __future__ import annotations

import httpx
import pytest

from test_hub_page_audit import hub_app  # noqa: F401  (a fixture)
from test_hub_presets_ui import _drive
from test_hub_recent import recent_hub  # noqa: F401  (a fixture)
from test_hub_spools import PRELUDE, TOTAL, hub, stock, _go  # noqa: F401  (fixtures and the page driver's prelude)

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
def test_spools_offers_print_a_label_and_print_labels_in_every_look(hub, stock, tmp_path, look, width):
    page = _go(hub, tmp_path, STUB + r"""
const vw = document.documentElement.clientWidth;
out.all = { text: text($(".spools-print")), shown: shown($(".spools-print")), type: $(".spools-print").type };
out.shownIds = rows().map((r) => Number(r.dataset.id));
$(".spools-print").click();
out.afterAll = labelsAsked();
$(".coll__title", rowOf(5)).click();
await until(() => shown($("#spool-detail-5")), "the detail");
await sleep(400);
const one = $("#spool-detail-5 .spools-print-one");
out.one = { text: text(one), shown: shown(one), name: one.getAttribute("aria-label") || text(one), inDialog: !!one.closest("dialog") };
one.click();
out.afterOne = labelsAsked();
out.noCode = noCode();
out.locked = $(".chip--lock").dataset.state;
out.overflow = document.documentElement.scrollWidth > vw;
""", width=width, look=look)
    assert page["all"] == {"text": "Print labels", "shown": True, "type": "button"}
    assert len(page["afterAll"]) == 1 and page["afterAll"][0] == {"path": "/setup/api/labels", "ids": page["shownIds"], "size": "62x29", "target": "_blank", "features": "noopener"}
    assert len(page["shownIds"]) == 50 and TOTAL > 50
    assert page["one"]["text"] == "Print a label" and page["one"]["shown"]
    assert page["afterOne"][1] == {"path": "/setup/api/labels", "ids": [5], "size": "62x29", "target": "_blank", "features": "noopener"} and len(page["afterOne"]) == 2
    assert page["noCode"] and page["locked"] == "locked"       # the route is open: no code asked, none sent
    assert page["overflow"] is False


def test_print_labels_follows_what_the_list_shows(hub, stock, tmp_path):
    page = _go(hub, tmp_path, STUB + r"""
chip("low").click();
await sleep(900);
out.low = rows().map((r) => Number(r.dataset.id));
$(".spools-print").click();
out.asked = labelsAsked();
""")
    assert page["low"] and page["asked"][0]["ids"] == page["low"] and page["asked"][0]["size"] == "62x29"


def test_the_empty_list_offers_no_print_labels(hub, spoolman_url, tmp_path):
    httpx.patch(spoolman_url + "/api/v1/spool/1", json={"archived": True}).raise_for_status()
    page = _go(hub, tmp_path, r"""
out.print = !!$(".spools-print");
out.rows = rows().length;
""")
    assert page["rows"] == 0 and page["print"] is False


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
