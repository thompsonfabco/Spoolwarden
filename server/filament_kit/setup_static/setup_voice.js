// The Settings section's Weigh Station voice group (DECISIONS R88 2.1 to 2.4; 03 10.10): Speak, Voice, Speaker, Speed, Volume and Test.
// Loaded after setup_settings.js (setGroup, wireGroup, sender, setRefusal, wrapOf, sbox) and setup.js. It is the pattern of
// setup_alerts.js: voiceGroup(data) is the group's builder, and the group's row in SET_GROUPS names it. The data is the read of
// settings/general (`values`, `station_on`, and `station_remote` for a Weigh Station on another computer; a stub until VO2), and
// Test calls the page route `P station/voice/test` (code) with the values as they stand in the form. Everything from the Hub goes
// in through textContent.
"use strict";

// ----- the words -----
// Texts the catalogue does not have yet (only RG1 opens it): the catalogue's own wins once the key is there.
const VOICE_STUB = {
  settings_voice_speak: "Speak",
  settings_voice_choice_many: "Piper, many speakers",
  settings_voice_choice_one: "Piper, one clear speaker",
  settings_voice_choice_espeak: "espeak-ng, robotic, no download",
  settings_voice_remote: "Chosen on the Weigh Station's own computer: spoolwarden config.",
  settings_voice_speaker: "Speaker",
  settings_voice_speaker_hint: "This voice has 904 speakers. Try a few with Test.",
  settings_voice_speed: "Speed",
  settings_voice_speed_unit: "%",
  settings_voice_volume: "Volume",
  settings_voice_test: "Test",
  settings_voice_save_test: "Save, then test",
  settings_voice_test_ok: "Played on the Weigh Station.",
  settings_voice_test_no_answer: "The Weigh Station did not answer.",
  settings_voice_test_busy: "The Weigh Station is in use. Try again when it is idle.",
  settings_voice_test_off: "Speaking is switched off.",
  settings_voice_save: "Save voice",
};
const voT = (key, vars) => {
  const got = SW.t(key, vars);
  if (got !== key || !Object.hasOwn(VOICE_STUB, key)) return got;
  return VOICE_STUB[key].replace(/\{(\w+)\}/g, (_, k) => (vars && vars[k] !== undefined ? vars[k] : `{${k}}`));
};

// ----- the choices of the Voice row: what each one writes -----
// "many" is the default (libritts_r, 904 speakers); "one" is ljspeech; "espeak" writes `tts` only and leaves the voice file as it was
const VOICE_CHOICES = [
  ["many", "settings_voice_choice_many", { "station.tts": "auto", "station.voice": "en_US-libritts_r-medium" }],
  ["one", "settings_voice_choice_one", { "station.tts": "auto", "station.voice": "en_US-ljspeech-medium" }],
  ["espeak", "settings_voice_choice_espeak", { "station.tts": "espeak" }],
];
const VOICE_SPEAKERS = 903;                                      // the many-speaker voice has the speakers 0 to 903
const voiceChoiceOf = (v) => (v["station.tts"] === "espeak" ? "espeak" : v["station.voice"] === "en_US-ljspeech-medium" ? "one" : "many");
const voiceNumber = (v, key, dflt) => (v[key] == null || v[key] === "" || Number.isNaN(Number(v[key])) ? dflt : Number(v[key]));
const voiceSpeaker = (v) => (/^\d+$/.test(String(v).trim()) && +v >= 0 && +v <= VOICE_SPEAKERS ? null : SW.t("field_error_range", { min: 0, max: VOICE_SPEAKERS }));

// the result slot of the latest drawn group: a save redraws the group, and a result goes where the group now is
let voiceBox = null;

// a refusal of the Test route in words: by its code (busy, off, no_answer), else by the status, else the Hub's own sentence
function voiceTestText(e) {
  if (e.unreachable) return [false, SW.t("common_hub_no_answer")];
  const code = e.data && e.data.code;
  if (code === "busy") return ["warn", voT("settings_voice_test_busy")];
  if (code === "off") return ["warn", voT("settings_voice_test_off")];
  if (code === "no_answer" || e.status === 502) return [false, voT("settings_voice_test_no_answer")];
  return [false, hubText(e.message)];
}

// ----- the group -----
function voiceGroup({ g }) {
  if (!g.station_on) return setGroup("voice", { desc: SW.t("settings_voice_desc"), body: [offNote("settings_voice_off")] });
  const v = g.values || {}, remote = !!g.station_remote, choice = voiceChoiceOf(v);
  const speak = SW.toggle({ label: voT("settings_voice_speak"), checked: v["station.speech"] !== false && v["station.speech"] !== "false" });
  speak._control.name = "station.speech";

  // the Voice row: a select of three, or on a Weigh Station on another computer the line that says where it is chosen
  const pick = remote ? null : SW.field({ row: true, type: "select", name: "voice_choice", label: SW.t("settings_voice_label"), value: choice, effect: "applies",
    options: VOICE_CHOICES.map(([value, key]) => ({ value, label: voT(key) })) });
  const remoteLine = remote ? h("p", { class: "voice__remote", textContent: voT("settings_voice_remote") }) : null;

  const speaker = SW.field({ row: true, type: "number", name: "station.voice_speaker", mono: true, label: voT("settings_voice_speaker"), hint: voT("settings_voice_speaker_hint"),
    value: String(voiceNumber(v, "station.voice_speaker", 0)), check: (x) => (speaker.hidden ? null : voiceSpeaker(x)) });
  speaker.classList.add("voice__speaker");
  const slider = (name, key, min, max, step, dflt, unit) => {
    const f = SW.field({ row: true, type: "range", name, mono: true, label: voT(key), value: String(voiceNumber(v, name, dflt)), unit: unit || String(dflt), attrs: { min, max, step } });
    f.classList.add("voice__slider");
    const out = f.querySelector(".field__unit"), paint = () => { out.textContent = f._control.value + (unit ? " " + unit : ""); };
    f._control.addEventListener("input", paint);
    f._paint = paint;
    paint();
    return f;
  };
  const speedUnit = voT("settings_voice_speed_unit");
  const speed = slider("station.voice_speed", "settings_voice_speed", 50, 200, 10, 100, speedUnit);
  const volume = slider("station.speaker_volume", "settings_voice_volume", 0, 100, 1, 50, "");

  const test = SW.btn(voT("settings_voice_test"), { action: "voice-test", run: () => press() }), box = sbox();
  const el = setGroup("voice", { desc: SW.t("settings_voice_desc"), save: voT("settings_voice_save"),
    body: [speak, pick, remoteLine, speaker, speed, volume, h("div", { class: "voice__test" }, test, box)].filter(Boolean) });
  voiceBox = box;

  // what the form shows now: the Speaker row for the many-speaker voice only, the Test button for an unsaved Voice, the readouts
  const form = () => el.elements;
  const nowChoice = () => (pick ? pick._control.value : choice);
  const unsavedVoice = () => nowChoice() !== choice;
  const paint = () => {
    speaker.hidden = nowChoice() !== "many";
    test.textContent = unsavedVoice() ? voT("settings_voice_save_test") : voT("settings_voice_test");
    speed._paint();
    volume._paint();
  };
  for (const ev of ["input", "change", "sw:dirty"]) el.addEventListener(ev, paint);
  paint();

  // the keys of the form: Speak, Speaker, Speed and Volume go in one request of settings/general; the Voice is its own request
  // (it writes `station.tts` and `station.voice`, and applies: a download and a restart of the Weigh Station)
  const o = { send: null };
  const own = { voice_choice: async (value) => {
    const values = VOICE_CHOICES.find((c) => c[0] === value)[2];
    try { return await api("settings/general", { values, code: theCode() }); } catch (e) { throw setRefusal(el, e); }
  } };
  o.send = sender(form, () => o.group, own, { "station.voice_speaker": Number, "station.voice_speed": Number, "station.speaker_volume": Number });
  wireGroup(el, "voice", o);

  // Test says what the form says (saved or not); an unsaved Voice is saved first, and the test follows when the save went through
  let testing = false;
  const body = () => {
    const f = form(), out = { speed: Number(f["station.voice_speed"].value), volume: Number(f["station.speaker_volume"].value) };
    if (nowChoice() === "many") out.speaker = Number(f["station.voice_speaker"].value);
    return out;
  };
  const redrawn = async (was) => { for (let i = 0; i < 50 && voiceBox === was; i++) await new Promise((done) => setTimeout(done, 100)); };
  async function press() {
    if (testing) return;
    if (speaker._check && !speaker.hidden) { const bad = speaker._check(); SW.fieldError(speaker, bad); if (bad) { speaker._control.focus(); return; } }
    testing = true;
    try {
      const sent = body();
      if (unsavedVoice()) {
        const was = voiceBox;
        await o.group.save();
        if (o.group.dirty()) return;                             // the save did not go through: it said why, and nothing is tested
        await redrawn(was);                                      // the group is drawn again from what the Hub kept
      }
      await needCode(async () => {
        working(voiceBox, SW.t("common_testing"));
        try {
          await api("station/voice/test", { ...sent, code: theCode() });
          say(voiceBox, voT("settings_voice_test_ok"), true);
        } catch (e) {
          if (e.locked) { SW.result(voiceBox, null); throw e; }
          const [kind, text] = voiceTestText(e);
          say(voiceBox, text, kind);
        }
      });
    } finally { testing = false; }
  }
  return el;
}
