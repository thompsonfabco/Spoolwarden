"""Settings > Weigh Station voice on the Hub page (DECISIONS R88 2.1 to 2.4 and the 02:00 ruling, step VO3), in a real headless browser:
the rows Speak, Voice, Speaker, Speed, Volume and Test; no Save button, each row applies as it is changed (a slider when it is let go);
the Speaker row for the many-speaker voice only; the line under Voice; the four results of Test; a refused change going back; and a
Weigh Station on another computer. The group is built by setup_voice.js, which RG1 registers; until then this test loads it the way RG1
will (its script next to the others, its builder in the Voice row of SET_GROUPS). The Hub's side (VO2) is a stub here: the page's `api`
is wrapped, so station/voice (the view read G, which needs no code, and the save by P of Speak, Speaker, Speed and Volume), the Voice row's settings/general write and
station/voice/test answer from the test. The settings values of the four are decoys: the group must read only the answer. The same skip rules as the page audit: no Node 22 or no
browser is a skip (a failure with AUT5_REQUIRED=1)."""
from __future__ import annotations

import pytest

from filament_kit import setup_page
from test_hub_page_audit import hub_app  # noqa: F401
from test_hub_presets_ui import _drive, hub  # noqa: F401
from test_hub_settings import PRELUDE


@pytest.fixture(autouse=True)
def _voice_files(monkeypatch):
    """The script is served as RG1 will have it listed (an autouse fixture runs before the Hub is built)."""
    monkeypatch.setattr(setup_page, "SHELL_SCRIPTS", (*setup_page.SHELL_SCRIPTS, "setup_voice.js"))


HELPERS = r"""
// the stub Hub: every request the group makes is recorded; a saved value is kept and shown on the next read
const calls = [], reads = [], codeFree = {};
const voice = { speech: true, speaker: 0, speed: 100, volume: 50, voice: "Piper, many speakers", speaker_range: [0, 903] };
const general = { "station.tts": "auto", "station.voice": "en_US-libritts_r-medium" };
const plan = { remote: false, test: null, save: null };
const realApi = api;
api = async (path, body) => {
  if (path === "station/voice" && body === undefined) { reads.push(codeFree); return { station: true, ...voice, remote: plan.remote }; }   // G, the view read
  if (path === "station/voice") {
    const { code, ...rest } = body;
    if (plan.save) { calls.push(["refused", rest]); throw hubError(400, { error: "no way", code: "bad_value", field: Object.keys(rest)[0] }); }
    calls.push(["save", rest]); Object.assign(voice, rest); return { station: true, ...voice, remote: plan.remote };
  }
  if (path === "settings/general" && body && body.values) {
    if (plan.save) { calls.push(["refused", body.values]); throw hubError(400, { error: "no way", field: "x" }); }
    calls.push(["general", body.values]); Object.assign(general, body.values); return { values: body.values };
  }
  if (path === "station/voice/test") { const { code, ...rest } = body; calls.push(["test", rest]); if (plan.test) throw hubError(plan.test[0], plan.test[1]); return {}; }
  return realApi(path, body);
};
const entry = SET_GROUPS.find((x) => x[0] === "voice");
// the withdrawn settings keys carry other values than the answer: a group that read them would show them
const decoys = { "station.speech": false, "station.voice_speaker": 999, "station.voice_speed": 200, "station.speaker_volume": 5 };
entry[3] = (d) => voiceGroup({ g: { ...d.g, station_on: true, values: { ...d.g.values, ...decoys, ...general } } });
const redraw = async () => { await loadGroup("voice", { fresh: true }); await until(() => $("#set-voice .voice__test"), "the voice group"); await sleep(100); };
const grp = () => $("#set-voice");
const sw = () => $('#set-voice input[role="switch"]');
const pickEl = () => $("#set-voice select");
const speakerEl = () => $("#set-voice .voice__speaker input");
const sliders = () => [...document.querySelectorAll("#set-voice input[type=range]")];
const readout = (c) => text($(".field__unit", c.closest(".field")));
const change = (c, value) => { c.value = value; c.dispatchEvent(new Event("input", { bubbles: true })); c.dispatchEvent(new Event("change", { bubbles: true })); };
const testBtn = () => $('#set-voice [data-action="voice-test"]');
const resultText = () => text($("#set-voice .voice__test .result__text"));
const ready = async () => { await loaded(); await unlock(); await redraw(); };
"""


def _drive_voice(hub, tmp_path, body, width=1280):
    script = PRELUDE + HELPERS + "\nawait ready();\nconst out = {};\n" + body + "\nreturn out;\n"
    return _drive(hub, tmp_path, script, width=width)


def test_every_row_is_there_with_its_control_value_and_words_and_there_is_no_save_button(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
out.title = text($("#set-voice .group__title"));
out.labels = [...document.querySelectorAll("#set-voice .field__label, #set-voice .toggle__label")].map(text);
out.speak = { checked: sw().checked, role: sw().getAttribute("role") };
out.reads = reads.length;
out.voice = { value: pickEl().value, options: [...pickEl().options].map((o) => [o.value, o.text]), hint: text($(".field__hint", pickEl().closest(".field"))) };
out.speaker = { shown: shown(speakerEl()), value: speakerEl().value, hint: text($(".field__hint", speakerEl().closest(".field"))) };
out.sliders = sliders().map((c) => ({ min: c.min, max: c.max, step: c.step, value: c.value, readout: readout(c) }));
out.buttons = [...document.querySelectorAll("#set-voice button")].map(text);
out.form = grp().tagName === "FORM";
change(sliders()[0], "150");
out.moved = readout(sliders()[0]);
""")
    assert page["title"] == "Weigh Station voice"
    assert page["labels"] == ["Speak", "Voice", "Speaker", "Speed", "Volume"]
    assert page["speak"] == {"checked": True, "role": "switch"} and page["reads"] >= 1                 # the unlock and the redraw each read
    assert page["voice"] == {"value": "many", "hint": "The Weigh Station restarts when the voice changes.",
                             "options": [["many", "Piper, many speakers"], ["one", "Piper, one clear speaker"], ["espeak", "espeak-ng, robotic, no download"]]}
    assert page["speaker"] == {"shown": True, "value": "0", "hint": "This voice has 904 speakers. Try a few with Test."}
    assert page["sliders"] == [{"min": "50", "max": "200", "step": "10", "value": "100", "readout": "100 %"},
                               {"min": "0", "max": "100", "step": "1", "value": "50", "readout": "50"}]
    assert page["buttons"] == ["Test"] and page["form"] is False
    assert page["moved"] == "150 %"


def test_the_speaker_row_shows_for_the_many_speaker_voice_only_and_test_always_reads_test(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
const sel = () => ({ speaker: shown(speakerEl()), button: text(testBtn()) });
out.start = sel();
change(pickEl(), "one"); await sleep(200);
out.one = sel();
change(pickEl(), "espeak"); await sleep(200);
out.espeak = sel();
change(pickEl(), "many"); await sleep(200);
out.back = sel();
""")
    assert page["start"] == {"speaker": True, "button": "Test"}
    assert page["one"] == {"speaker": False, "button": "Test"}
    assert page["espeak"] == {"speaker": False, "button": "Test"}
    assert page["back"] == {"speaker": True, "button": "Test"}


def test_each_row_applies_as_it_changes_with_numbers_and_the_voice_writes_its_two_keys(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
const nth = async (n) => { await until(() => calls.length === n, "request " + n); await sleep(250); return calls[n - 1]; };
sw().click();
out.speak = await nth(1);
change(speakerEl(), "412");
out.speaker = await nth(2);
change(sliders()[0], "70");
out.speed = await nth(3);
change(sliders()[1], "80");
out.volume = await nth(4);
change(pickEl(), "espeak");
out.espeak = await nth(5);
change(pickEl(), "one");
out.one = await nth(6);
await sleep(300);
out.count = calls.length;
await redraw();
out.kept = { speak: sw().checked, speed: sliders()[0].value, volume: sliders()[1].value, choice: pickEl().value };
""")
    assert page["speak"] == ["save", {"speech": False}]
    assert page["speaker"] == ["save", {"speaker": 412}]
    assert page["speed"] == ["save", {"speed": 70}]                       # only the changed value, to P station/voice
    assert page["volume"] == ["save", {"volume": 80}]
    assert page["espeak"] == ["general", {"station.tts": "espeak"}]      # only the Voice row stays on the settings
    assert page["one"] == ["general", {"station.tts": "auto", "station.voice": "en_US-ljspeech-medium"}]
    assert page["count"] == 6
    assert page["kept"] == {"speak": False, "speed": "70", "volume": "80", "choice": "one"}


def test_test_sends_the_values_on_the_page_and_says_played_and_each_refusal_in_its_words(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
let last = null;
const press = async (fresh) => {
  testBtn().click();
  await until(() => calls.length === fresh && resultText() !== last && resultText() && !$("#set-voice .voice__test .result[data-state=run]"), "a test result");
  return (last = resultText());
};
speakerEl().value = "7";                                                    // typed, not let go
sliders()[0].value = "130";
sliders()[1].value = "35";
out.ok = await press(1);
out.sent = calls[0];
for (const [n, status, data] of [[2, 409, { error: "busy", code: "busy" }], [3, 409, { error: "off", code: "off" }], [4, 502, { error: "x", code: "no_answer" }]]) {
  plan.test = [status, data];
  out["r" + n] = await press(n);
}
""")
    assert page["ok"] == "Played on the Weigh Station."
    assert page["sent"] == ["test", {"speaker": 7, "speed": 130, "volume": 35}]
    assert page["r2"] == "The Weigh Station is in use. Try again when it is idle."
    assert page["r3"] == "Speaking is switched off."
    assert page["r4"] == "The Weigh Station did not answer."


def test_test_for_the_one_speaker_voice_carries_no_speaker(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
change(pickEl(), "one");
await until(() => calls.length === 1, "the voice change");
await sleep(300);
testBtn().click();
await until(() => calls.length === 2 && resultText() === "Played on the Weigh Station.", "the test");
out.calls = calls.slice();
""")
    assert page["calls"] == [["general", {"station.tts": "auto", "station.voice": "en_US-ljspeech-medium"}], ["test", {"speed": 100, "volume": 50}]]


def test_a_bad_speaker_number_sends_nothing_and_a_refused_change_goes_back_with_its_reason(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
change(speakerEl(), "904");
testBtn().click();
await sleep(300);
out.bad = { calls: calls.length, error: text($(".field__error", speakerEl().closest(".field"))) };
change(speakerEl(), "5");
await until(() => calls.length === 1, "the speaker save");
await sleep(300);
plan.save = true;
change(sliders()[0], "60");
await until(() => calls.length === 2, "the refused speed");
await sleep(300);
change(pickEl(), "espeak");
await until(() => calls.length === 3, "the refused voice");
await sleep(400);
out.refused = { calls: calls.map((c) => c[0]), speed: sliders()[0].value, readout: readout(sliders()[0]), voice: pickEl().value, speaker: shown(speakerEl()), result: resultText() };
""")
    assert page["bad"] == {"calls": 0, "error": "Enter a number from 0 to 903."}
    assert page["refused"] == {"calls": ["save", "refused", "refused"], "speed": "100", "readout": "100 %", "voice": "many", "speaker": True, "result": "No way."}


def test_a_weigh_station_on_another_computer_shows_where_the_voice_is_chosen_and_keeps_the_other_rows(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
plan.remote = true;
await redraw();
out.line = text($("#set-voice .voice__remote"));
out.select = $("#set-voice select") === null;
out.labels = [...document.querySelectorAll("#set-voice .field__label, #set-voice .toggle__label")].map(text);
out.speaker = shown(speakerEl());
change(sliders()[1], "10");
await until(() => calls.length === 1, "the volume");
await sleep(300);
testBtn().click();
await until(() => calls.length === 2 && resultText() === "Played on the Weigh Station.", "the test");
out.calls = calls.slice();
""")
    assert page["line"] == "Chosen on the Weigh Station's own computer: spoolwarden config."
    assert page["select"] is True
    assert page["labels"] == ["Speak", "Speaker", "Speed", "Volume"] and page["speaker"] is True
    assert page["calls"] == [["save", {"volume": 10}], ["test", {"speaker": 0, "speed": 100, "volume": 10}]]


def test_the_group_is_laid_out_without_sideways_scrolling_on_a_phone(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
location.hash = "#settings/voice";
await until(() => shown(sliders()[0]), "the voice group on its own page");
out.wide = document.documentElement.scrollWidth <= document.documentElement.clientWidth;
out.slider = shown(sliders()[0]);
""", width=390)
    assert page == {"wide": True, "slider": True}


def test_the_values_come_only_from_the_voice_answer_and_the_settings_values_of_the_four_are_never_used_or_written(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
out.shown = { speak: sw().checked, speaker: speakerEl().value, speed: sliders()[0].value, volume: sliders()[1].value };
out.reads = reads.slice();
change(sliders()[0], "120");
await until(() => calls.length === 1, "the speed save");
await sleep(300);
change(pickEl(), "one");
await until(() => calls.length === 2, "the voice change");
await sleep(300);
out.calls = calls.slice();
out.withdrawn = JSON.stringify(calls).match(/station\.(speech|voice_speaker|voice_speed|speaker_volume)/);
""")
    assert page["shown"] == {"speak": True, "speaker": "0", "speed": "100", "volume": "50"}      # not the decoys
    assert page["reads"] and all(r == {} for r in page["reads"])      # a read is the code-free G
    assert page["calls"] == [["save", {"speed": 120}], ["general", {"station.tts": "auto", "station.voice": "en_US-ljspeech-medium"}]]
    assert page["withdrawn"] is None


def test_while_the_page_is_locked_the_rows_show_read_only_without_a_note_and_are_editable_after_unlock(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
SW.setLock("locked");
await until(() => $("#set-voice .voice__test") && !pickEl(), "the locked rows");
await sleep(100);
const controls = () => [...document.querySelectorAll("#set-voice input, #set-voice select, #set-voice button")];
out.locked = { rows: !!sw(), note: !!$("#set-voice .banner"),
  voiceValue: [...document.querySelectorAll("#set-voice .field")].filter((f) => text($(".field__label", f)) === "Voice").map((f) => f.querySelector("input").value),
  disabled: controls().every((c) => c.disabled), count: controls().length,
  values: { speak: sw().checked, speed: sliders()[0].value, volume: sliders()[1].value } };
out.html = grp().textContent.includes("shown once the page is unlocked");
const before = reads.length;
SW.setLock("unlocked", { code: "__CODE__" });
await until(() => pickEl(), "the editable rows");
await sleep(100);
out.reads = reads.length - before;
out.open = { disabled: controls().filter((c) => c.disabled).length, note: !!$("#set-voice .banner") };
change(sliders()[0], "90");
await until(() => calls.length === 1, "the speed save");
out.calls = calls.slice();
""")
    assert page["locked"]["rows"] and page["locked"]["note"] is False
    assert page["locked"]["voiceValue"] == ["Piper, many speakers"]
    assert page["locked"]["disabled"] is True and page["locked"]["count"] >= 5
    assert page["locked"]["values"] == {"speak": True, "speed": "100", "volume": "50"}
    assert page["html"] is False
    assert page["reads"] == 1 and page["open"] == {"disabled": 0, "note": False}
    assert page["calls"] == [["save", {"speed": 90}]]
