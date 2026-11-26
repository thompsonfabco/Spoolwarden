"""Browser UI tests for the Getting started card's "I did this" control."""
from __future__ import annotations

import re

from filament_kit import firstrun, getting_started
from hub_alerts_stub import drive, hub_app, hub_stub  # noqa: F401


def _parse_count(label: str) -> tuple[int, int]:
    m = re.fullmatch(r"(\d+) of (\d+) done", label)
    assert m, f"unexpected count text: {label!r}"
    return int(m.group(1)), int(m.group(2))


def test_i_did_this_marks_step_done_and_persists_across_reload(hub_stub, monkeypatch, tmp_path):
    assert firstrun.set_hidden(False)
    monkeypatch.setattr(getting_started, "_printers", lambda: [])

    page1 = drive(hub_stub, tmp_path, r"""
await until(() => $(".start__steps .start__step"), "Getting started steps");
const openSteps = $$(".start__steps .start__step").filter((li) => li.dataset.state !== "done");
out.openIds = openSteps.map((li) => li.dataset.step);
out.doneButtons = openSteps.map((li) => {
  const btn = $(`[data-action="start-done-${li.dataset.step}"]`, li);
  return btn
    ? {
        id: li.dataset.step,
        text: text(btn),
        ghost: btn.className.includes("btn--ghost"),
        primary: btn.className.includes("btn--primary"),
      }
    : null;
});
out.beforeCount = text($(".start__count"));
unlock();
$('[data-action="start-done-printer"]').click();
await until(() => {
  const li = $('[data-step="printer"]');
  return li && li.dataset.state === "done";
}, "printer step marked done");
out.afterCount = text($(".start__count"));
out.printerBtnAfter = $$('[data-action="start-done-printer"]').length;
""")

    assert "printer" in page1["openIds"]
    assert all(
        b is not None and b["text"] == "I did this" and b["ghost"] and not b["primary"]
        for b in page1["doneButtons"]
    ), page1["doneButtons"]

    before_done, before_total = _parse_count(page1["beforeCount"])
    after_done, after_total = _parse_count(page1["afterCount"])
    assert after_total == before_total
    assert after_done == before_done + 1
    assert page1["printerBtnAfter"] == 0

    page2 = drive(hub_stub, tmp_path, r"""
await until(() => $(".start__steps .start__step"), "Getting started steps after reload");
const li = $('[data-step="printer"]');
out.printerState = li ? li.dataset.state : null;
out.printerBtn = $$('[data-action="start-done-printer"]').length;
out.count = text($(".start__count"));
""")

    assert page2["printerState"] == "done"
    assert page2["printerBtn"] == 0
    assert page2["count"] == page1["afterCount"]
