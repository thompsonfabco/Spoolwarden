"""Wave round 3 the Hub page and station page scripts: static checks of the script text for the
behaviour each finding asks for. The browser suites that drive these scripts are not run in this round."""
from __future__ import annotations

import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "filament_kit"
STATIC = PKG / "setup_static"


def js(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def station_js() -> str:
    return (PKG / "station" / "static" / "station.js").read_text(encoding="utf-8").replace("\r\n", "\n")


def between(text: str, start: str, end: str) -> str:
    i = text.index(start)
    return text[i:text.index(end, i + len(start))]


def stub(text: str, key: str) -> str:
    m = re.search(rf'^\s*{key}: "((?:[^"\\]|\\.)*)",?$', text, re.M)
    assert m, key
    return m.group(1)


# ---- MEDIUM 5: the restore confirmation carries the Tag Reader and the password sentences

def test_the_restore_confirm_says_the_tag_reader_and_the_password_sentences():
    src = js("setup_backup.js")
    ask = between(src, "function restoreAsk(", "\nfunction restoreSteps(")
    assert re.search(r's\.other_host \? para\(SW\.t\("reader_setup_again"\)\)', ask)
    assert re.search(r's\.encrypted \? para\(SW\.t\("settings_restore_encrypted_password"\)\)', ask)
    assert 'SW.t("settings_restore_body")' in ask
    # a backup opened with its password is marked encrypted for the summary and the confirm
    assert "checked({ ...unlocked, encrypted: true })" in src
    assert 's.encrypted && line("settings_restore_summary_encrypted_line")' in src


# ---- MEDIUM 6: Label Reader Trust asks first, shows the old and the new fingerprint, in the design's words (D4)

def test_trust_asks_first_and_shows_both_fingerprints():
    src = js("setup_settings.js")
    test = between(src, "async function testReader(", "\n// the reader that runs on this machine")
    run = between(test, 'action: "trust-reader-cert"', "      return;\n")
    ask, trust = run.index("await SW.ask("), run.index("runRemoteTest(true)")
    assert ask < trust and "if (!yes) return;" in run[ask:trust]
    assert 'setT("settings_reader_trust_title")' in run and 'setT("settings_reader_trust_body", { address: url })' in run
    assert "fpRows(r)" in run
    rows = between(test, "const fpRows = ", "const show = ")
    assert '"settings_reader_cert_old", r.pinned' in rows and '"settings_reader_cert_new", r.fp' in rows
    assert "btn--small" not in run                      # the default button, as the design, Hub page says


def test_trust_texts_are_the_designs():
    src = js("setup_settings.js")
    assert stub(src, "settings_reader_cert_changed") == "The Label Reader's certificate changed."
    assert stub(src, "settings_reader_trust") == "Trust the new certificate"
    assert stub(src, "settings_reader_trust_title") == "Trust the new certificate of the Label Reader?"
    assert stub(src, "settings_reader_trust_body") == (
        "The Hub accepts the certificate the Label Reader at {address} shows now, in place of the one it saved. "
        "Do this only after the reader was reinstalled or moved.")


def test_trust_moves_the_focus_to_test_afterwards():
    run = between(js("setup_settings.js"), 'action: "trust-reader-cert"', "      return;\n")
    assert run.index("await runRemoteTest(true)") < run.index("again.focus()")
    assert '[data-action="test-reader"]' in run


# ---- MEDIUM 10: Lock now and Unlock keep the embedded live view and its camera

def test_a_lock_redraw_keeps_the_live_views():
    src = js("setup_printers.js")
    lock = between(src, 'document.addEventListener("sw:lock", () => {', "});")
    assert "drawList(listEl, true)" in lock
    stop = between(src, "function stopRowLives(keep)", "\n}")
    assert "if (keep && v.live) keep.set(serial, v.live);" in stop
    assert "else if (v.live && typeof v.live.stop === \"function\") v.live.stop();" in stop
    region = between(src, "function detailRegion(", "const back = ")
    assert "(keptLives && keptLives.get(p.serial)) || window.printerLive(p)" in region
    draw = between(src, "function drawList(body, keepLive = false)", "\nfunction drawListNow(")
    assert "keptLives = keepLive ? new Map() : null;" in draw and "l.stop()" in draw      # a printer that went away stops
    assert "stopRowLives(keptLives);" in between(src, "function drawListNow(", "rowViews = new Map();")


# ---- MEDIUM 11: a spool's events, the newest ten, newest first, in the Hub's words (the design, Hub page, D1)

def test_a_spools_events_are_the_newest_ten_newest_first_in_the_hubs_words():
    src = js("setup_spools.js")
    ev = between(src, "view.events = async (again) => {", "\n  };")
    assert "&newest=10`" in ev and "limit=10" not in ev
    assert ".slice(-10).reverse()" in ev
    assert "ev.kind" not in ev and "actor.type" not in ev
    assert "spWho(ev.actor)" in ev
    who = between(src, "const SP_WHO = ", "\n")
    for t in ("page", "cli", "hub", "device", "connector"):
        assert f'{t}: "activity_who_{t}"' in who
    assert '"activity_who_unknown"' in between(src, "const spWho = ", "\n")
    assert stub(src, "spools_events_title") == "Latest events"
    assert stub(src, "spools_events_none") == "Nothing has been recorded for this spool yet."


# ---- MEDIUM 13: the station page's status rows hold no English of their own and no raw ids (D13)

def test_the_station_rows_have_no_inline_english_and_no_raw_ids():
    src = station_js()
    status = between(src, "function showStatus(s) {", "var img = ")
    for bad in ('" (firmware "', '" C, "', '" s)"', '" g, "', '" g"', "s.flow.step +", "s.scale.status :"):
        assert bad not in src, bad
    assert 'word("station_step_", s.flow.step)' in status
    assert 'scaleState(s.scale.status)' in status
    for key in ("station_page_age", "station_page_grams", "station_page_scale_value", "station_page_panel_firmware",
                "station_page_air_value", "station_page_step_message"):
        assert f'"{key}"' in src, key
    assert 'word("station_scale_", st)' in src and 'tx("station_scale_unit")' in src
    assert 'label.replace("{mode}", word("station_page_mode_", mode))' in src
    assert 'label.replace("{mode}", mode)' not in src


def test_enter_in_a_station_setting_does_not_reload_the_page():
    src = station_js()
    assert re.search(r'\$\("settings"\)\.addEventListener\("submit", function \(e\) \{ e\.preventDefault\(\);', src)


# ---- Overview and Getting started

def test_still_to_do_keeps_its_setup_code_row_while_getting_started_shows():
    src = js("setup_overview.js")
    assert "todo.hidden = Boolean(r?.show)" not in src
    assert "todoItems().filter((t) => !codeOnly || t.code)" in src
    start = between(src, "function overviewStart() {", "\n}")
    assert "todoCard(true)" in start and "todo.replaceChildren(todoCard(ovStartShown)" in start


def test_make_the_fields_is_not_filled_while_getting_started_shows():
    src = js("setup_overview.js")
    fix = between(src, 'if (r.action === "ensure_fields") {', "\n  }")
    assert 'cls: fieldsCls(), action: "make-fields"' in fix and '"btn--primary", run' not in fix
    assert 'const fieldsCls = () => (ovStartShown ? "" : "btn--primary");' in src
    assert 'b.classList.toggle("btn--primary", !ovStartShown)' in src


def test_getting_started_keeps_the_focus_and_announces_after_hide_show_and_keep():
    src = js("setup_start.js")
    post = between(src, "async function post(", "\n    }\n")
    assert post.index("paint(fresh);") < post.index("settle(fresh);")
    settle = between(src, "function settle(data) {", "\n    }\n")
    assert "target.focus()" in settle and "s.announce(" in settle
    assert '[data-action="start-show"]' in settle and '[data-action="start-hide"]' in settle
    assert ".start__step--next .start__act" in settle
    assert 'el("h2", { class: "start__title", tabindex: "-1"' in src


# ---- the catalog Remove texts are the Hub page's and the command line's

def test_catalog_remove_texts_match_the_command_line():
    src = js("setup_settings.js")
    assert stub(src, "settings_catalog_remove_title") == "Remove {brand} {name} from your own entries?"
    assert stub(src, "settings_catalog_remove_body") == "Slicer presets are rebuilt."


# ---- LOW one-liners

def test_after_remove_the_focus_goes_to_the_remove_in_its_place():
    src = js("setup_settings.js")
    rm = between(src, 'action: "remove-local-entry"', "body.replaceChildren(tbl);")
    assert rm.index("await loadList();") < rm.index("(left[Math.min(at, left.length - 1)] || closeBtn).focus();")


def test_the_update_button_names_a_port_change_it_waits_for():
    run = between(js("setup_updates.js"), "const runningJob = () => {", "};")
    assert '"port_change"' in run


def test_features_knows_refusals_by_code_not_by_english():
    src = js("setup_features.js")
    fn = between(src, "function isRemote443(", "\n}")
    assert 'data.code === "remote_443"' in fn
    assert "remote access" not in fn and "443\\b" not in fn and "port_remote_refusal" not in fn
    assert "/disable server/" not in src
    assert 'p.code === "server_module" || (m.name === "server" && m.on)' in src


def test_logs_activity_has_one_page_menu():
    logs = js("setup_logs.js")
    more = between(logs, "const actMore = ", "const actHead = ")
    assert 'SW.t("common_page_actions")' in more and "actMore.dataset.pageMenu" in more
    assert "hubWebMenuItem()" in more and "common_more_actions_for" not in more
    head = between(js("setup.js"), "function pageHead(meta, actions) {", "\n}")
    assert '!(actions && actions.querySelector("[data-page-menu]"))' in head


def test_the_port_dialog_focuses_its_field():
    dlg = between(js("setup_settings.js"), "function openPortDialog(", "\nfunction groupNetwork(")
    assert 'kind: "entry"' in dlg and 'kind: "confirm"' not in dlg


def test_density_and_speed_get_a_decimal_keypad():
    src = js("setup_settings.js")
    for name in ("density", "max_volumetric_speed"):
        line = next(x for x in src.splitlines() if f'name: "{name}", type: "number"' in x)
        assert "decimal: true" in line, name
