"""Checked-out spools on the Hub page's Spools section: the route (`filters`, `filter=checked_out`,
`POST spools/{id}/return`, the home change through `POST spools/{id}`) and the section in a real headless browser (the chip
"Checked out (<n>)", the rows with the place, Home and since, Return and its Undo toast, the Home line with Change home, the empty
state, and an older Hub that offers none of it), in the four looks at 1280 and 390 px.

The kit's side (the view's `checked_out` / `home` / `checked_out_since`, `inventory.return_spool`, `NotCheckedOut`, the `home` edit)
is NOT in this tree: `stubbed` puts a stand-in for each in place, over the fake Spoolman. The stand-in is the contract the page assumes:
  spool view   + checked_out: bool, home: str|null, checked_out_since: str|null   (here: a remembered place in `extra.prev_location` and its time in `extra.prev_since`, as `inventory.prev_extra` writes them; a spool in a printer slot with none is checked out with no home)
  return_spool (sm, id, rules=, dry=) -> {spool, event: {id, undo}}   NotCheckedOut for a spool that is not checked out
  edit_spool   (..., home="<place>") -> {spool, dry}   NotCheckedOut (409), SpoolEditError field "home" for an unknown place (400)
The same skip rules as the page audit: no Node 22 or no browser is a skip (a failure with AUT5_REQUIRED=1)."""
from __future__ import annotations

import json

import httpx
import pytest
from starlette.testclient import TestClient

from filament_kit import inventory, storage
from filament_kit.setup_console import spools_tab
from filament_kit.storage import MATERIALS, Location, Storage
from test_hub_page_audit import hub_app, _serve  # noqa: F401
from test_hub_presets_ui import AsHttps, _drive, T  # noqa: F401
from test_hub_spools import PRELUDE, hub  # noqa: F401
from test_setup_page import CODE

SENTENCE = "This spool is not checked out."
SINCE = "2026-11-20T10:30:00+00:00"
OLDER_KIT = pytest.mark.skipif(hasattr(inventory, "return_spool"), reason="the kit already has the return (CO1 is merged)")


class NotCheckedOut(Exception):
    pass


def _prev(s: dict) -> tuple[str, str | None]:
    return inventory.prev_entry(s)                          # the product's own reader: the extras are seeded by `inventory.prev_extra`


def _checked(s: dict) -> bool:
    return bool(_prev(s)[0]) or " – " in (s.get("location") or "")


@pytest.fixture
def stubbed(monkeypatch):
    real_view, real_edit = spools_tab.view, inventory.edit_spool

    def view(spool, low_g):
        v = real_view(spool, low_g)
        home, since = _prev(spool)
        v.update(checked_out=_checked(spool), home=home or None, checked_out_since=since if home else None)
        return v

    def return_spool(sm, spool_id, *, grams=None, rules=None, dry=False):
        before = sm.get(f"spool/{spool_id}")
        if not _checked(before):
            raise NotCheckedOut(SENTENCE)
        home = _prev(before)[0] or "Rack 1"                     # no home: the rules' place (here Rack 1)
        sm.patch(f"spool/{spool_id}", {"location": home, "extra": {**(before.get("extra") or {}), "prev_location": json.dumps("")}})
        return {"spool": sm.get(f"spool/{spool_id}"), "event": {"id": 900 + spool_id, "undo": {"possible": True}}}

    def edit_spool(sm, spool_id, **kw):
        if kw.get("home") is None:
            kw.pop("home", None)
            return real_edit(sm, spool_id, **kw)
        before = sm.get(f"spool/{spool_id}")
        if not _checked(before):
            raise NotCheckedOut(SENTENCE)
        if kw["home"] not in kw["locations"]:
            raise inventory.SpoolEditError(f"{kw['home']!r} is not one of the Hub's storage locations", "home")
        sm.patch(f"spool/{spool_id}", {"extra": {**(before.get("extra") or {}), "prev_location": json.dumps(kw["home"])}})
        return {"spool": sm.get(f"spool/{spool_id}"), "dry": False}

    monkeypatch.setattr(spools_tab, "view", view)
    monkeypatch.setattr(inventory, "return_spool", return_spool, raising=False)
    monkeypatch.setattr(inventory, "NotCheckedOut", NotCheckedOut, raising=False)
    monkeypatch.setattr(inventory, "edit_spool", edit_spool)


@pytest.fixture
def made(hub_app, spoolman_url):  # noqa: F811
    """Storage rules, and four spools: A in a slot with home Rack 1 since SINCE, B in a slot with home Rack 2 (no time), C in a slot
    with no home, D on a shelf (not checked out). The audit data's own spool is number 1."""
    storage.save(Storage([Location("Sealed stock", sealed=True), Location("Rack 1", fill=MATERIALS, materials=["PLA"]),
                          Location("Rack 2"), Location("Overflow", overflow=True, default_overflow=True)], converted=True))
    with httpx.Client() as c:
        fil = c.get(spoolman_url + "/api/v1/filament").json()[0]["id"]
        make = lambda **kw: c.post(spoolman_url + "/api/v1/spool", json={"filament_id": fil, "remaining_weight": 500, **kw}).raise_for_status().json()["id"]  # noqa: E731
        return {"A": make(location="Workshop X1 – AMS A1", extra=inventory.prev_extra("Rack 1", SINCE)),
                "B": make(location="Workshop X1 – AMS A2", extra=inventory.prev_extra("Rack 2")),
                "C": make(location="Workshop X1 – AMS A3"),
                "D": make(location="Rack 1")}


def client(hub_app):  # noqa: F811
    return TestClient(hub_app[0], base_url="http://127.0.0.1", client=("127.0.0.1", 50000))


def post(hub_app, path, body=None):  # noqa: F811
    return client(hub_app).post(f"/setup/api/{path}", json={"code": CODE, **(body or {})}, headers={"Origin": "https://127.0.0.1"})


def get(hub_app, path):  # noqa: F811
    return client(hub_app).get(f"/setup/api/{path}")


# ---------------------------------------------------------------- the route
@OLDER_KIT
def test_a_kit_without_the_return_answers_no_checked_out_filter_and_refuses_it(hub_app, made):  # noqa: F811
    r = get(hub_app, "spools").json()
    assert r["filters"] == ["sealed", "opened", "loaded", "low"] and "checked_out" not in r["filters"]
    assert get(hub_app, "spools?filter=checked_out").status_code == 400
    assert post(hub_app, f"spools/{made['A']}/return").status_code == 404


def test_the_list_names_its_filters_and_the_checked_out_filter_counts_by_total(hub_app, made, stubbed):  # noqa: F811
    all_ = get(hub_app, "spools").json()
    assert all_["filters"] == ["sealed", "opened", "loaded", "low", "checked_out"]
    by = {v["id"]: v for v in all_["spools"]}
    assert (by[made["A"]]["checked_out"], by[made["A"]]["home"], by[made["A"]]["checked_out_since"]) == (True, "Rack 1", SINCE)
    assert (by[made["B"]]["home"], by[made["B"]]["checked_out_since"]) == ("Rack 2", None)
    assert (by[made["C"]]["checked_out"], by[made["C"]]["home"]) == (True, None) and by[made["D"]]["checked_out"] is False
    one = get(hub_app, "spools?filter=checked_out&limit=1").json()
    assert one["total"] == 3 and len(one["spools"]) == 1 and one["has_more"] is True      # the chip's count is `total`
    assert [v["id"] for v in get(hub_app, "spools?filter=checked_out").json()["spools"]] == [made["A"], made["B"], made["C"]]
    assert get(hub_app, "spools?filter=nonsense").status_code == 400


def test_return_sends_a_spool_home_answers_its_place_and_event_and_refuses_twice_with_the_sentence(hub_app, made, stubbed, spoolman_url):  # noqa: F811
    r = post(hub_app, f"spools/{made['A']}/return")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["location"] == "Rack 1" and body["spool"]["location"] == "Rack 1" and body["spool"]["checked_out"] is False
    assert body["event"] == {"id": 900 + made["A"], "undo": {"possible": True}}
    assert httpx.get(f"{spoolman_url}/api/v1/spool/{made['A']}").json()["location"] == "Rack 1"
    for sid in (made["A"], made["D"]):                                                   # returned already; never checked out
        again = post(hub_app, f"spools/{sid}/return")
        assert again.status_code == 409 and again.json() == {"error": SENTENCE, "code": "not_checked_out"}
    assert post(hub_app, f"spools/{made['C']}/return").json()["location"] == "Rack 1"    # no home: the rules' place
    assert get(hub_app, "spools?filter=checked_out").json()["total"] == 1
    assert client(hub_app).post(f"/setup/api/spools/{made['B']}/return", json={}, headers={"Origin": "https://127.0.0.1"}).status_code in (401, 403, 423)   # a change needs the code


def test_the_home_changes_through_the_spool_route_and_a_refusal_keeps_the_kits_words(hub_app, made, stubbed, spoolman_url):  # noqa: F811
    r = post(hub_app, f"spools/{made['B']}", {"home": "Rack 1", "dry_run": False})
    assert r.status_code == 200 and r.json()["spool"]["home"] == "Rack 1" and r.json()["spool"]["location"] == "Workshop X1 – AMS A2"
    bad = post(hub_app, f"spools/{made['B']}", {"home": "Nowhere", "dry_run": False})
    assert bad.status_code == 400 and bad.json()["field"] == "home"
    no = post(hub_app, f"spools/{made['D']}", {"home": "Rack 2", "dry_run": False})
    assert no.status_code == 409 and no.json() == {"error": SENTENCE, "code": "not_checked_out"}


# ---------------------------------------------------------------- the page
SCRIPT = PRELUDE + r"""
const toasts = () => $$(".toast__text").map(text);
const whereOf = (id) => text($('.coll__row:first-child > .coll__cell[data-col="title"]', rowOf(id)));      // the lines sit under the spool's name
const returnBtn = (id) => $(".spools-return", rowOf(id));
const chips = () => $$(".spools-chip").map(text);
"""


def _co(hub, tmp_path, body, width=1280, look="console"):
    script = SCRIPT + f'const out = {{}};\nawait ready("{look}");\n' + body + "\nreturn out;\n"
    return _drive(hub, tmp_path, script, width=width)


@OLDER_KIT
def test_an_older_hub_shows_no_checked_out_chip_rows_or_return(hub, made, tmp_path):
    page = _co(hub, tmp_path, r"""
out.chips = chips();
out.returns = $$(".spools-return").length;
out.home = $$(".spools-home").length;
rowOf(Number(__B__)).querySelector(".coll__title").click();
await sleep(500);
out.homeline = $$(".spools-homeline").filter(shown).length;
""".replace("__B__", str(made["B"])))
    assert page["chips"] == [T("common_sealed"), T("spool_state_opened"), "In a printer", "Running low"] and page["returns"] == 0 and page["home"] == 0 and page["homeline"] == 0


def test_the_checked_out_chip_rows_return_with_undo_and_the_empty_state(hub, made, stubbed, tmp_path):
    page = _co(hub, tmp_path, r"""
await unlock();
const A = __A__, B = __B__, C = __C__, D = __D__;
out.chips = chips();
chip("checked_out").click();
await settle(() => rows().length === 3, "the three checked-out rows");
out.ids = rows().map((r) => Number(r.dataset.id));
out.count = text($(".spools-count"));
out.where = { A: whereOf(A), B: whereOf(B), C: whereOf(C) };
out.buttons = { A: text(returnBtn(A)), B: text(returnBtn(B)), C: text(returnBtn(C)) };
out.label = returnBtn(A).getAttribute("aria-label");
returnBtn(A).click();
await settle(() => toasts().some((t) => t.startsWith("Returned")), "the Returned toast");
out.toast = toasts().find((t) => t.startsWith("Returned"));
out.undo = $$(".toast .btn").map(text);
await settle(() => rows().length === 2, "the row to leave the filter");
out.afterChips = chips();
returnBtn(C).click();
await settle(() => toasts().includes("Returned to Rack 1."), "the toast of the spool with no home");
returnBtn(B).click();
await settle(() => toasts().includes("Returned to Rack 2."), "the toast of B");
await settle(() => !rows().length && $(".empty"), "the empty state");
out.empty = text($(".empty"));
out.finalChips = chips();
""".replace("__A__", str(made["A"])).replace("__B__", str(made["B"])).replace("__C__", str(made["C"])).replace("__D__", str(made["D"])))
    assert page["chips"] == [T("common_sealed"), T("spool_state_opened"), "In a printer", "Checked out (3)", "Running low", "Reorder"]    # design order: the design, Hub page
    assert page["ids"] == [made["A"], made["B"], made["C"]] and page["count"] == "3 spools"
    assert "In Workshop X1 – AMS A1" in page["where"]["A"] and "Home: Rack 1" in page["where"]["A"] and "since 2026-11-20" in page["where"]["A"]
    assert "Home: Rack 2" in page["where"]["B"] and "since" not in page["where"]["B"]
    assert "Home: not recorded" in page["where"]["C"] and "since" not in page["where"]["C"]
    assert page["buttons"] == {"A": "Return to Rack 1", "B": "Return to Rack 2", "C": "Return"} and page["label"] == "Return to Rack 1: Polymaker PolyTerra PLA Charcoal Black"
    assert page["toast"] == "Returned to Rack 1."
    assert page["afterChips"][3] == "Checked out (2)"
    assert page["empty"].startswith("No spool is checked out.") and page["finalChips"][3] == "Checked out (0)"


def test_the_home_line_and_change_home_show_only_while_checked_out(hub, made, stubbed, tmp_path):
    page = _co(hub, tmp_path, r"""
await unlock();
const B = __B__, C = __C__, D = __D__;
const open = async (id) => { $(".coll__title", rowOf(id)).click(); await settle(() => shown($("#spool-detail-" + id)), "the detail of " + id); };
const line = (id) => { const l = $("#spool-detail-" + id + " .spools-homeline"); return shown(l) ? text(l) : null; };
await open(D); out.notChecked = line(D);
await open(C); out.noHome = line(C);
await open(B); out.line = line(B);
$$("#spool-detail-" + B + " .spools-homeline .btn")[0].click();
const sheet = await until(() => $("dialog.sheet[open]"), "the Home sheet");
out.title = text($(".sheet__title", sheet));
const sel = $("select[name=home]", sheet);
out.options = [...sel.options].map((o) => o.textContent);
out.current = sel.value;
sel.value = "Rack 1"; sel.dispatchEvent(new Event("change", { bubbles: true }));
$(".sheet__foot .btn", sheet).click();
await settle(() => $(".result", sheet) && /Home is now/.test($(".result", sheet).textContent), "the result");
out.done = text($(".result", sheet));
$(".sheet__head [data-action=close]", sheet).click();
await settle(() => !$("dialog[open]"), "the sheet to close");
out.after = line(B);
out.whereAfter = whereOf(B);
""".replace("__B__", str(made["B"])).replace("__C__", str(made["C"])).replace("__D__", str(made["D"])))
    assert page["notChecked"] is None and page["noHome"].startswith("Home: not recorded") and page["line"].startswith("Home: Rack 2")
    assert "Change home" in page["line"] and page["title"] == "Home" and page["current"] == "Rack 2"
    assert any(o.startswith("Rack 1: ") and "in use" in o for o in page["options"]) and any(o.startswith("Sealed stock: ") for o in page["options"])
    assert page["done"].startswith("Home is now Rack 1.") and page["after"].startswith("Home: Rack 1") and "Home: Rack 1" in page["whereAfter"]


@pytest.mark.parametrize("look", ["console", "workbench", "palette", "panes"])
@pytest.mark.parametrize("width", [1280, 390])
def test_checked_out_rows_and_the_home_line_wrap_in_every_look_without_overflow(hub, made, stubbed, tmp_path, look, width):
    page = _co(hub, tmp_path, r"""
const vw = document.documentElement.clientWidth;
const wide = (root) => $$("*", root).filter((el) => shown(el) && el.getBoundingClientRect().right > vw + 1 && !el.closest(".sr, [aria-hidden=true], .tooltip")).slice(0, 5).map((el) => el.tagName + "." + el.className);
chip("checked_out").click();
await settle(() => rows().length === 3, "the three rows");
const cell = (id) => $('.coll__row:first-child > .coll__cell[data-col="title"]', rowOf(id));
out.list = { overflow: document.documentElement.scrollWidth > vw, wide: wide($(".spools")),
  clipped: [__A__, __B__, __C__].filter((id) => cell(id).scrollWidth > cell(id).clientWidth + 1),
  visible: [__A__, __B__, __C__].map((id) => shown(returnBtn(id)) && shown($(".spools-home", rowOf(id)))),
  width: cell(__A__).getBoundingClientRect().width };
$(".coll__title", rowOf(__B__)).click();
await settle(() => shown($("#spool-detail-" + __B__)), "the detail");
const d = $("#spool-detail-" + __B__);
out.detail = { overflow: document.documentElement.scrollWidth > vw, wide: wide(d), line: shown($(".spools-homeline", d)) };
""".replace("__A__", str(made["A"])).replace("__B__", str(made["B"])).replace("__C__", str(made["C"])), width=width, look=look)
    assert page["list"]["overflow"] is False and page["list"]["wide"] == [] and page["list"]["clipped"] == [], page["list"]
    assert page["list"]["visible"] == [True] * 3 and page["list"]["width"] >= 40, page["list"]
    assert page["detail"]["overflow"] is False and page["detail"]["wide"] == [] and page["detail"]["line"], page["detail"]


def test_return_toast_shows_printer_not_told_when_printer_notified_false(hub, made, stubbed, tmp_path):
    page = _co(hub, tmp_path, r"""
await unlock();
const origFetch = window.fetch;
window.fetch = async (u, i) => {
  const res = await origFetch(u, i);
  if (String(u).includes("/return")) {
    const data = await res.json();
    data.printer_notified = false;
    return new Response(JSON.stringify(data), { status: res.status, headers: { "Content-Type": "application/json" } });
  }
  return res;
};
chip("checked_out").click();
await settle(() => !!returnBtn(__A__), "row A");
returnBtn(__A__).click();
await settle(() => toasts().some((t) => t.includes("The printer could not be told")), "not-told toast");
out.toast = toasts().find((t) => t.includes("The printer could not be told"));
""".replace("__A__", str(made["A"])))
    assert "The printer could not be told to clear its slot." in page["toast"]
