"""Share corrections on the Hub page (step B1), in a real headless browser against the real routes of the kit and the fake
local reader (fakes/local_reader_fake.py, which has the abilities of a 1.1 reader and no others): the switch and its paragraph
in Settings > Label Reader with each of its states, Corrections waiting (the list, Remove, Remove all, Download the package,
Send now, a correction the reader did not take, a refusal that shows its code), the question when switching off while
corrections wait, the notes on Recently added and the line of the Features drawer. Jobs are started by the Hub's helper; here
they run in a thread of the test: the one that sends runs the command line's own `label-reader corrections send`. The same skip
rules as the page audit: no Node 22 or no browser is a skip (a failure with AUT5_REQUIRED=1)."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import threading
import time

import pytest
from fakes import local_reader_fake as lrf

from filament_kit import cli, config, intake, intake_history, local_intake_install, local_reader, reader_corrections, setup_helper, share_corrections, share_queue
from filament_kit.spoolman import Spoolman

from test_hub_page_audit import ALLOW, _run, hub_app, hub_change, ITEM  # noqa: F401
from test_hub_presets_ui import _drive, T, hub  # noqa: F401
from test_hub_recent import _seed
from test_hub_settings import PRELUDE

JPEG = b"\xff\xd8\xff\xe0" + b"x" * 100
SOURCE = {"vendor": "Polymaker", "name": "PolyTerra PLA Charcoal Black"}


# ---------- the Hub with a reader ----------

def _reader(fake_reader, monkeypatch, *, saved=True, on=True, installed=True):
    """Label Reader: On this Hub pointing at the fake reader (a 1.1 reader with its queue), Share corrections as asked."""
    url, data = fake_reader
    cfg = config.load()
    cfg["modules"]["local_intake"] = on
    cfg["local_intake"] = {"url": url}
    cfg["label_reader"] = {**(cfg.get("label_reader") or {}), "share_corrections": saved}
    config.save(cfg)
    data["opt_in"] = saved
    lrf.feedback_health(data)
    monkeypatch.setattr(local_reader, "CONNECT_TIMEOUT", 0.5)
    monkeypatch.setattr(local_intake_install, "installed", lambda: {"version": "0.2.2", "tier": "cpu"} if installed else None)
    return data


def _add(data, record, vendor="Polymaker", was="Polymakr", note="", source="local"):
    """One waiting correction as the reader holds it (`record` is the reader's own pointer; the Hub adds `photos`)."""
    n = data["next_id"]
    data["next_id"] += 1
    data["queue"].append({"id": f"fb-{n}", "created_at": "2026-11-15T12:00:00Z", "predicted": {"vendor": was}, "corrected": {"vendor": vendor},
                          "note": note, "photos": 1, "jpegs": [JPEG], "thumbnail_b64": base64.b64encode(JPEG[:16]).decode(),
                          "record": record, "source": source})
    return f"fb-{n}"


def _rid() -> str:
    """The record the Hub page's example data keeps in Recently added."""
    return intake_history._ids_newest_first()[0]


def _record(spoolman_url, *, photos=1):
    """A record in Recently added whose spools are in the fake Spoolman (a save changes them), read by the reader on this Hub."""
    item = {"count": 1, "sealed": False, "density": 1.24, "tare": 200, "net": 1000, "nozzle": 220, "bed": 55, "material": "PLA",
            "vendor": "Polymaker", "name": "PolyTerra PLA Charcoal Black", "color_hex": "2B2B2E"}
    read = {"item": {"confidence_levels": {k: "high" for k in item if k not in ("count", "sealed")}, "confidence": 0.9},
            "engine": {"reader": "local", "model": "label-reader", "tier": "full"}, "confidence_rated": True} if photos else None
    return _seed(Spoolman(spoolman_url), item, source="phone", photos=photos, read=read)[0]


def _jobs(monkeypatch, data):
    """The Hub's helper starts jobs: here each is a thread that writes the log as a unit would. The reader's own state follows
    `local_intake_share` (it restarts with the value the Hub saved); `corrections_send` runs the command line's own send."""
    started: list[str] = []
    monkeypatch.setattr(cli, "_require_root", lambda: True)

    def start(name, why="", checked=False):
        started.append(name)

        def job():
            log = setup_helper.job_log()
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(setup_helper.header(name) + "\n")
            time.sleep(0.5)
            out = io.StringIO()
            code = 0
            if name == "local_intake_share":
                data["opt_in"] = share_corrections.saved(config.load())
                lrf.feedback_health(data)            # the restarted reader tells its new state
            elif name == "corrections_send":
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                    code = cli.cmd_local_intake_corrections_send()
            with log.open("a") as f:
                f.write(out.getvalue() + f"==> finished (exit {code})\n")
        threading.Thread(target=job, daemon=True).start()
    monkeypatch.setattr(setup_helper, "_start_job", start)
    return started


HELPERS = r"""
const grp = () => $("#set-label-reader");
const sw = () => $('#set-label-reader input[role="switch"]');
const line = () => $("#set-label-reader .settings__corr-line");
const card = () => $("#set-corrections");
const rows = () => [...document.querySelectorAll("#set-corrections .revisions__item")];
const names = (root) => [...root.querySelectorAll("button, a")].map((b) => b.getAttribute("aria-label") || text(b));
const rowInfo = (li) => ({ text: text(li), link: li.querySelector("a") && li.querySelector("a").getAttribute("href"), actions: names(li) });
const cardResult = () => $("#set-corrections .result .result__text");
const groupResult = () => $("#set-label-reader .group__foot .result .result__text");
const foot = () => $("#set-corrections .corr__body > .actions");
const footButton = (action) => $(`#set-corrections [data-action="${action}"]`);
const settle = async () => {
  await until(() => sw() && (!sw().disabled || line().children.length), "the switch");
  await until(() => card() && (card().hidden || $(".revisions__list, .empty, .error-state", card())), "the card");
};
const ready = async () => { await loaded(); location.hash = "#settings/label-reader"; await unlock(); await settle(); };
const dialog = () => $("dialog[open]");
const dialogInfo = (dlg) => ({ title: text($(".dialog__title", dlg)), body: text($(".dialog__text", dlg)), buttons: [...dlg.querySelectorAll(".dialog__foot .btn")].map(text) });
const press = (action) => $(`dialog[open] [data-action="${action}"]`).click();
const closed = async () => { await until(() => !dialog(), "the dialog to close"); await sleep(200); };      // its close event is a task later
const done = (box, before) => until(() => { const t = text(box()); return t && t !== before && !$("#set-corrections .result[data-state=run]") ? t : null; }, "a result");
"""


def _drive_corr(hub, tmp_path, body, width=1280, *, lock=False):
    """The Hub page on Settings > Label Reader; `lock`: not unlocked (the lock asks in place)."""
    helpers = HELPERS if not lock else HELPERS.replace("await unlock(); ", "")
    script = PRELUDE + helpers + "\nawait ready();\nconst out = {};\n" + body + "\nreturn out;\n"
    return _drive(hub, tmp_path, script, width=width)


# ---------- the switch (10.5.1) ----------

def test_the_switch_has_its_paragraph_as_its_description_and_nothing_under_it_when_all_is_well(hub, fake_reader, monkeypatch, tmp_path):
    _reader(fake_reader, monkeypatch, saved=True)
    page = _drive_corr(hub, tmp_path, r"""
const c = sw();
out.sw = { checked: c.checked, disabled: c.disabled, role: c.getAttribute("role"), name: c.name };
out.label = text($(".check__label", c.closest("label")));
out.heading = text([...grp().querySelectorAll(".settings__heading")].pop());
const desc = $(".check__desc", c.closest("label"));
out.para = text(desc);
out.described = c.getAttribute("aria-describedby") === desc.id && shown(desc);
out.line = line().children.length;
// off, the paragraph is still there
c.click();
out.stillThere = shown(desc);
""")
    assert page["sw"] == {"checked": True, "disabled": False, "role": "switch", "name": "label_reader.share_corrections"}
    assert page["label"] == T("settings_corr_label") and page["heading"] == T("settings_corr_heading")
    assert page["para"] == T("settings_corr_text") and page["described"] is True and page["line"] == 0 and page["stillThere"] is True


@pytest.mark.parametrize("case, line_key, link", [("off", "settings_corr_needs_reader", "#features"),
                                                   ("not installed", "settings_corr_needs_reader", "#features")])
def test_the_switch_is_disabled_with_its_line_while_label_reader_on_this_hub_is_off_or_not_installed(hub, fake_reader, monkeypatch, tmp_path, case, line_key, link):
    _reader(fake_reader, monkeypatch, saved=False, on=case != "off", installed=case != "not installed")
    page = _drive_corr(hub, tmp_path, r"""
out.sw = { checked: sw().checked, disabled: sw().disabled };
out.line = text(line());
out.links = [...line().querySelectorAll("a")].map((a) => [text(a), a.getAttribute("href")]);
""")
    assert page["sw"] == {"checked": False, "disabled": True}
    assert T(line_key) in page["line"] and page["links"] == [[T("nav_features"), link]]


def test_the_switch_is_disabled_for_a_reader_too_old_and_for_one_that_does_not_answer_and_enabled_when_it_is_set_differently(hub, fake_reader, monkeypatch, tmp_path):
    url, data = fake_reader
    _reader(fake_reader, monkeypatch, saved=False)
    # a reader without the queue (API 1.0)
    data["health"] = {**data["health"], "api_version": "1.0"}
    data["health"].pop("capabilities", None)
    old = _drive_corr(hub, tmp_path, r"""
out.sw = { disabled: sw().disabled };
out.line = text(line());
out.links = [...line().querySelectorAll("a")].map((a) => [text(a), a.getAttribute("href")]);
""")
    assert old["sw"] == {"disabled": True} and T("settings_corr_too_old") in old["line"] and old["links"] == [[T("nav_updates"), "#updates"]]
    # a reader that does not answer, the saved value shown
    cfg = config.load()
    cfg["label_reader"]["share_corrections"] = True
    cfg["local_intake"] = {"url": "http://127.0.0.1:9"}
    config.save(cfg)
    silent = _drive_corr(hub, tmp_path, r"""
out.sw = { checked: sw().checked, disabled: sw().disabled };
out.line = text(line());
""")
    assert silent["sw"] == {"checked": True, "disabled": True} and T("settings_corr_no_answer") in silent["line"]
    # a reader set differently from the Hub: enabled, with the line
    cfg["local_intake"] = {"url": url}
    cfg["label_reader"]["share_corrections"] = False
    config.save(cfg)
    data["opt_in"] = True
    lrf.feedback_health(data)
    differs = _drive_corr(hub, tmp_path, r"""
await sleep(800);
out.sw = { checked: sw().checked, disabled: sw().disabled };
out.line = text(line());
""")
    assert differs["sw"] == {"checked": False, "disabled": False} and T("settings_corr_differs") in differs["line"], differs


def test_save_sets_the_reader_again_when_it_is_set_differently_with_nothing_else_changed(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=False)
    data["opt_in"] = True
    lrf.feedback_health(data)
    started = _jobs(monkeypatch, data)
    page = _drive_corr(hub, tmp_path, r"""
await until(() => line().children.length, "the line");
$("#set-label-reader [type=submit]").click();
out.result = await until(() => { const t = text(groupResult()); return t && t.includes("restarted") ? t : null; }, "the result");
await until(() => !line().children.length, "the line to go");
out.after = { checked: sw().checked, line: line().children.length };
""")
    assert started == ["local_intake_share"] and data["opt_in"] is False
    assert page["result"] == T("form_saved_fields", fields=T("settings_corr_heading")) + " " + T("settings_corr_restarted")
    assert page["after"] == {"checked": False, "line": 0}


def test_switching_on_saves_the_setting_and_the_reader_restarts_in_a_job(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=False)
    started = _jobs(monkeypatch, data)
    page = _drive_corr(hub, tmp_path, r"""
sw().click();
await until(() => grp().dataset.state === "dirty", "the dirty state");
$("#set-label-reader [type=submit]").click();
out.result = await until(() => { const t = text(groupResult()); return t && t.includes("restarted") ? t : null; }, "the result");
await until(() => $("#set-label-reader") && sw() && sw().checked, "the group read again");
out.after = { checked: sw().checked, card: !card().hidden };
""")
    assert started == ["local_intake_share"] and config.load()["label_reader"]["share_corrections"] is True and data["opt_in"] is True
    assert page["result"] == T("form_saved_fields", fields=T("settings_corr_heading")) + " " + T("settings_corr_restarted")
    assert page["after"] == {"checked": True, "card": True}      # on: the card is there, with nothing waiting yet


def test_switching_off_with_corrections_waiting_asks_and_cancel_saves_nothing_then_keep_them_saves_and_keeps_them(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid())
    _add(data, "zz-gone")
    started = _jobs(monkeypatch, data)
    page = _drive_corr(hub, tmp_path, r"""
sw().click();
$("#set-label-reader [type=submit]").click();
const dlg = await until(dialog, "the question");
out.ask = dialogInfo(dlg);
out.focus = document.activeElement.dataset.action;
press("cancel"); await closed();
out.cancelled = { dirty: grp().dataset.state, checked: sw().checked, result: text(groupResult()) };
$("#set-label-reader [type=submit]").click();
await until(dialog, "the question again");
press("confirm"); await closed();
out.result = await until(() => { const t = text(groupResult()); return t && t.includes("restarted") ? t : null; }, "the result");
await until(() => sw() && !sw().checked && card(), "the group read again");
await until(() => !card().hidden, "the card");
out.after = { checked: sw().checked, rows: rows().length, intro: text($("#set-corrections .corr__body > p")) };
""")
    assert page["ask"] == {"title": T("settings_corr_stop_title"), "body": T("settings_corr_stop_body", count=2),
                           "buttons": [T("common_cancel"), T("settings_corr_keep"), T("settings_corr_delete")]}
    assert page["focus"] == "confirm"                                   # Keep them is the default
    assert page["cancelled"] == {"dirty": "dirty", "checked": False, "result": None}
    assert config.load()["label_reader"]["share_corrections"] is False and started == ["local_intake_share"]
    assert len(data["queue"]) == 2                                       # kept
    assert page["result"] == T("form_saved_fields", fields=T("settings_corr_heading")) + " " + T("settings_corr_restarted")
    # off, with corrections left: the card stays and says so
    assert page["after"] == {"checked": False, "rows": 2, "intro": T("settings_corr_intro_off")}


def test_delete_them_deletes_the_corrections_waiting_and_saves_the_group(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid())
    started = _jobs(monkeypatch, data)
    page = _drive_corr(hub, tmp_path, r"""
sw().click();
$("#set-label-reader [type=submit]").click();
const dlg = await until(dialog, "the question");
out.ask = dialogInfo(dlg);
press("delete-corrections"); await closed();
out.result = await until(() => { const t = text(groupResult()); return t && t.includes("restarted") ? t : null; }, "the result");
await until(() => card() && card().hidden, "the card to go");
out.card = card().hidden;
""")
    assert page["ask"]["title"] == T("settings_corr_stop_title") and page["ask"]["body"] == T("settings_corr_stop_body", count=1)
    assert data["queue"] == [] and config.load()["label_reader"]["share_corrections"] is False and started == ["local_intake_share"]
    assert page["result"] == f"{T('form_saved_fields', fields=T('settings_corr_heading'))} {T('settings_corr_deleted', count=1)} {T('settings_corr_restarted')}"
    assert page["card"] is True


def test_switching_off_with_nothing_waiting_asks_nothing(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=True)
    started = _jobs(monkeypatch, data)
    page = _drive_corr(hub, tmp_path, r"""
sw().click();
$("#set-label-reader [type=submit]").click();
out.result = await until(() => { const t = text(groupResult()); return t && t.includes("restarted") ? t : dialog() ? "dialog" : null; }, "the result");
""")
    assert page["result"] == T("form_saved_fields", fields=T("settings_corr_heading")) + " " + T("settings_corr_restarted") and started == ["local_intake_share"]


# ---------- Corrections waiting (10.5.2) ----------

def test_the_card_lists_each_correction_with_its_record_its_changes_and_its_buttons_under_the_group(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=True)
    rid = _rid()
    _add(data, rid, note="brand is Polymaker")
    _add(data, "zz-gone", vendor="Bambu Lab", was="Bambu", source="cloud")
    page = _drive_corr(hub, tmp_path, r"""
out.title = text($("#set-corrections h2"));
out.count = text($("#set-corrections .corr__count"));
out.intro = text($("#set-corrections .corr__body > p"));
out.rows = rows().map(rowInfo);
out.foot = names(foot());
out.footDisabled = [...foot().querySelectorAll("button")].map((b) => b.getAttribute("aria-disabled"));
out.hint = text($("#set-corrections .field__hint"));
out.under = grp().nextElementSibling === card() && !grp().contains(card());
const thumb = rows()[0].querySelector("button.thumb");
out.thumb = thumb && thumb.getAttribute("aria-label");
out.goneThumb = !!rows()[1].querySelector("button.thumb");
thumb.click();
const dlg = await until(dialog, "the photo viewer");
out.viewer = text($(".dialog__title", dlg));
""")
    assert page["title"] == T("settings_corr_title") and page["count"] == T("settings_corr_count", count=2, size="1 KB")
    assert page["intro"] == T("settings_corr_intro") and page["hint"] == T("settings_corr_package_hint") and page["under"] is True
    first, gone = page["rows"]
    assert "Polymaker" in first["text"] and "Polymakr" in first["text"] and T("settings_corr_read_by", engine=T("settings_reader_group_local")) in first["text"]
    assert T("recent_revision_note", note="brand is Polymaker") in first["text"] and "2026-11-15 12:00" in first["text"]
    assert first["link"] == f"#recent/{rid}" and T("settings_corr_remove_named", name="Polymaker") in first["actions"]
    assert T("settings_corr_record_gone") in gone["text"] and T("settings_corr_photos_gone") in gone["text"] and gone["link"] is None
    assert T("settings_corr_read_by", engine=T("settings_reader_group_cloud")) in gone["text"] and T("settings_corr_remove_named", name="Bambu Lab") in gone["actions"]
    assert page["foot"] == [T("settings_corr_remove_all"), T("settings_corr_package"), T("settings_corr_send")] and page["footDisabled"] == [None, None, None]
    assert page["thumb"] == T("photo_enlarge", number=1, total=1) and page["goneThumb"] is False
    assert page["viewer"] == T("photo_position", number=1, total=1)


def test_the_card_is_empty_with_nothing_waiting_and_download_and_send_say_why_they_wait(hub, fake_reader, monkeypatch, tmp_path):
    _reader(fake_reader, monkeypatch, saved=True)
    page = _drive_corr(hub, tmp_path, r"""
out.empty = text($("#set-corrections .empty"));
out.count = text($("#set-corrections .corr__count"));
out.foot = [...foot().querySelectorAll("button")].map((b) => [text(b), b.getAttribute("aria-disabled")]);
out.reasons = [...foot().querySelectorAll(".btn__reason")].map(text);
""")
    assert page["empty"] == T("settings_corr_empty_title") + T("settings_corr_empty_text") and page["count"] == ""
    assert page["foot"] == [[T("settings_corr_package"), "true"], [T("settings_corr_send"), "true"]]
    assert page["reasons"] == [T("settings_corr_nothing")] * 2


def test_the_card_is_not_there_with_sharing_off_and_nothing_left(hub, fake_reader, monkeypatch, tmp_path):
    _reader(fake_reader, monkeypatch, saved=False)
    page = _drive_corr(hub, tmp_path, r"""
await sleep(500);
out.hidden = !card() || card().hidden || !shown(card());
""")
    assert page["hidden"] is True


def test_remove_takes_one_out_at_once_with_no_question_and_says_the_record_is_unchanged(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=True)
    a = _add(data, _rid())
    b = _add(data, "zz-gone", vendor="Bambu Lab")
    page = _drive_corr(hub, tmp_path, r"""
rows()[0].querySelector("button:not(.thumb)").click();
out.result = await done(cardResult, null);
out.asked = !!dialog();
out.rows = rows().length;
out.count = text($("#set-corrections .corr__count"));
""")
    assert page["result"] == T("settings_corr_removed") and page["asked"] is False and page["rows"] == 1
    assert [e["id"] for e in data["queue"]] == [b] and a not in [e["id"] for e in data["queue"]]
    assert page["count"] == T("settings_corr_count", count=1, size="1 KB")


def test_remove_all_asks_first_and_cancel_removes_nothing_then_it_removes_all_and_says_nothing_was_sent(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid())
    _add(data, "zz-gone")
    page = _drive_corr(hub, tmp_path, r"""
footButton("remove-all-corrections").click();
const dlg = await until(dialog, "the question");
out.ask = dialogInfo(dlg);
out.danger = !!$(".btn--danger", $(".dialog__foot", dlg));
press("cancel"); await closed();
out.kept = rows().length;
footButton("remove-all-corrections").click();
await until(dialog, "the question again");
press("confirm"); await closed();
out.result = await done(cardResult, null);
out.after = { rows: rows().length, empty: !!$("#set-corrections .empty"), remove: !!footButton("remove-all-corrections") };
""")
    assert page["ask"] == {"title": T("settings_corr_remove_all_title"), "body": T("settings_corr_remove_all_body", count=2),
                           "buttons": [T("common_cancel"), T("settings_corr_remove_all")]} and page["danger"] is True
    assert page["kept"] == 2 and data["queue"] == []
    assert page["result"] == T("settings_corr_removed_all", count=2)
    assert page["after"] == {"rows": 0, "empty": True, "remove": False}


def test_send_now_asks_first_and_cancel_sends_nothing_then_sends_by_the_job_and_empties_the_list(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid())
    _add(data, "zz-gone")
    started = _jobs(monkeypatch, data)
    page = _drive_corr(hub, tmp_path, r"""
footButton("send-corrections").click();
const dlg = await until(dialog, "the question");
out.ask = dialogInfo(dlg);
out.primary = !!$(".btn--primary", $(".dialog__foot", dlg));
press("cancel"); await closed();
out.kept = rows().length;
footButton("send-corrections").focus();
footButton("send-corrections").click();
await until(dialog, "the question again");
press("confirm"); await closed();
out.sending = await until(() => { const t = text(cardResult()); return t; }, "the sending line");
out.sendingFocus = {
  active: document.activeElement === footButton("send-corrections"),
  nativeDisabled: footButton("send-corrections").disabled,
  ariaDisabled: footButton("send-corrections").getAttribute("aria-disabled"),
};
out.result = await until(() => { const t = text(cardResult()); return t && t.startsWith("Sent") ? t : null; }, "the result");
out.after = { rows: rows().length, empty: !!$("#set-corrections .empty"), reason: text($("#set-corrections .btn__reason")) };
""")
    assert page["ask"] == {"title": T("settings_corr_send_title", count=2), "body": T("settings_corr_send_body"),
                           "buttons": [T("common_cancel"), T("settings_corr_send_confirm")]} and page["primary"] is True
    assert page["kept"] == 2 and started == ["corrections_send"]
    assert page["sending"] == T("settings_corr_sending", count=2)
    assert page["sendingFocus"] == {"active": True, "nativeDisabled": False, "ariaDisabled": "true"}
    assert page["result"] == T("settings_corr_sent", count=2)
    assert len(data["submits"]) == 1 and data["queue"] == []
    assert page["after"] == {"rows": 0, "empty": True, "reason": T("settings_corr_nothing")}


def test_a_send_the_reader_could_not_make_says_why_and_the_corrections_are_still_waiting(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid())
    data["submit"] = (502, "upload_failed")
    started = _jobs(monkeypatch, data)
    page = _drive_corr(hub, tmp_path, r"""
footButton("send-corrections").click();
await until(dialog, "the question");
press("confirm"); await closed();
out.result = await until(() => { const t = text(cardResult()); return t && t.startsWith("Could not") ? t : null; }, "the result");
out.state = $("#set-corrections .result").dataset.state;
out.rows = rows().length;
""")
    assert started == ["corrections_send"] and len(data["queue"]) == 1
    assert page["result"] == T("settings_corr_not_sent", reason="The reader refused: the local reader answered HTTP 502 (upload_failed): fake upload_failed.")
    assert page["state"] == "bad" and page["rows"] == 1


def test_download_the_package_saves_the_zip_the_hub_made_and_a_refusal_shows_its_code(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid())
    page = _drive_corr(hub, tmp_path, r"""
const saved = [];
const click = HTMLAnchorElement.prototype.click;
HTMLAnchorElement.prototype.click = function () { if (this.download) { saved.push(this.download); return; } return click.call(this); };
footButton("download-package").click();
await until(() => saved.length, "the file");
out.saved = saved;
out.result = text(cardResult());
""")
    assert page["saved"] == [f"spoolwarden-corrections-{time.strftime('%Y-%m-%d')}.zip"]
    # the reader goes away: the package cannot be made, in the Hub's words, with the code
    cfg = config.load()
    cfg["local_intake"] = {"url": "http://127.0.0.1:9"}
    config.save(cfg)
    failed = _drive_corr(hub, tmp_path, r"""
await until(() => $("#set-corrections .error-state"), "the error state");
const state = $("#set-corrections .error-state");
out.state = { text: text($(".error-state__text", state)), code: state.getAttribute("data-code"), details: text($(".error-state__details", state)) };
out.tryAgain = !!$('[data-action="try-again"]', state);
""")
    assert failed["state"]["text"] == T("settings_corr_reader_down") and failed["state"]["code"] == "unreachable" and "code: unreachable" in failed["state"]["details"]
    assert failed["tryAgain"] is True


def test_try_again_reads_the_list_again_when_the_reader_is_back(hub, fake_reader, monkeypatch, tmp_path):
    url, data = fake_reader
    _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid())
    cfg = config.load()
    cfg["local_intake"] = {"url": "http://127.0.0.1:9"}
    config.save(cfg)

    def back():
        time.sleep(1.5)
        cfg = config.load()
        cfg["local_intake"] = {"url": url}
        config.save(cfg)
    threading.Thread(target=back, daemon=True).start()
    page = _drive_corr(hub, tmp_path, r"""
out.error = !!$("#set-corrections .error-state") || card().hidden;
await sleep(1800);
const again = $('#set-corrections [data-action="try-again"]');
if (again) again.click();
await until(() => rows().length === 1, "the list");
out.rows = rows().length;
""")
    assert page["rows"] == 1


def test_a_refusal_of_remove_is_shown_in_the_hubs_words_with_its_code(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid())
    page = _drive_corr(hub, tmp_path, r"""
// another phone took the correction out after the list was drawn
await api("label-reader/corrections/" + rows()[0].dataset.id + "/remove", { code: "__CODE__" });
rows()[0].querySelector("button:not(.thumb)").click();
out.result = await done(cardResult, null);
const box = $("#set-corrections .result");
out.refusal = { state: box.dataset.state, code: box.getAttribute("data-code") };
""")
    assert page["result"] == reader_corrections.NOT_FOUND and page["refusal"] == {"state": "bad", "code": "not_found"}


def test_remove_asks_for_the_code_in_place_while_locked_and_the_list_still_reads(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid())
    page = _drive_corr(hub, tmp_path, r"""
out.rows = rows().length;
rows()[0].querySelector("button:not(.thumb)").click();
const dlg = await until(dialog, "the unlock question");
out.dialog = text($(".dialog__title", dlg));
""", lock=True)
    assert page["rows"] == 1 and page["dialog"] and len(data["queue"]) == 1


def test_a_correction_the_reader_did_not_take_is_not_counted_and_try_again_asks_the_hub_to_queue_it(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=True)
    rid = _rid()
    share_queue._write(rid, {"entries": {}, "failed": {"reason": "the reader did not answer", "at": "2026-11-15T12:00:00Z"}})
    page = _drive_corr(hub, tmp_path, r"""
out.count = text($("#set-corrections .corr__count"));
out.rows = rows().map(rowInfo);
out.empty = !!$("#set-corrections .empty");
""")
    assert page["count"] == "" and page["empty"] is False
    (row,) = page["rows"]
    assert T("settings_corr_not_waiting", reason="the reader did not answer") in row["text"] and row["link"] == f"#recent/{rid}"
    assert row["actions"][-1] == T("common_try_again")
    assert data["queue"] == []


# ---------- the phone ----------

def test_on_a_phone_the_group_and_the_card_show_and_the_list_names_the_corrections_waiting(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid())
    _add(data, "zz-gone")
    page = _drive_corr(hub, tmp_path, r"""
out.card = shown(card());
out.rows = rows().length;
out.switch = shown(sw());
location.hash = "#settings";
await until(() => { const s = $('.settings__row[data-id="label-reader"] .settings__state'); return s && s.textContent.includes("waiting") ? s : null; }, "the list line");
out.state = text($('.settings__row[data-id="label-reader"] .settings__state'));
""", width=360)
    assert page["card"] is True and page["rows"] == 2 and page["switch"] is True
    assert page["state"] == T("settings_state_reader_waiting", order=T("settings_reader_order_local"), count=2)


# ---------- Recently added (6.2, 6.4) ----------

RECENT = r"""
const recent = () => $("main");
const meta = () => text($(".page-head__meta"));
"""


def _drive_recent(hub, tmp_path, body, width=1280):
    script = PRELUDE.replace('location.hash = "#settings";', 'location.hash = "#recent";') + RECENT + HELPERS.split("const settle")[0] + \
        "const dialog = () => $('dialog[open]');\nawait until(() => $('.coll--recent .coll__item') || $('.error-state'), 'the list');\nconst out = {};\n" + \
        "SW.setLock('unlocked', { code: '__CODE__' });\n" + body + "\nreturn out;\n"
    return _drive(hub, tmp_path, script, width=width)


def test_the_list_header_names_share_corrections_with_the_link_to_what_waits_while_it_is_on(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid())
    _add(data, "zz-gone")
    page = _drive_recent(hub, tmp_path, r"""
await until(() => $(".page-head__meta a[href='#settings/label-reader']:not(.btn)"), "the link");
out.meta = meta();
out.link = [...document.querySelectorAll(".page-head__meta a")].map((a) => [text(a), a.getAttribute("href")]);
""")
    assert T("recent_corr_meta") + " " + T("status_on") in page["meta"] and T("recent_corr_meta_waiting", count=2) in page["meta"]
    assert [T("recent_corr_meta_waiting", count=2), "#settings/label-reader"] in page["link"]


def test_the_list_header_has_nothing_of_it_while_sharing_is_off(hub, fake_reader, monkeypatch, tmp_path):
    _reader(fake_reader, monkeypatch, saved=False)
    page = _drive_recent(hub, tmp_path, r"""
await sleep(600);
out.meta = meta();
""")
    assert T("recent_corr_meta") not in page["meta"]


RECORD = r"""
const openRecord = async (id) => { location.hash = "#recent/" + id; await until(() => $(".recent__main .btn"), "the record"); };
const edit = async () => {
  [...document.querySelectorAll(".recent__main .btn")].find((b) => text(b) === EDIT).click();
  await until(() => $(".recent__form"), "the form");
};
const saveVendor = async () => {
  const form = $(".recent__form");
  form.elements.vendor.value = "Polymakr";
  form.elements.vendor.dispatchEvent(new Event("input", { bubbles: true }));
  form.requestSubmit();
  return await until(() => { const r = $('.card .result[data-state="ok"] .result__text, .recent__foot .result[data-state="bad"] .result__text'); return r && text(r); }, "the save");
};
// the note about the correction comes a moment after the result; the record's own share state is read for the report
const noteAfter = async (state) => {
  for (let i = 0; i < 120; i++) { const r = $(`.card .result[data-state="${state}"] .result__text`); if (r && text(r)) return text(r); await sleep(100); }
  return null;
};
const shareOf = async (id) => (await api("intake/history/" + id)).share;
"""


def _drive_record(hub, tmp_path, body):
    return _drive_recent(hub, tmp_path, "const EDIT = " + json.dumps(T("recent_edit")) + ";\n" + RECORD + body)


def test_editing_by_hand_says_the_correction_will_wait_above_save_and_after_save_says_it_is_waiting_with_the_link(hub, fake_reader, monkeypatch, tmp_path, spoolman_url):
    data = _reader(fake_reader, monkeypatch, saved=True)
    rid = _record(spoolman_url)
    page = _drive_record(hub, tmp_path, f"""
await openRecord({json.dumps(rid)});
await edit();
const form = $(".recent__form");
out.note = text($(".banner", form));
out.order = !!(form.querySelector(".banner").compareDocumentPosition(form.querySelector(".recent__foot")) & Node.DOCUMENT_POSITION_FOLLOWING);
out.saved = await saveVendor();
out.after = await noteAfter("info");
out.share = await shareOf({json.dumps(rid)});
out.link = [...document.querySelectorAll('.card .result[data-state="info"] a')].map((a) => [text(a), a.getAttribute("href")]);
""")
    assert page["note"] == T("recent_corr_on") and page["order"] is True
    assert page["saved"].startswith("Saved"), page
    assert page["after"] and T("recent_corr_waiting") in page["after"], page
    assert page["link"] == [[T("recent_corr_link"), "#settings/label-reader"]]
    assert len(data["queue"]) == 1 and data["queue"][0]["corrected"]["vendor"] == "Polymakr"


def test_a_save_the_reader_did_not_take_says_so_in_the_hubs_reason(hub, fake_reader, monkeypatch, tmp_path, spoolman_url):
    data = _reader(fake_reader, monkeypatch, saved=True)
    data["errors"] = [(500, "boom", None)] * 3
    rid = _record(spoolman_url)
    page = _drive_record(hub, tmp_path, f"""
await openRecord({json.dumps(rid)});
await edit();
out.saved = await saveVendor();
out.after = await noteAfter("warn");
out.share = await shareOf({json.dumps(rid)});
""")
    assert page["saved"].startswith("Saved"), page
    assert page["share"]["state"] == "not_queued" and page["after"], page
    assert page["after"] == T("recent_corr_not_taken", reason=page["share"]["reason"])


def test_a_record_with_no_photo_says_it_is_not_shared(hub, fake_reader, monkeypatch, tmp_path, spoolman_url):
    _reader(fake_reader, monkeypatch, saved=True)
    rid = _record(spoolman_url, photos=0)
    page = _drive_record(hub, tmp_path, f"""
await openRecord({json.dumps(rid)});
await edit();
out.saved = await saveVendor();
out.after = await noteAfter("info");
out.share = await shareOf({json.dumps(rid)});
""")
    assert page["saved"].startswith("Saved"), page
    assert page["after"] and T("recent_corr_no_photo") in page["after"], page


def test_a_suggestion_says_the_correction_will_wait_above_accept_and_accepting_says_it_is_waiting(hub, fake_reader, monkeypatch, tmp_path, spoolman_url):
    _reader(fake_reader, monkeypatch, saved=True)
    rid = _record(spoolman_url)
    monkeypatch.setattr(intake, "_call", lambda *a, **k: {"items": [{**k["revise"][0], "vendor": "Polymakr"}], "reader": "local", "confidence": 0.9, "note": "",
                                                          "engine": {"reader": "local", "model": "label-reader", "tier": "full"}})
    page = _drive_record(hub, tmp_path, f"""
await openRecord({json.dumps(rid)});
await until(() => $("#recent-ask-note"), "the ask card");
const note = $("#recent-ask-note");
note.value = "brand is Polymakr";
note.dispatchEvent(new Event("input", {{ bubbles: true }}));
note.dispatchEvent(new Event("change", {{ bubbles: true }}));
[...document.querySelectorAll(".card .btn")].find((b) => text(b) === {json.dumps(T("recent_ask_button"))}).click();
const sug = await until(() => $(".changes[data-proposal]"), "the suggestion");
out.note = text($(".banner", sug));
out.order = !!(sug.querySelector(".banner").compareDocumentPosition(sug.querySelector(".actions")) & Node.DOCUMENT_POSITION_FOLLOWING);
[...sug.querySelectorAll(".btn")].find((b) => text(b) === {json.dumps(T("common_accept"))}).click();
out.accepted = await until(() => {{ const r = $('.card .result[data-state="ok"] .result__text'); return r && text(r); }}, "Accept");
out.after = await noteAfter("info");
out.share = await shareOf({json.dumps(rid)});
""")
    assert page["note"] == T("recent_corr_on") and page["order"] is True, page
    assert page["after"] and T("recent_corr_waiting") in page["after"], page


def test_nothing_of_it_is_on_a_record_while_sharing_is_off(hub, fake_reader, monkeypatch, tmp_path, spoolman_url):
    _reader(fake_reader, monkeypatch, saved=False)
    rid = _record(spoolman_url)
    page = _drive_recent(hub, tmp_path, f"""
location.hash = "#recent/{rid}";
await until(() => $(".recent__main .btn"), "the record");
[...document.querySelectorAll(".recent__main .btn")].find((b) => text(b) === {json.dumps(T("recent_edit"))}).click();
await until(() => $(".recent__form"), "the form");
out.banners = document.querySelectorAll(".recent__form .banner").length;
""")
    assert page["banners"] == 0


# ---------- Features ----------

FEATURES = r"""
const featuresReady = async () => { await until(() => $('[data-key="local_intake"] button'), "the features"); };
"""


def _drive_features(hub, tmp_path, body):
    script = PRELUDE.replace('location.hash = "#settings";', 'location.hash = "#features";') + FEATURES + HELPERS.split("const settle")[0] + \
        "const dialog = () => $('dialog[open]');\nconst out = {};\nawait featuresReady();\n" + body + "\nreturn out;\n"
    return _drive(hub, tmp_path, script)


def test_turning_label_reader_off_while_corrections_wait_says_they_are_deleted_and_not_otherwise(hub, fake_reader, monkeypatch, tmp_path):
    data = _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid())
    _add(data, "zz-gone")
    page = _drive_features(hub, tmp_path, r"""
$('[data-key="local_intake"] button').click();
const dlg = await until(() => $("dialog[open].dialog--drawer .features__preview .banner, dialog[open].dialog--drawer .features__preview p"), "the drawer");
await until(() => !$("dialog[open] .skeleton"), "the preview");
out.warn = text($("dialog[open] .features__preview .banner--warn"));
""")
    assert page["warn"] == T("features_corr_warn", count=2)
    data["queue"].clear()
    none = _drive_features(hub, tmp_path, r"""
$('[data-key="local_intake"] button').click();
await until(() => $("dialog[open].dialog--drawer"), "the drawer");
await until(() => !$("dialog[open] .skeleton"), "the preview");
out.warn = !!$("dialog[open] .features__preview .banner--warn");
""")
    assert none["warn"] is False


# ---------- the audit ----------

READY = """(async () => {
  const until = async (what, f, ms = 30000) => {
    for (let t = 0; t < ms && !f(); t += 100) await new Promise((r) => setTimeout(r, 100));
    if (!f()) throw new Error("Share corrections: " + what + " :: " + document.querySelector("main").innerText.slice(0, 300));
  };
  const card = () => document.querySelector("#set-corrections");
  await until("the card was not drawn", () => card() && !card().hidden && (card().querySelector(".revisions__list") || card().querySelector(".error-state")));
  %s
})()"""


def _audit(hub, step=""):
    run = _run("--url", hub, "--views", "#settings/label-reader", "--ready", READY % step, "--allow", str(ALLOW))
    assert run.returncode == 0, run.stdout + run.stderr
    out = json.loads(run.stdout)
    assert out["views"] == 4 and out["failing"] == 0


def test_the_audit_passes_on_the_label_reader_group_with_the_card_at_both_widths(hub, fake_reader, monkeypatch):
    data = _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid(), note="brand is Polymaker")
    _add(data, "zz-gone", source="cloud")
    _audit(hub, 'if (document.querySelectorAll("#set-corrections .revisions__item").length !== 2) throw new Error("two rows");')


@pytest.mark.parametrize("action", ["remove-all-corrections", "send-corrections"])
def test_the_audit_passes_with_each_question_of_the_card_open(hub, fake_reader, monkeypatch, action):
    data = _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid())
    _audit(hub, f'document.querySelector(\'#set-corrections [data-action="{action}"]\').click(); await until("the question", () => document.querySelector("dialog[open]"));')


def test_the_audit_passes_with_the_question_about_switching_off_open(hub, fake_reader, monkeypatch):
    data = _reader(fake_reader, monkeypatch, saved=True)
    _add(data, _rid())
    _audit(hub, """const sw = document.querySelector('#set-label-reader input[role="switch"]');
  await until("the switch", () => !sw.disabled);
  sw.click();
  document.querySelector('#set-label-reader [type=submit]').click();
  await until("the question", () => document.querySelector("dialog[open]"));""")


def test_the_audit_passes_with_the_refusal_shown_with_its_code(hub, fake_reader, monkeypatch):
    _reader(fake_reader, monkeypatch, saved=True)
    cfg = config.load()
    cfg["local_intake"] = {"url": "http://127.0.0.1:9"}
    config.save(cfg)
    _audit(hub, 'if (card().querySelector(".error-state").getAttribute("data-code") !== "unreachable") throw new Error("no code");')
