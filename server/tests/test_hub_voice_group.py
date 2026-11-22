"""Settings > Weigh Station voice on the Hub page (DECISIONS R88 2.1 to 2.4, step VO3), in a real headless browser: the rows Speak,
Voice, Speaker, Speed, Volume and Test, the Speaker row for the many-speaker voice only, "Save, then test" for an unsaved Voice, the
four results of Test, and a Weigh Station on another computer. The group is built by setup_voice.js, which RG1 registers; until then
this test loads it the way RG1 will (its script and style next to the others, its builder in the Voice row of SET_GROUPS). The Hub's
side (VO2) is a stub here: the page's `api` is wrapped, so settings/general and station/voice/test answer from the test. The same skip
rules as the page audit: no Node 22 or no browser is a skip (a failure with AUT5_REQUIRED=1)."""
from __future__ import annotations

import pytest

from filament_kit import setup_page
from test_hub_page_audit import hub_app  # noqa: F401
from test_hub_presets_ui import _drive, hub  # noqa: F401
from test_hub_settings import PRELUDE


@pytest.fixture(autouse=True)
def _voice_files(monkeypatch):
    """The script and the style are served as RG1 will have them listed (an autouse fixture runs before the Hub is built)."""
    monkeypatch.setattr(setup_page, "SHELL_SCRIPTS", (*setup_page.SHELL_SCRIPTS, "setup_voice.js"))
    monkeypatch.setattr(setup_page, "CSS_SECTIONS", (*setup_page.CSS_SECTIONS, "setup_voice.css"))


HELPERS = r"""
// the stub Hub: every request the group makes is recorded; a saved value is kept and shown on the next read
const calls = [];
const stored = { "station.speech": true, "station.tts": "auto", "station.voice": "en_US-libritts_r-medium", "station.voice_speaker": 0, "station.voice_speed": 100, "station.speaker_volume": 50 };
const plan = { remote: false, test: null };
const realApi = api;
api = async (path, body) => {
  if (path === "settings/general" && body && body.values) { calls.push(["save", body.values]); Object.assign(stored, body.values); return { values: body.values }; }
  if (path === "station/voice/test") { const { code, ...rest } = body; calls.push(["test", rest]); if (plan.test) throw hubError(plan.test[0], plan.test[1]); return {}; }
  return realApi(path, body);
};
const entry = SET_GROUPS.find((x) => x[0] === "voice");
entry[3] = (d) => voiceGroup({ g: { ...d.g, station_on: true, station_remote: plan.remote, values: { ...d.g.values, ...stored } } });
const redraw = async () => { await loadGroup("voice", { fresh: true }); await until(() => $("#set-voice .voice__test"), "the voice group"); await sleep(100); };
const form = () => $("#set-voice");
const field = (name) => form().elements[name].closest(".field");
const set = (name, value) => { const c = form().elements[name]; c.value = value; c.dispatchEvent(new Event("input", { bubbles: true })); c.dispatchEvent(new Event("change", { bubbles: true })); };
const testBtn = () => $('#set-voice [data-action="voice-test"]');
const resultText = () => text($("#set-voice .voice__test .result__text"));
const ready = async () => { await loaded(); await unlock(); await redraw(); };
"""


def _drive_voice(hub, tmp_path, body, width=1280):
    script = PRELUDE + HELPERS + "\nawait ready();\nconst out = {};\n" + body + "\nreturn out;\n"
    return _drive(hub, tmp_path, script, width=width)


def test_every_row_is_there_with_its_control_value_and_words(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
out.title = text($("#set-voice .group__title"));
out.labels = [...document.querySelectorAll("#set-voice .field__label, #set-voice .toggle__label")].map(text);
out.speak = { checked: form().elements["station.speech"].checked, role: form().elements["station.speech"].getAttribute("role") };
out.voice = { value: form().elements.voice_choice.value, options: [...form().elements.voice_choice.options].map((o) => [o.value, o.text]) };
out.speaker = { shown: shown(field("station.voice_speaker")), value: form().elements["station.voice_speaker"].value, hint: text($(".field__hint", field("station.voice_speaker"))) };
const range = (n) => { const c = form().elements[n]; return { type: c.type, min: c.min, max: c.max, step: c.step, value: c.value, readout: text($(".field__unit", c.closest(".field"))) }; };
out.speed = range("station.voice_speed");
out.volume = range("station.speaker_volume");
out.test = { text: text(testBtn()), save: text($("#set-voice .group__foot [type=submit]")), result: $("#set-voice .voice__test .result[data-state]") !== null };
set("station.voice_speed", "150");
set("station.speaker_volume", "20");
out.moved = { speed: text($(".field__unit", field("station.voice_speed"))), volume: text($(".field__unit", field("station.speaker_volume"))) };
""")
    assert page["title"] == "Weigh Station voice"
    assert page["labels"] == ["Speak", "Voice", "Speaker", "Speed", "Volume"]
    assert page["speak"] == {"checked": True, "role": "switch"}
    assert page["voice"] == {"value": "many", "options": [["many", "Piper, many speakers"], ["one", "Piper, one clear speaker"], ["espeak", "espeak-ng, robotic, no download"]]}
    assert page["speaker"] == {"shown": True, "value": "0", "hint": "This voice has 904 speakers. Try a few with Test."}
    assert page["speed"] == {"type": "range", "min": "50", "max": "200", "step": "10", "value": "100", "readout": "100 %"}
    assert page["volume"] == {"type": "range", "min": "0", "max": "100", "step": "1", "value": "50", "readout": "50"}
    assert page["test"] == {"text": "Test", "save": "Save voice", "result": False}
    assert page["moved"] == {"speed": "150 %", "volume": "20"}


def test_the_speaker_row_shows_for_the_many_speaker_voice_only_and_an_unsaved_voice_makes_the_button_save_then_test(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
const sel = () => ({ speaker: shown(field("station.voice_speaker")), button: text(testBtn()), dirty: form().dataset.state || null });
out.start = sel();
set("voice_choice", "one");
out.one = sel();
set("voice_choice", "espeak");
out.espeak = sel();
set("voice_choice", "many");
out.back = sel();
""")
    assert page["start"] == {"speaker": True, "button": "Test", "dirty": None}
    assert page["one"] == {"speaker": False, "button": "Save, then test", "dirty": "dirty"}
    assert page["espeak"] == {"speaker": False, "button": "Save, then test", "dirty": "dirty"}
    assert page["back"] == {"speaker": True, "button": "Test", "dirty": None}


def test_a_save_sends_speak_speaker_speed_and_volume_in_one_request_as_numbers_and_the_voice_as_its_own(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
form().elements["station.speech"].click();
set("station.voice_speaker", "412");
set("station.voice_speed", "70");
set("station.speaker_volume", "80");
form().requestSubmit();
await until(() => calls.length === 1, "the first save");
out.first = calls.slice();
await sleep(400);
set("voice_choice", "espeak");
form().requestSubmit();
await until(() => calls.length === 2, "the voice save");
out.second = calls.slice(1);
await sleep(300);
set("voice_choice", "one");
form().requestSubmit();
await until(() => calls.length === 3, "the second voice save");
out.third = calls.slice(2);
""")
    assert page["first"] == [["save", {"station.speech": False, "station.voice_speaker": 412, "station.voice_speed": 70, "station.speaker_volume": 80}]]
    assert page["second"] == [["save", {"station.tts": "espeak"}]]
    assert page["third"] == [["save", {"station.tts": "auto", "station.voice": "en_US-ljspeech-medium"}]]


def test_test_sends_the_values_as_the_form_has_them_and_says_played_and_each_refusal_in_its_words(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
let last = null;
const press = async (fresh) => {
  testBtn().click();
  await until(() => calls.length === fresh && resultText() !== last && resultText() && !$("#set-voice .voice__test .result[data-state=run]"), "a test result");
  return (last = resultText());
};
set("station.voice_speaker", "7");
set("station.voice_speed", "130");
set("station.speaker_volume", "35");
out.ok = await press(1);
out.sent = calls[0];
out.unsaved = form().dataset.state;                                     // Test saved nothing
for (const [n, status, data] of [[2, 409, { error: "busy", code: "busy" }], [3, 409, { error: "off", code: "off" }], [4, 502, { error: "x", code: "no_answer" }]]) {
  plan.test = [status, data];
  out["r" + n] = await press(n);
}
""")
    assert page["ok"] == "Played on the Weigh Station." and page["unsaved"] == "dirty"
    assert page["sent"] == ["test", {"speaker": 7, "speed": 130, "volume": 35}]
    assert page["r2"] == "The Weigh Station is in use. Try again when it is idle."
    assert page["r3"] == "Speaking is switched off."
    assert page["r4"] == "The Weigh Station did not answer."


def test_test_for_the_one_speaker_voice_carries_no_speaker_and_with_an_unsaved_voice_saves_first(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
set("voice_choice", "one");
set("station.speaker_volume", "65");
testBtn().click();
await until(() => calls.length === 3 && resultText() === "Played on the Weigh Station.", "the saves and the test");
out.calls = calls.slice();
out.after = { choice: form().elements.voice_choice.value, dirty: form().dataset.state || null, button: text(testBtn()) };
""")
    assert page["calls"] == [["save", {"station.speaker_volume": 65}], ["save", {"station.tts": "auto", "station.voice": "en_US-ljspeech-medium"}],
                             ["test", {"speed": 100, "volume": 65}]]
    assert page["after"] == {"choice": "one", "dirty": None, "button": "Test"}


def test_a_bad_speaker_number_stops_test_and_a_failed_save_stops_the_test_after_it(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
set("station.voice_speaker", "904");
testBtn().click();
await sleep(300);
out.bad = { calls: calls.length, error: text($(".field__error", field("station.voice_speaker"))) };
set("station.voice_speaker", "5");
set("voice_choice", "espeak");
const was = api;
api = async (path, body) => { if (path === "settings/general" && body && body.values) { calls.push(["refused", body.values]); throw hubError(400, { error: "no voice", field: "station.tts" }); } return was(path, body); };
testBtn().click();
await until(() => calls.length === 1, "the refused save");
await sleep(500);
out.refused = { calls: calls.map((c) => c[0]), dirty: form().dataset.state };
""")
    assert page["bad"] == {"calls": 0, "error": "Enter a number from 0 to 903."}
    assert page["refused"] == {"calls": ["refused"], "dirty": "dirty"}


def test_a_weigh_station_on_another_computer_shows_where_the_voice_is_chosen_and_keeps_the_other_rows(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
plan.remote = true;
await redraw();
out.line = text($("#set-voice .voice__remote"));
out.voice_control = form().elements.voice_choice === undefined;
out.labels = [...document.querySelectorAll("#set-voice .field__label, #set-voice .toggle__label")].map(text);
out.speaker = shown(field("station.voice_speaker"));
set("station.speaker_volume", "10");
testBtn().click();
await until(() => calls.length === 1 && resultText(), "the test");
out.test = { text: text(testBtn()), sent: calls[0], result: resultText() };
""")
    assert page["line"] == "Chosen on the Weigh Station's own computer: spoolwarden config."
    assert page["voice_control"] is True
    assert page["labels"] == ["Speak", "Speaker", "Speed", "Volume"] and page["speaker"] is True
    assert page["test"] == {"text": "Test", "sent": ["test", {"speaker": 0, "speed": 100, "volume": 10}], "result": "Played on the Weigh Station."}


def test_the_group_is_laid_out_without_sideways_scrolling_on_a_phone(hub, tmp_path):
    page = _drive_voice(hub, tmp_path, r"""
out.wide = document.documentElement.scrollWidth <= document.documentElement.clientWidth;
out.slider = shown(form().elements["station.voice_speed"]);
""", width=390)
    assert page == {"wide": True, "slider": True}
