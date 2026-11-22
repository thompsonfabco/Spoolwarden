// The Settings section's Weigh Station voice group: Speak, Voice, Speaker, Speed, Volume
// and Test. No Save button: each row applies as it is changed (a slider when it is let go). Loaded after setup_settings.js (setGroup,
// sbox, offNote, paintList, setSeen, followApply) and setup.js. It is the pattern of
// setup_alerts.js: voiceGroup(data) is the group's builder, and the group's row in SET_GROUPS names it. The data is the read of
// settings/general for the Voice row only (`values` station.tts and station.voice, applied through the settings route) and
// `station_on`. Speak, Speaker, Speed and Volume live in voice.xml: the group reads them, `remote` and `voice` from
// the view read `G station/voice` (no code: looking needs nothing, so a locked page shows the values) and saves each changed
// one with `P station/voice` (code); on a locked page a change or Test asks for the code in place, as every group does, and goes back if it is not given; a value out of range is refused by the Hub, and the control goes back. Test calls `P station/voice/test` (code) with the values as they stand in the form. Everything
// from the Hub goes in through textContent.
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
  settings_voice_speed: "Speed",
  settings_voice_speed_unit: "%",
  settings_voice_volume: "Volume",
  settings_voice_test: "Test",
  settings_voice_restarts: "The Weigh Station restarts when the voice changes.",
  settings_voice_test_ok: "Played on the Weigh Station.",
  settings_voice_test_no_answer: "The Weigh Station did not answer.",
  settings_voice_test_busy: "The Weigh Station is in use. Try again when it is idle.",
  settings_voice_test_off: "Speaking is switched off.",
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
const SLIDING = "input";                                         // the event a slider moves with
const VOICE_SPEAKERS = 903;                                      // the many-speaker voice has the speakers 0 to 903
const voiceChoiceOf = (v) => (v["station.tts"] === "espeak" ? "espeak" : v["station.voice"] === "en_US-ljspeech-medium" ? "one" : "many");
const voiceNumber = (v, key, dflt) => (v[key] == null || v[key] === "" || Number.isNaN(Number(v[key])) ? dflt : Number(v[key]));
// the speakers the answer's `speaker_range` [first, last] counts; the many-speaker voice's own when the answer has none
const voiceSpeakerCount = (a) => (Array.isArray(a.speaker_range) ? a.speaker_range[1] - a.speaker_range[0] + 1 : VOICE_SPEAKERS + 1);
const voiceSpeaker = (v) => (/^\d+$/.test(String(v).trim()) && +v >= 0 && +v <= VOICE_SPEAKERS ? null : SW.t("field_error_range", { min: 0, max: VOICE_SPEAKERS }));


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
// The head and a body that fills itself once from the view read `G station/voice`, locked or not; the controls stay enabled while
// Locked: a change or Test asks for the code in place (needCode), then applies.
function voiceGroup({ g }) {
  if (!g.station_on) return setGroup("voice", { desc: SW.t("settings_voice_desc"), body: [offNote("settings_voice_off")] });
  const body = h("div", { class: "voice__rows" });
  const el = setGroup("voice", { desc: SW.t("settings_voice_desc"), body: [body] });
  later(api("station/voice").then((answer) => {
    body.replaceChildren(...(answer.station === false ? [offNote("settings_voice_off")] : voiceRows(g.values || {}, answer)));
  }).catch((e) => say(body, e.message, false)));
  return el;
}

// the rows, from the settings' Voice values (`v`) and the answer of `G station/voice`: speech, voice, speaker, speed, volume, remote;
// the Voice row shows the choice the settings values give, else the one the answer's voice name gives
function voiceRows(v, a) {
  const remote = !!a.remote, box = sbox();
  const saved = { choice: voiceChoiceOf({ "station.voice": a.voice, ...v }), speaker: voiceNumber(a, "speaker", 0), speed: voiceNumber(a, "speed", 100), volume: voiceNumber(a, "volume", 50) };
  // one write of the Voice row through settings/general; `back` puts the control back when it was refused or the code was not given
  const put = (values, back, after) => needCode(async () => {
    working(box, SW.t("common_saving"));
    try { await api("settings/general", { values, code: theCode() }); } catch (e) {
      back();
      if (e.locked) { SW.result(box, null); throw e; }
      say(box, hubText(e.message), false);
      return;
    }
    if (setSeen.g && setSeen.g.values) Object.assign(setSeen.g.values, values);
    say(box, SW.t("common_saved"), true);
    if (after) after();
    paintList();
  }, back);
  // one write of a value in voice.xml, only the changed one; the Hub refuses a value out of range and `back` puts the control back
  const putVoice = (values, back, after) => needCode(async () => {
    working(box, SW.t("common_saving"));
    try { await api("station/voice", { ...values, code: theCode() }); } catch (e) {
      back();
      if (e.locked) { SW.result(box, null); throw e; }
      say(box, hubText(e.message), false);
      return;
    }
    say(box, SW.t("common_saved"), true);
    if (after) after();
  }, back);

  const speak = SW.toggle({ label: voT("settings_voice_speak"), checked: a.speech !== false,
    apply: async (on) => {
      try { await api("station/voice", { speech: on, code: theCode() }); } catch (e) { throw Object.assign(e, { text: () => hubText(e.message) }); }
      return SW.t("common_saved");
    } });

  // the Voice row: a select of three, or on a Weigh Station on another computer the line that says where it is chosen
  const pick = remote ? null : SW.field({ row: true, type: "select", label: SW.t("settings_voice_label"), value: saved.choice, hint: voT("settings_voice_restarts"),
    options: VOICE_CHOICES.map(([value, key]) => ({ value, label: voT(key) })) });
  const remoteLine = remote ? h("p", { class: "voice__remote", textContent: voT("settings_voice_remote") }) : null;

  const speaker = SW.field({ row: true, type: "number", mono: true, label: voT("settings_voice_speaker"), hint: SW.t("voice_speaker_hint", { count: voiceSpeakerCount(a) }), value: String(saved.speaker) });
  speaker.classList.add("voice__speaker");
  const slider = (key, min, max, step, value, unit) => {
    const f = SW.field({ row: true, type: "range", mono: true, label: voT(key), value: String(value), unit: unit || String(value), attrs: { min, max, step } });
    f.classList.add("voice__slider");
    const out = f.querySelector(".field__unit");
    f._control.addEventListener(SLIDING, () => { out.textContent = f._control.value + (unit ? " " + unit : ""); });
    f._show = (n) => { f._control.value = String(n); out.textContent = n + (unit ? " " + unit : ""); };
    f._show(value);
    return f;
  };
  const speed = slider("settings_voice_speed", 50, 200, 10, saved.speed, voT("settings_voice_speed_unit"));
  const volume = slider("settings_voice_volume", 0, 100, 1, saved.volume, "");

  const test = SW.btn(voT("settings_voice_test"), { action: "voice-test", run: () => press() });
  const rows = [speak, pick, remoteLine, speaker, speed, volume, h("div", { class: "voice__test" }, test, box)].filter(Boolean);

  const nowChoice = () => (pick ? pick._control.value : saved.choice);
  const paint = () => { speaker.hidden = nowChoice() !== "many"; };
  paint();

  // each row applies when it changes; a refused one goes back to what was kept
  if (pick) pick._control.addEventListener("change", () => {
    const value = pick._control.value, was = saved.choice;
    paint();
    put(VOICE_CHOICES.find((c) => c[0] === value)[2], () => { pick._control.value = was; paint(); }, () => { saved.choice = value; followApply(true); });
  });
  speaker._control.addEventListener("change", () => {
    const bad = voiceSpeaker(speaker._control.value);
    SW.fieldError(speaker, bad);
    if (bad) return;
    const n = Number(speaker._control.value), was = saved.speaker;
    if (n === was) return;
    putVoice({ speaker: n }, () => { speaker._control.value = String(was); SW.fieldError(speaker, null); }, () => { saved.speaker = n; });
  });
  for (const [f, name] of [[speed, "speed"], [volume, "volume"]]) {
    f._control.addEventListener("change", () => {                   // let go
      const n = Number(f._control.value), was = saved[name];
      if (n === was) return;
      putVoice({ [name]: n }, () => f._show(was), () => { saved[name] = n; });
    });
  }

  // Test plays with the values on the page; right after a Voice change the Weigh Station restarts and does not answer until it is back
  let testing = false;
  async function press() {
    if (testing) return;
    const speakerBad = !speaker.hidden && voiceSpeaker(speaker._control.value);
    if (speakerBad) { SW.fieldError(speaker, speakerBad); speaker._control.focus(); return; }
    testing = true;
    try {
      const sent = { speed: Number(speed._control.value), volume: Number(volume._control.value) };
      if (nowChoice() === "many") sent.speaker = Number(speaker._control.value);
      await needCode(async () => {
        working(box, SW.t("common_testing"));
        try {
          await api("station/voice/test", { ...sent, code: theCode() });
          say(box, voT("settings_voice_test_ok"), true);
        } catch (e) {
          if (e.locked) { SW.result(box, null); throw e; }
          const [kind, text] = voiceTestText(e);
          say(box, text, kind);
        }
      });
    } finally { testing = false; }
  }
  return rows;
}
