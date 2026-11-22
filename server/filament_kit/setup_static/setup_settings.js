// The Settings section (03 10): nine groups in a fixed order, each a form group (02 5.11) with one Save that sends its
// changed keys. Loaded after setup.js; everything from the server goes in through textContent, as there.
// The section is one region. Its groups read their data once when it opens (settings/general is read once for all of
// them), and a save reads only its own group again, so what is typed in another group stays. The Backups group is in
// setup_backup.js.
"use strict";

// id (the address #settings/<id>), catalogue key of the name, icon, builder, the read it needs
const SET_GROUPS = [
  ["appearance", "settings_group_appearance", "half-circle", groupAppearance, null],
  ["general", "settings_group_general", "settings", groupGeneral, "general"],
  ["storage", "settings_group_storage", "spool", (d) => storageGroup(d), "storage"],
  ALERTS_GROUP,                                                  // setup_alerts.js: fourth (03 10.1)
  ["label-reader", "settings_group_label_reader", "camera", groupLabelReader, "general"],
  ["backups", "settings_group_backups", "download", (d) => backupsGroup(d), null],
  ["network", "settings_group_network", "plug", groupNetwork, "general"],
  ["passphrase", "settings_group_passphrase", "lock", groupPassphrase, null],
  ["catalog", "settings_group_catalog", "tag", groupCatalog, "library"],
  ["voice", "settings_group_voice", "scale", (d) => voiceGroup(d), "general"],   // setup_voice.js
];
const SET_KEY = Object.fromEntries(SET_GROUPS.map((g) => [g[0], g[1]]));
const SET_ICON = Object.fromEntries(SET_GROUPS.map((g) => [g[0], g[2]]));

const SET_STUB = {
  settings_reader_cert_fp: "Certificate fingerprint",
  settings_reader_cert_changed: "The Label Reader's certificate changed.",
  settings_reader_cert_old: "Old fingerprint",
  settings_reader_cert_new: "New fingerprint",
  settings_reader_trust: "Trust the new certificate",
  settings_reader_trust_title: "Trust the new certificate of the Label Reader?",
  settings_reader_trust_body: "The Hub accepts the certificate the Label Reader at {address} shows now, in place of the one it saved. Do this only after the reader was reinstalled or moved.",
  settings_network_port_hint: "443, or 1024 to 65535.",
  settings_network_port_empty: "Enter a port number.",
  settings_catalog_other: "Other",
  settings_catalog_other_material: "Material name",
  settings_catalog_choose_material: "Choose…",
  settings_catalog_more: "More (optional)",
  settings_catalog_nozzle_min: "Nozzle min (°C)",
  settings_catalog_nozzle_max: "Nozzle max (°C)",
  settings_catalog_bed_min: "Bed min (°C)",
  settings_catalog_bed_max: "Bed max (°C)",
  settings_catalog_mvs: "Max volumetric speed (mm³/s)",
  settings_catalog_added: "Added. Slicer presets are rebuilt in a minute.",
  settings_catalog_error_brand: "Enter the brand.",
  settings_catalog_error_name: "Enter the filament name.",
  settings_catalog_error_material: "Choose a material.",
  settings_catalog_error_other_material: "Enter the material name.",
  settings_catalog_see_entries: "See custom entries",
  settings_catalog_entries_title: "Custom catalog entries",
  settings_catalog_entries_empty: "You have no entries of your own yet.",
  settings_catalog_col_added: "Added",
  settings_catalog_remove_title: "Remove {brand} {name} from your own entries?",
  settings_catalog_remove_body: "Slicer presets are rebuilt.",
  settings_catalog_removed: "Removed.",
  settings_catalog_entries_error: "Could not load custom entries.",
  settings_language_title: "Language",
  settings_language_hub_hint: "The language of the Hub's own texts and of devices set to Automatic.",
  settings_language_done: "Language: {name}.",
  settings_language_machine: "{name} (machine-translated)",
  settings_language_complete: "{percent} % translated",
  settings_language_more: "Get more languages",
  settings_language_install: "Install",
  settings_language_installed: "Installed",
  settings_language_remove: "Remove",
  settings_language_installed_done: "{name} installed.",
  settings_language_removed_done: "{name} removed.",
  settings_language_not_installed: "{name} was not installed: {reason}",
  settings_language_is_hub: "{name} is the Hub's language. Choose another one for the Hub first.",
  settings_language_none_more: "This release has no other languages.",
  settings_language_error: "Could not read the list of languages. {text}",
  settings_language_demo: "Not in the demo.",
  settings_language_device: "Language",
  settings_language_auto: "Automatic",
  settings_language_device_hint: "Automatic uses this browser's language if the Hub has it, otherwise the Hub's language.",
  settings_demo_title: "Demo",
  settings_demo_hint: "Look around the whole product on made-up data. Only on a Hub with no spools and no printers.",
  settings_demo_try: "Try the demo",
  settings_demo_leave: "Leave demo",
  settings_demo_on: "The demo is on.",
  settings_demo_off: "The demo is off.",
};
const setT = (key, vars) => {
  const got = SW.t(key, vars);
  if (got !== key || !Object.hasOwn(SET_STUB, key)) return got;
  return SET_STUB[key].replace(/\{(\w+)\}/g, (_, k) => (vars && vars[k] !== undefined ? vars[k] : `{${k}}`));
};

let setEl = null;                 // the section's region, kept while Settings stays open
let setGeneral = null;            // the one read of settings/general
const setSeen = { g: null, lib: null, st: null, al: null };
let setHashMove = false;          // the address moved inside Settings: the groups are not read again
let setScrollOn = false;
const FRESH_MS = 5000;

const readGeneral = (fresh) => (!fresh && setGeneral) || (setGeneral = api("settings/general").catch((e) => { setGeneral = null; throw e; }));
const wrapOf = (id) => (setEl ? setEl.querySelector(`.settings__group[data-id="${id}"]`) : null);
const dirtyIn = (el) => !!el.querySelector('form[data-state="dirty"]');
const settingsGroupName = () => (setGroupId() ? SW.t(SET_KEY[setGroupId()]) : "");
function setGroupId() {
  let id = "";
  try { id = decodeURIComponent(location.hash.slice(1).split("/")[1] || ""); } catch { /* a broken address opens the list */ }
  return SET_KEY[id] ? id : "";
}

// ----- small builders -----
// a result slot of a control that is not part of the group's Save
const sbox = () => h("p", { class: "result", role: "status" });
const badge = (nameKey, word, o) => h("span", { class: "settings__badge" }, SW.t(nameKey), " ", SW.status(word, { small: true, ...o }));
const offNote = (key) => SW.banner({ kind: "info", inline: true, text: SW.t(key),
  actions: [h("a", { class: "btn btn--primary", href: "#features", textContent: SW.t("common_go_to_features") })] });
const heading = (key) => h("h3", { class: "settings__heading" }, SW.t(key));
const whole = (min, max, key) => (v) => (/^\d+$/.test(v.trim()) && +v >= min && (max == null || +v <= max) ? null : SW.t(key));
const plainField = (o) => SW.field({ row: true, ...o, attrs: { ...(o.optional === false ? {} : { "aria-required": "false" }), ...o.attrs } });
const requiredField = (o) => SW.field({ row: true, ...o });
const megabytes = (n) => (n < 1048576 ? Math.max(1, Math.ceil(n / 1024)) + " KB" : (n / 1048576).toFixed(1) + " MB");
const codeBlock = (text, name) => h("div", { class: "code" },
  h("div", { class: "code__bar" }, h("span", { class: "result", role: "status" }), SW.btn(SW.t("common_copy"), { cls: "btn--ghost", icon: "copy", action: "copy" })),
  h("pre", { class: "code__text", tabindex: "0", "aria-label": name, textContent: text }));

// a group: head (icon, title, description, badges), body, and a foot with the result and the Save when it has one
function setGroup(id, o) {
  const tid = "set-" + id + "-title", saves = !!o.save;
  const dirty = SW.status("needs_look", { small: true, cls: "settings__dirty", text: SW.t("common_unsaved_changes") });
  dirty.hidden = true;
  const head = h("div", { class: "group__head" }, SW.icon(SET_ICON[id], "icon--s"),
    h("div", {}, h("h2", { class: "group__title", id: tid, tabindex: "-1", textContent: SW.t(SET_KEY[id]) }),
      o.desc ? h("p", { class: "group__desc", textContent: o.desc }) : null),
    h("div", { class: "settings__badges" }, ...(o.badges || []), dirty));
  const foot = saves ? h("div", { class: "group__foot" }, sbox(), h("button", { class: "btn btn--primary", type: "submit", textContent: o.save })) : null;
  return h(saves ? "form" : "section", { class: "group", id: "set-" + id, "aria-labelledby": tid, novalidate: saves ? true : undefined },
    head, h("div", { class: "group__body" }, ...o.body), foot);
}

// ----- saving (02 5.11, N2) -----
// The keys that go through settings/general are sent together, one request for the keys of one effect (none, then the
// ones that restart the Hub, as the Hub page's order is: plain, applies, restarts); `own` names the keys with an
// endpoint of their own. The shared form group calls this once per changed key, in its order.
const effectOf = (form, key) => { const c = form.elements[key]; return (c && c.dataset && c.dataset.effect) || ""; };
function fieldValue(form, key) {
  const c = form.elements[key];
  return !c ? "" : c.type === "checkbox" ? c.checked : c.value;
}
function setRefusal(form, e) {
  if (e.locked) return e;
  const errs = e.data && e.data.errors;
  if (errs) {
    const keys = Object.keys(errs), say1 = (k) => hubText(errs[k].startsWith(k + " ") ? errs[k].slice(k.length + 1) : errs[k]);
    for (const k of keys) { const c = form.elements[k]; if (c && c.closest && c.closest(".field")) SW.fieldError(c.closest(".field"), say1(k)); }
    e.field = keys[0];
    e.text = () => say1(keys[0]);
  } else {
    e.field = e.data && e.data.field;
    e.text = () => hubText(e.message);
  }
  return e;
}
function sender(getForm, getGroup, own, conv = {}) {
  let plan = {};
  const send = async (key, value) => {
    if (own[key]) return own[key](value);
    const form = getForm(), fx = effectOf(form, key);
    if (!plan[fx]) {
      const keys = getGroup().changed().filter((k) => !own[k] && effectOf(form, k) === fx), values = {};
      for (const k of keys) values[k] = conv[k] ? conv[k](fieldValue(form, k)) : fieldValue(form, k);
      plan[fx] = { left: keys, reply: null };
      try { plan[fx].reply = await api("settings/general", { values, code: theCode() }); } catch (e) { delete plan[fx]; throw setRefusal(form, e); }
    }
    const p = plan[fx];
    p.left = p.left.filter((k) => k !== key);
    return p.left.length ? {} : p.reply;
  };
  send.reset = () => { plan = {}; };
  return send;
}
// the form group of a form, with its sender; `before(keys)` may stop the save (a dialog); `done(text)` words the result
function wireGroup(form, id, o) {
  let keys = [];
  const g = SW.formGroup(form, { title: SW.t(SET_KEY[id]), send: o.send, onSaved: (saved) => {
    if (saved.length === keys.length) setTimeout(() => settled(form, id, o), 60);
  } });
  const save0 = g.save;
  let running = false;
  g.save = async () => {
    if (running) return;                        // a second press while a save is under way changes nothing
    running = true;
    try {
      keys = o.keys = g.changed();
      if (keys.length && o.before && !(await o.before(keys))) return;
      if (o.send.reset) o.send.reset();
      return await save0();
    } finally { running = false; }
  };
  if (o.after) o.after(g);
  o.group = g;
  return g;
}
// after a save: only this group is read again, and says what the save said
function settled(form, id, o) {
  const res = form.querySelector(".group__foot .result"), kind = res && res.dataset.state;
  const text = res && res.querySelector(".result__text") ? res.querySelector(".result__text").textContent : null;
  loadGroup(id, { quiet: true, fresh: true, result: kind && kind !== "run" && text ? [kind, o.done ? o.done(text) : text] : null });
}

// A request with the code that says its result in `box` and does not draw anything again
async function settingAct(box, path, body, done, clear) {
  await needCode(async () => {
    working(box, SW.t("common_saving"));
    let r;
    try { r = await api(path, { ...body, code: theCode() }); } catch (e) {
      if (e.locked) { SW.result(box, null); throw e; }
      say(box, hubText(e.message), false);
      return;
    }
    for (const i of clear || []) i.value = "";
    if (done) done(r); else say(box, SW.t("common_saved"), true);
  });
}
// the same behind a dialog (`q`: SW.ask's options); resolves with the reply or null
async function confirmed(q, path, body, done) {
  const out = await SW.ask({ ...q, run: async (values, { result }) => {
    let r = null;
    await needCode(async () => {
      try { r = await api(path, { ...(typeof body === "function" ? body(values) : body), code: theCode() }); } catch (e) {
        if (e.locked) throw e;
        SW.result(result, "bad", hubText(e.message));
      }
    });
    return r || false;
  } });
  if (out && done) done(out);
  return out;
}
// a background job: its strip follows it; `q` asks first
function runJob(box, name, q) {
  const started = () => { SW.result(box, null); followApply(true); };
  if (q) return confirmed(q, "job", { name }, started);
  return settingAct(box, "job", { name }, started);
}

// ----- the section -----
function settingsTab() {
  pageHead(h("p", { class: "page-head__meta", textContent: SW.t("settings_intro") }));
  if (setEl && setEl.isConnected) {                 // Settings stays open: a group that is not being edited reads again
    applyHash();
    // a group read a moment ago is as new as a read now; the others read again (a job ended, the Hub came back)
    if (!setHashMove) { setGeneral = null; for (const [id] of SET_GROUPS) if (Date.now() - (wrapOf(id)._readAt || 0) > FRESH_MS) loadGroup(id, { quiet: true }); }
    return setEl;
  }
  setGeneral = null;
  setSeen.g = setSeen.lib = setSeen.st = setSeen.al = null;
  const link = ([id, key]) => h("li", {}, h("a", { class: "subnav__link", href: "#settings/" + id, "data-id": id, on: { click: jump } },
    SW.t(key), SW.status("needs_look", { small: true, cls: "subnav__dirty", text: SW.t("common_unsaved") })));
  const row = ([id, key]) => h("li", {}, h("a", { class: "settings__row", href: "#settings/" + id, "data-id": id },
    h("span", { class: "settings__name", textContent: SW.t(key) }), h("span", { class: "settings__state" }), SW.icon("chevron", "icon--s")));
  setEl = h("div", { class: "settings" },
    h("nav", { class: "subnav", "aria-label": SW.t("settings_groups_label") }, h("ul", { class: "subnav__list" }, SET_GROUPS.map(link))),
    h("ul", { class: "settings__list" }, SET_GROUPS.map(row)),
    h("div", { class: "settings__main" },
      h("a", { class: "btn btn--ghost settings__back", href: "#settings" }, SW.icon("chevron", "icon--s"), SW.t("nav_settings")),
      h("div", { class: "settings__groups" }, SET_GROUPS.map(([id]) => h("div", { class: "settings__group", "data-id": id })))));
  for (const d of setEl.querySelectorAll(".subnav__dirty")) d.hidden = true;
  setEl.addEventListener("sw:dirty", markDirty);
  applyHash();
  paintList();
  const loads = SET_GROUPS.map(([id]) => loadGroup(id));
  later(Promise.all(loads));
  const first = setGroupId();
  Promise.all(loads).then(() => {
    const arrivedReader = first === "label-reader" && setEl && setEl.querySelector('.settings__reader[data-arrived="1"]');
    if (arrivedReader) {
      delete arrivedReader.dataset.arrived;
      arrivedReader.scrollIntoView({ behavior: "auto", block: "nearest" });
      markCurrent(first);
    } else if (first && setEl && !SW.phone()) {
      scrollToGroup(first, false, true);
    }
  });
  if (!setScrollOn) { setScrollOn = true; window.addEventListener("scroll", onScroll, { passive: true }); }
  markCurrent(first || SET_GROUPS[0][0]);
  return setEl;
}

function loadGroup(id, o = {}) {
  const wrap = wrapOf(id), def = SET_GROUPS.find((g) => g[0] === id);
  if (!wrap || (o.quiet && wrap._hold)) return Promise.resolve();
  const retry = () => loadGroup(id, { fresh: true });
  const L = o.quiet ? null : SW.loading(wrap, { thing: SW.t(def[1]), items: 3, onTry: retry, title: SW.t("settings_read_error", { reason: "" }).trim() });
  return (async () => {
    try {
      if (o.fresh) { setGeneral = null; try { S = await api("state"); } catch { /* the page keeps what it has */ } }
      const data = {};
      if (def[4] === "general") data.g = setSeen.g = await readGeneral(o.fresh);
      if (def[4] === "library") data.lib = setSeen.lib = await api("library");
      if (def[4] === "storage") data.st = setSeen.st = await loadStorage();       // setup_storage.js
      if (def[4] === "alerts") data.al = setSeen.al = await loadAlerts();         // setup_alerts.js
      if (o.quiet && (dirtyIn(wrap) || wrap._hold || !wrap.isConnected)) { paintList(); return; }
      wrap._readAt = Date.now();
      const had = wrap.contains(document.activeElement), keep = o.quiet && !o.result ? capture(wrap) : null, el = def[3](data, o);
      if (L) L.done(el); else wrap.replaceChildren(el);
      stamp(wrap);
      if (keep && keep.size) {                     // what was typed and not sent stays (the shared carry), and the group shows it as unsaved
        wrap._keep = keep;
        carry(wrap);
        const f = el.matches("form") ? el : null;
        if (f) f.dispatchEvent(new Event("change", { bubbles: true }));
      }
      if (o.result) { const r = el.querySelector(".group__foot .result"); if (r) SW.result(r, o.result[0], o.result[1]); }
      if (had) { const t1 = el.querySelector(".group__title"); if (t1) t1.focus({ preventScroll: true }); }
    } catch (e) {
      if (L) L.done();
      if (e.locked) return;
      if (!o.quiet) readFailed(wrap, "settings_read_error", e, retry);
    }
    markDirty();
    paintList();
  })();
}

// the unsaved marks of the sub-navigation and of the group titles
function markDirty() {
  if (!setEl) return;
  for (const [id] of SET_GROUPS) {
    const w = wrapOf(id), d = !!(w && dirtyIn(w)), m = setEl.querySelector(`.subnav__link[data-id="${id}"] .subnav__dirty`), t1 = w && w.querySelector(".settings__dirty");
    if (m) m.hidden = !d;
    if (t1) t1.hidden = !d;
  }
}

// the one-line state of each group, on the phone's list (03 10.1)
function listState(id) {
  const g = setSeen.g, v = g && g.values, lib = setSeen.lib;
  if (id === "appearance") return SW.t("settings_state_appearance", { mode: SW.t("appearance_" + appearanceNow()),
    theme: SW.t("theme_" + (document.documentElement.getAttribute("data-look") || "console") + "_name") });
  if (id === "passphrase") return SW.t(S.passphrase.set ? "status_set" : "status_not_set");
  if (id === "network") return S.web.public_set ? SW.t("status_set") : SW.t("settings_state_no_public");
  if (id === "backups") return S.backup && S.backup.last ? SW.t("settings_state_backups", { time: S.backup.last })
    : SW.lock.state === "locked" ? SW.t("common_unlock_to_show") : SW.t("common_none");
  if (id === "alerts") return setSeen.al ? alertsState(setSeen.al) : "";
  if (id === "storage") return setSeen.st ? SW.t("settings_state_storage", { count: setSeen.st.locations.length }) : "";
  if (id === "catalog") return lib ? (lib.source === "none" ? SW.t("settings_state_catalog_none") : String(lib.version)) : "";
  if (!g) return "";
  if (id === "general") return String(v.server_name || "");
  if (id === "label-reader") {
    if (g.ai_intake_on && g.local_intake_on) return SW.t("settings_reader_order_" + ({ auto: "auto", "local-then-cloud": "local_cloud", "cloud-then-local": "cloud_local",
      cloud: "cloud", local: "local" }[v["intake.reader"] || "auto"]));
    return g.ai_intake_on ? SW.t("settings_reader_order_cloud") : g.local_intake_on ? SW.t("settings_reader_order_local") : SW.t("settings_state_off");
  }
  if (id === "voice") return g.station_on ? SW.t(v["station.tts"] === "espeak" ? "settings_voice_espeak" : "settings_voice_piper") : SW.t("settings_state_off");
  return "";
}
function paintList() {
  if (!setEl) return;
  for (const [id] of SET_GROUPS) { const s = setEl.querySelector(`.settings__row[data-id="${id}"] .settings__state`); if (s) s.textContent = listState(id); }
}

// ----- the sub-navigation and the address (03 10.1) -----
function markCurrent(id) {
  if (!setEl) return;
  for (const a of setEl.querySelectorAll(".subnav__link, .settings__row")) { if (a.dataset.id === id) a.setAttribute("aria-current", "true"); else a.removeAttribute("aria-current"); }
}
let setScrollLock = 0;
function onScroll() {
  if (!setEl || !setEl.isConnected) { setScrollOn = false; window.removeEventListener("scroll", onScroll); return; }
  if (SW.phone() || Date.now() < setScrollLock) return;
  let cur = SET_GROUPS[0][0];
  for (const [id] of SET_GROUPS) { const w = wrapOf(id); if (w && w.getBoundingClientRect().top <= window.innerHeight / 3) cur = id; }
  if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 2) {
    const last = [...SET_GROUPS].reverse().find(([id]) => wrapOf(id));
    if (last) cur = last[0];
  }
  markCurrent(cur);
}
function scrollToGroup(id, focus, instant) {
  const w = wrapOf(id);
  if (!w) return;
  setScrollLock = Date.now() + (instant ? 100 : 600);
  w.scrollIntoView({ behavior: instant || matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
  const t1 = w.querySelector(".group__title");
  if (focus && t1) t1.focus({ preventScroll: true });
  markCurrent(id);
}
function jump(e) {
  const id = e.currentTarget.dataset.id;
  e.preventDefault();
  if (SW.phone()) { location.hash = "#settings/" + id; return; }
  history.replaceState(null, "", "#settings/" + id);
  scrollToGroup(id, true);
  if (shellView) shellView.paint(false);
}
// the phone shows the list or one group; the wide layouts show them all (the CSS decides by the width)
function applyHash() {
  const id = setGroupId();
  if (id) setEl.dataset.open = id; else delete setEl.dataset.open;
  for (const w of setEl.querySelectorAll(".settings__group")) w.toggleAttribute("data-shown", w.dataset.id === id);
  if (id) markCurrent(id);
}
window.addEventListener("hashchange", () => {
  setHashMove = true;
  setTimeout(() => { setHashMove = false; }, 0);
  if (setEl && setEl.isConnected && tabNow() === "settings") {
    const id = setGroupId();
    if (id === "label-reader" && window.__swReaderArrived) {
      window.__swReaderArrived = false;
      const box = setEl.querySelector(".settings__reader");
      if (box) {
        box.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "nearest" });
        const checkBtn = box.querySelector('[data-action="check-reader"]');
        if (checkBtn && !box.querySelector('[data-action="install-reader"]')) checkBtn.click();
      }
      markCurrent(id);
      return;
    }
    if (id && !SW.phone() && setEl.dataset.open !== id) scrollToGroup(id, true);
  }
});

// ----- Appearance (03 10.2): this device, applied at once; Theme for new devices (10.2) has its own button -----
function groupAppearance() {
  const res = sbox(), hint = h("p", { class: "field__hint", textContent: SW.t("settings_appearance_mode_hint") });
  const choice = SW.segmented({ legend: SW.t("settings_appearance_mode"), cls: "appearance", name: "appearance-device", value: appearanceNow(),
    options: APPEARANCE.map(([value, icon]) => ({ value, icon, label: SW.t("appearance_" + value) })) });
  choice.addEventListener("change", () => setTimeout(paintList, 0));
  const look = SW.themePicker({ legend: true });    // built from the root's data-look each time, so a redraw keeps the choice
  look.addEventListener("change", () => setTimeout(paintList, 0));
  return setGroup("appearance", { desc: SW.t("settings_appearance_desc"), body: [h("div", { class: "settings__choice" }, choice, hint, res),
    h("div", { class: "settings__choice" }, look, sbox()), setLangChoice(), newDevices()] });
}
// Theme for new devices (03 10.2): the Hub's own default, saved with its own button, which needs the setup code
const NEW_LOOKS = ["console", "workbench", "palette", "panes"];
function newDevices() {
  let saved = (S.appearance && S.appearance.theme) || "console";
  const box = sbox();
  const pick = SW.field({ type: "select", name: "appearance.theme", label: SW.t("appearance_theme_default_title"), hint: SW.t("settings_appearance_new_devices_hint"),
    value: saved, optional: false, options: NEW_LOOKS.map((v) => ({ value: v, label: SW.t("theme_" + v + "_name") })) });
  const save = SW.btn(SW.t("settings_appearance_save"), { cls: "btn--primary", action: "save-theme", run: () => {
    const value = pick._control.value;
    if (value === saved) return;
    return settingAct(box, "settings/general", { values: { "appearance.theme": value } }, async () => {
      saved = value;
      try { S = await api("state"); } catch { /* the page keeps what it has */ }
      sync();
      say(box, SW.t("common_saved"), true);
    });
  } });
  const sync = () => SW.disable(save, pick._control.value === saved ? SW.t("common_nothing_changed") : null);
  pick._control.addEventListener("change", () => { SW.result(box, null); sync(); });
  sync();
  return h("div", { class: "settings__choice" }, pick, h("div", { class: "actions" }, save), box);
}

// ----- General (03 10.3) -----
function groupGeneral({ g }) {
  const v = g.values;
  const fields = [requiredField({ name: "server_name", label: SW.t("settings_general_name"), value: v.server_name == null ? "" : String(v.server_name),
    check: (x) => (x.trim() ? null : SW.t("settings_general_name_required")) })];
  if (g.spoolman_external) fields.push(plainField({ name: "spoolman", label: SW.t("settings_general_spoolman"), mono: true, value: g.spoolman_url || "",
    placeholder: SW.t("settings_general_spoolman_placeholder"), hint: SW.t("settings_general_spoolman_hint"), effect: "applies" }));
  fields.push(setLangBlock(), setDemoRow());
  const el = setGroup("general", { desc: SW.t("settings_general_desc"), save: SW.t("settings_general_save"), body: fields });
  const own = {
    spoolman: async (value) => {
      const f = el.elements.spoolman.closest(".field"), was = f._hint.textContent;
      f._hint.textContent = SW.t("settings_general_spoolman_checking");
      try { await api("settings/spoolman", { url: value.trim(), code: theCode() }); } catch (e) { throw setRefusal(el, Object.assign(e, { data: { ...(e.data || {}), field: "spoolman" } })); } finally { f._hint.textContent = was; }
      return { applies: true };
    },
  };
  const o = { send: null };
  o.send = sender(() => el, () => o.group, own);
  wireGroup(el, "general", o);
  return el;
}

// ----- Language (R85 3): the Hub's languages in General, this browser's choice in Appearance, the page's `lang` -----
// Reads `languages` ({hub, languages: [{code, name, reviewed, installed, complete}], available: [...], problem?}); English is
// always there, listed here when the Hub's list leaves it out.
const SET_LANG_KEY = "spoolwarden.language";
let setLangSeen = null;
const setLangStored = () => { try { return localStorage.getItem(SET_LANG_KEY) || ""; } catch { return ""; } };
const setLangKeep = (code) => { try { if (code) localStorage.setItem(SET_LANG_KEY, code); else localStorage.removeItem(SET_LANG_KEY); } catch { /* storage refused: the choice lasts until the page closes */ } };
const setLangName = (l) => (l.reviewed === false ? setT("settings_language_machine", { name: l.name }) : l.name);
const setLangHave = (d) => [{ code: "en", name: "English", reviewed: true, installed: true, complete: 100 }, ...d.languages.filter((l) => l.installed && l.code !== "en")];
// the code the page speaks: this browser's choice if installed, else (Automatic) the browser's language if installed, else the Hub's
function setLangPick(d) {
  const have = new Set(setLangHave(d).map((l) => l.code)), own = setLangStored(), dev = String(navigator.language || "").toLowerCase().split("-")[0];
  return have.has(own) ? own : have.has(dev) ? dev : have.has(d.hub) ? d.hub : "en";
}
// reads the languages (again when `fresh`) and sets the page's `lang`; a Hub without the route leaves it as it is
async function setLangApply(fresh) {
  if (!setLangSeen || fresh) {
    try { setLangSeen = await api("languages"); } catch { return null; }
  }
  document.documentElement.lang = setLangPick(setLangSeen);
  return setLangSeen;
}
// a request with the code that says `ok` (or `bad(e)` for a refusal) in `box`; true when the Hub took it, false when it refused or the unlock dialog was cancelled
async function setLangSend(box, path, body, ok, bad) {
  let sent = false;
  await needCode(async () => {
    working(box, SW.t("common_working"));
    try { await api(path, { ...body, code: theCode() }); } catch (e) {
      if (e.locked) { SW.result(box, null); throw e; }
      say(box, bad ? bad(e) : hubText(e.message), false);
      return;
    }
    sent = true;
    say(box, ok, true);
  });
  return sent;
}
const setLangSentence = (m) => { const s = hubText(m), i = s.search(/[.!?](\s|$)/); return i < 0 ? s : s.slice(0, i + 1); };
// Settings > General: the Hub's language (a select, sent at once, with its own result); Get more languages opens the dialog below
function setLangBlock() {
  const holder = h("div", { class: "settings__choice" }), box = sbox();
  const paint = (d) => {
    const have = setLangHave(d);
    let now = have.some((l) => l.code === d.hub) ? d.hub : "en";
    const pick = SW.field({ type: "select", name: "general.language", label: setT("settings_language_title"), hint: setT("settings_language_hub_hint"),
      value: now, optional: false, options: have.map((l) => ({ value: l.code, label: setLangName(l) })) });
    pick._control.addEventListener("change", async () => {
      const code = pick._control.value, name = have.find((l) => l.code === code).name;
      if (await setLangSend(box, "settings/language", { hub: code }, setT("settings_language_done", { name }))) { now = code; d.hub = code; document.documentElement.lang = setLangPick(d); } else pick._control.value = now;
    });
    const more = SW.btn(setT("settings_language_more"), { cls: "btn--ghost", action: "language-more", run: (ev) => setLangMore(ev.currentTarget) });
    holder.replaceChildren(pick, h("div", { class: "actions" }, more), box);
  };
  paint({ hub: "en", languages: [] });
  later(setLangApply(true).then((d) => { if (d && holder.isConnected) paint(d); }));
  return holder;
}
// the dialog (a sheet on a phone) of Get more languages: the packs of this release, Install, and Remove for an installed one
function setLangMore(opener) {
  const region = h("div", { class: "settings__lang" }), box = sbox(), title = setT("settings_language_more");
  const paint = async (fresh) => {
    if (S && S.demo) { region.replaceChildren(h("p", { class: "muted", textContent: setT("settings_language_demo") })); return; }
    const L = SW.loading(region, { thing: title, items: 3, onTry: () => paint(true), title });
    let d = null, text = "";
    try { d = await setLangApply(fresh); } catch (e) { text = hubText(e.message); }
    if (d && d.problem) { text = hubText(d.problem); d = null; }
    if (!d) {
      const again = SW.btn(SW.t("common_try_again"), { cls: "btn--ghost", action: "language-again", run: () => paint(true) });
      return L.done(h("p", { class: "muted", textContent: setT("settings_language_error", { text }).trim() }), h("div", { class: "actions" }, again));
    }
    const rows = (d.available || []).filter((l) => l.code !== "en").map((l) => {
      const have = (d.languages || []).some((x) => x.code === l.code && x.installed), name = l.name, route = "languages/" + encodeURIComponent(l.code) + "/";
      const act = have ? [SW.status("ok", { small: true, text: setT("settings_language_installed") }),
        SW.btn(setT("settings_language_remove"), { cls: "btn--ghost btn--small", action: "language-remove-" + l.code, run: async () => {
          if (l.code === d.hub) return say(box, setT("settings_language_is_hub", { name }), false);
          if (await setLangSend(box, route + "remove", {}, setT("settings_language_removed_done", { name }))) await paint(true);
        } })]
        : [SW.btn(setT("settings_language_install"), { cls: "btn--primary btn--small", action: "language-install-" + l.code, run: async () => {
          const ok = await setLangSend(box, route + "install", {}, setT("settings_language_installed_done", { name }),
            (e) => setT("settings_language_not_installed", { name, reason: setLangSentence(e.message) }));
          if (ok) { followApply(true); await paint(true); }
        } })];
      return h("li", { class: "settings__lang-row", "data-code": l.code }, h("span", { class: "settings__name", textContent: setLangName(l) }),
        h("span", { class: "muted" }, l.complete != null ? setT("settings_language_complete", { percent: l.complete }) : null), h("span", { class: "actions" }, ...act));
    });
    L.done(rows.length ? h("ul", { class: "settings__lang-list" }, rows) : h("p", { class: "muted", textContent: setT("settings_language_none_more") }));
  };
  const body = h("div", {}, region, box);
  if (SW.phone()) SW.openModal(SW.sheet({ title, body }), opener);
  else SW.ask({ kind: "info", wide: true, title, body, opener });
  paint(true);
}
// Settings > General: Demo. Try the demo while off (a Hub with data refuses by its code, as Getting started reads it), Leave demo while on
function setDemoRow() {
  const holder = h("div", { class: "settings__choice" }), box = boxFor("settings:demo");
  const paint = () => {
    const on = !!(S && S.demo);
    const act = on ? SW.btn(setT("settings_demo_leave"), { cls: "btn--ghost", action: "settings-leave-demo", run: () => window.startDemoLeave(() => { if (holder.isConnected) paint(); say(box, setT("settings_demo_off"), true); }) })
      : SW.btn(setT("settings_demo_try"), { cls: "btn--primary", action: "settings-try-demo", run: async () => {
        working(box, SW.t("common_working"));
        try {
          if (!(await window.startDemoSend(true))) return SW.result(box, null);
          say(box, setT("settings_demo_on"), true);
          await refresh();
          if (holder.isConnected) paint();
        } catch (e) { if (e.locked) SW.result(box, null); else say(box, window.startDemoRefusal(e), false); }
      } });
    holder.replaceChildren(h("span", { class: "field__label", textContent: setT("settings_demo_title") }),
      h("p", { class: "field__hint", textContent: on ? setT("settings_demo_on") : setT("settings_demo_hint") }), h("div", { class: "actions" }, act), box);
  };
  paint();
  return holder;
}
// Settings > Appearance: this browser's language, "Automatic" by default, kept in this browser (localStorage) and applied at once
function setLangChoice() {
  const holder = h("div", { class: "settings__choice" }), res = sbox();
  const paint = (d) => {
    const have = d ? setLangHave(d) : [{ code: "en", name: "English", reviewed: true }];
    const pick = SW.field({ type: "select", name: "appearance.language", label: setT("settings_language_device"), hint: setT("settings_language_device_hint"),
      value: have.some((l) => l.code === setLangStored()) ? setLangStored() : "", optional: false,
      options: [{ value: "", label: setT("settings_language_auto") }, ...have.map((l) => ({ value: l.code, label: setLangName(l) }))] });
    pick._control.addEventListener("change", () => {
      setLangKeep(pick._control.value);
      if (setLangSeen) document.documentElement.lang = setLangPick(setLangSeen);
      const one = have.find((l) => l.code === pick._control.value);
      SW.announce(setT("settings_language_done", { name: one ? one.name : setT("settings_language_auto") }));       // no visible result: the change is the feedback (03 10.2)
    });
    holder.replaceChildren(pick, res);
  };
  paint(setLangSeen);
  if (!setLangSeen) later(setLangApply().then((d) => { if (d && holder.isConnected && !holder.contains(document.activeElement)) paint(d); }));
  return holder;
}

// ----- Label Reader (03 10.5) -----
const ORDERS = [["auto", "auto"], ["local-then-cloud", "local_cloud"], ["cloud-then-local", "cloud_local"], ["cloud", "cloud"], ["local", "local"]];
function groupLabelReader({ g }) {
  const v = g.values, cloud = !!g.ai_intake_on, local = !!g.local_intake_on, hist = g.intake_history || {};
  const body = [];
  const badges = [];
  if (cloud) badges.push(badge("settings_reader_badge_cloud", S.anthropic.set ? "set" : "not_set"));
  const localBadge = h("span", { class: "settings__badge" });
  if (local) badges.push(localBadge);
  if (!cloud && !local) body.push(offNote("settings_reader_off"));
  else if (cloud && local) body.push(plainField({ name: "intake.reader", type: "select", label: SW.t("settings_reader_order"), hint: SW.t("settings_reader_order_hint"),
    value: v["intake.reader"] || "auto", optional: false, options: ORDERS.map(([k, w]) => ({ value: k, label: SW.t("settings_reader_order_" + w) })) }));
  else body.push(h("p", { textContent: SW.t("settings_reader_order_single", { engine: SW.t(cloud ? "settings_reader_engine_cloud" : "settings_reader_group_local") }) }));

  let keyField = null, keyProxy = null;
  if (cloud) {
    const kbox = sbox();
    keyField = SW.field({ row: true, type: "secret", mono: true, label: SW.t("settings_reader_key_new"), placeholder: SW.t("settings_reader_key_placeholder"),
      attrs: { "aria-required": "false", autocomplete: "new-password" } });
    // the form group sends its fields in the order they stand: a hidden field named `key` at the end of the form stands for
    // the typed key, so it goes after Records kept (03 10.5) and can be cleared on its own once it was accepted
    keyProxy = SW.field({ name: "key", label: SW.t("settings_reader_key_new"), attrs: { "aria-required": "false" } });
    keyProxy.hidden = true;
    keyField._control.oninput = () => { keyProxy._control.value = keyField._control.value; };
    const cancel = SW.btn(SW.t("common_cancel"), { cls: "btn--ghost", action: "cancel-key" });
    const entry = h("div", { class: "settings__entry" }, keyField, cancel);
    entry.hidden = true;
    const open = SW.btn(SW.t(S.anthropic.set ? "common_replace" : "settings_reader_key_set"), { action: "replace-key", run: () => { entry.hidden = false; open.hidden = true; keyField._control.focus(); } });
    cancel.addEventListener("click", () => { keyField._control.value = keyProxy._control.value = ""; keyField._control.dispatchEvent(new Event("change", { bubbles: true })); entry.hidden = true; open.hidden = false; open.focus(); });
    const checkBtn = SW.btn(SW.t("common_check"), { action: "check-key", run: async () => {
      working(kbox, SW.t("settings_reader_key_checking"));
      try { const r = await api("key/check", {}); say(kbox, r.text, r.ok); } catch (e) { say(kbox, e.status === 429 ? SW.t("settings_reader_key_wait") : hubText(e.message), false); }
    } });
    const remove = S.anthropic.set ? SW.btn(SW.t("common_remove"), { cls: "btn--ghost btn--danger", action: "remove-key", run: (e) => confirmed(
      { ...destroy("settings_key_remove_title", "settings_key_remove_body", "settings_key_remove_confirm"), opener: e.currentTarget }, "key/remove", {},
      () => loadGroup("label-reader", { quiet: true, fresh: true })) }) : null;
    body.push(heading("settings_reader_group_cloud"),
      h("div", { class: "settings__keyrow" }, h("p", { class: "field__label" }, SW.t("settings_reader_key"), " ", SW.status(S.anthropic.set ? "set" : "not_set", { small: true })),
        h("div", { class: "actions" }, checkBtn, open, remove)),
      kbox, entry,
      requiredField({ name: "model", mono: true, label: SW.t("settings_reader_model"), value: v.model == null ? "" : String(v.model), effect: "restarts",
        check: (x) => (x.trim() ? null : SW.t("settings_reader_model_error")) }),
      requiredField({ name: "reads_per_day", type: "number", mono: true, label: SW.t("settings_reader_reads"), value: String(v.reads_per_day == null ? "" : v.reads_per_day),
        unit: SW.t("settings_reader_reads_unit"), hint: SW.t("settings_reader_reads_hint"), effect: "restarts", check: whole(1, 100000, "settings_reader_reads_error") }));
  } else if (local) body.push(h("p", { class: "muted", textContent: SW.t("settings_reader_key_cloud_off") }));

  if (local) {
    const saved = v["local_intake.url"] || "", out = slot(), fpBox = h("div", { class: "settings__fp" });
    const addr = requiredField({ name: "local_intake.url", mono: true, label: SW.t("settings_reader_address"), value: saved, placeholder: SW.t("settings_reader_address_placeholder"),
      hint: SW.t("settings_reader_address_hint") });
    const tier = SW.segmented({ legend: SW.t("settings_reader_tier"), name: "local_intake.tier", value: v["local_intake.tier"] || "auto",
      options: [["auto", SW.t("settings_reader_tier_auto")], ["cpu", SW.t("settings_reader_tier_cpu")], ["gpu", SW.t("settings_reader_tier_gpu")]]
        .map(([value, label]) => ({ value, label })) });
    const test = SW.btn(SW.t("settings_reader_test"), { action: "test-reader", run: () => testReader(out, fpBox, addr, tier, saved) });
    body.push(heading("settings_reader_group_local"), h("div", { class: "settings__withbtn" }, addr, test), h("div", { class: "settings__test-row" }, out, fpBox),
      h("div", { class: "settings__choice" }, tier, h("p", { class: "field__hint", textContent: SW.t("settings_reader_tier_hint") })),
      readerInstall(g));
    readerBadge(localBadge);
  }
  body.push(heading("settings_reader_group_recent"),
    requiredField({ name: "intake.history_keep", type: "number", mono: true, label: SW.t("settings_reader_keep"), value: String(v["intake.history_keep"] == null ? 20 : v["intake.history_keep"]),
      hint: SW.t("settings_reader_keep_hint", { count: hist.count || 0, size: megabytes(hist.photo_bytes || 0) }), check: whole(0, 500, "settings_reader_keep_error") }));
  if (cloud) body.push(SW.banner({ kind: "warn", inline: true, text: SW.t("settings_reader_restart_warn") }));

  const el = setGroup("label-reader", { desc: SW.t("settings_reader_desc"), save: SW.t("settings_reader_save"), badges, body });
  const o = { send: null };
  if (keyProxy) el.querySelector(".group__body").append(keyProxy);
  const own = { key: async (value) => {
    try { await api("key", { key: value, code: theCode() }); } catch (e) { throw setRefusal(el, Object.assign(e, { data: { ...(e.data || {}), field: "key" } })); }
    keyField._control.value = "";                // accepted: it does not stay in the page (the proxy keeps what was sent, so the group is clean)
    return {};
  } };
  o.send = sender(() => el, () => o.group, own, { "intake.history_keep": (x) => Number(x), reads_per_day: (x) => Number(x) });
  o.before = async (keys) => !(keys.includes("model") || keys.includes("reads_per_day")) || !!(await SW.ask({ kind: "confirm", title: SW.t("settings_restart_title"),
    text: SW.t("settings_restart_body"), confirm: SW.t("settings_restart_confirm") }));
  o.done = (text) => (o.keys.includes("key") ? text + " " + SW.t("settings_reader_key_saved") : text);
  wireGroup(el, "label-reader", o);
  el.addEventListener("click", (e) => { if (keyField && e.target.closest('[data-action="undo-changes"]')) keyField._control.value = ""; });
  return el;
}
// the badge of the reader on this Hub: whether it answers now
function readerBadge(into) {
  api("settings/local-intake").then((r) => into.replaceChildren(SW.t("settings_reader_badge_local"), " ", r.ok ? SW.status("ok", { small: true }) : SW.status("needs_look", { small: true }), r.ok ? "" : " " + SW.t("settings_reader_local_down")),
    () => into.replaceChildren(SW.t("settings_reader_badge_local"), " ", SW.status("needs_look", { small: true }), " " + SW.t("settings_reader_local_down")));
}
// Test (03 10.5): the saved address without the code; an address typed and not saved after unlocking in place, saved nowhere
async function testReader(out, fpBox, addr, tier, saved) {
  const url = addr._control.value.trim();
  const formatFp = (fp) => (String(fp || "").replace(/\s+/g, "").match(/.{1,4}/g) || []).join(" ");
  const paintFp = (r) => {
    if (!r || !r.fp) return;
    fpBox.replaceChildren(h("span", { class: "muted", textContent: setT("settings_reader_cert_fp") }), " ", h("span", { class: "mono settings__fp-code", textContent: formatFp(r.fp) }));
  };
  const runRemoteTest = async (trust) => {
    const picked = tier.querySelector("input:checked");
    await needCode(async () => {
      try {
        const payload = { url, tier: picked ? picked.value : "auto", code: theCode() };
        if (trust) payload.trust = true;
        show(await api("settings/local-intake/test", payload));
      } catch (e) { if (e.locked) throw e; failed(e); }
    }, () => { SW.result(out, null); fpBox.replaceChildren(); });
  };
  // a changed certificate (03 10.5, D4): the error with the old and the new fingerprint, each under its label; Trust asks first
  // (10.11) and only then sends trust; afterwards the focus goes to Test, as the button goes away with the error
  const fpRows = (r) => [["settings_reader_cert_old", r.pinned], ["settings_reader_cert_new", r.fp]].filter(([, fp]) => fp)
    .map(([key, fp]) => h("div", { class: "settings__fp-row" }, h("span", { class: "muted", textContent: setT(key) }), " ", h("span", { class: "mono settings__fp-code", textContent: formatFp(fp) })));
  const show = (r) => {
    fpBox.replaceChildren();
    if (r.pin === "changed") {
      say(out, setT("settings_reader_cert_changed"), false);
      fpBox.append(...fpRows(r), SW.btn(setT("settings_reader_trust"), {
        action: "trust-reader-cert",
        run: async (e) => {
          const yes = await SW.ask({ kind: "confirm", opener: e.currentTarget, title: setT("settings_reader_trust_title"),
            text: setT("settings_reader_trust_body", { address: url }), body: h("div", { class: "settings__fp" }, ...fpRows(r)), confirm: setT("settings_reader_trust") });
          if (!yes) return;
          working(out, SW.t("settings_reader_testing"));
          await runRemoteTest(true);
          const again = addr.parentElement && addr.parentElement.querySelector('[data-action="test-reader"]');
          if (again) again.focus();
        },
      }));
      return;
    }
    paintFp(r);
    const tiers = Object.entries(r.tiers || {}).filter(([, t1]) => t1.usable).map(([k]) => k.toUpperCase());
    const list = new Intl.ListFormat("en", { type: "conjunction" }).format(tiers);
    if (!r.ok) { say(out, SW.t("settings_reader_test_none", { address: url }), false); return; }
    if (r.degraded) { say(out, SW.t("settings_reader_test_degraded", { reason: String(r.error || "").replace(/[.]$/, "") }), "warn"); return; }
    say(out, SW.t("settings_reader_test_ok", { version: r.version || "?", api: r.api_version, tiers: list, corrections: SW.t(r.revise ? "settings_reader_corrections_yes" : "settings_reader_corrections_no") }), true);
  };
  const failed = (e) => { fpBox.replaceChildren(); say(out, e.status === 409 ? SW.t("settings_reader_test_off") : hubText(e.message), false); };
  fpBox.replaceChildren();
  working(out, SW.t("settings_reader_testing"));
  if (url === saved) { try { show(await api("settings/local-intake")); } catch (e) { failed(e); } return; }
  await runRemoteTest(false);
}

// the reader that runs on this machine: install, upgrade, remove, and its token (jobs and one setting; not part of the Save)
function readerInstall(g) {
  const arrived = !!window.__swReaderArrived;
  if (arrived) window.__swReaderArrived = false;
  const have = g.local_intake_installed, box = sbox(), install = sbox(), tokBox = sbox(), out = h("div");
  const check = () => runJob(box, "local_intake_plan");
  api("settings/local-intake/plan").then((r) => {
    const s = r.saved;
    if (!s) {
      if (arrived && !have) check();
      return;
    }
    const p = s.plan, t1 = p.tiers[p.tier], label = SW.t(p.upgrade ? "settings_reader_plan_upgrade" : p.same ? "settings_reader_plan_reinstall" : "settings_reader_plan_install");
    const name = SW.t("settings_reader_plan_button", { label, version: p.version, tier: p.tier });
    fill(out, h("p", { class: "muted", textContent: SW.t("settings_reader_plan_checked", { when: new Date(s.at * 1000).toLocaleString() }) }),
      ...s.text.map((line) => h("p", { textContent: line })),
      t1 && s.fresh && !p.downgrade ? SW.btn(name, { cls: "btn--primary", action: "install-reader", run: (e) => runJob(install, "local_intake_install", { kind: "confirm",
        title: SW.t("settings_local_install_title", { version: p.version }), text: s.text.filter((l) => !l.startsWith("Installed") && !l.startsWith("Other")).join(" "),
        confirm: name, opener: e.currentTarget }) }) : null,
      install,
      !s.fresh ? h("p", { class: "muted", textContent: SW.t("settings_reader_plan_old") }) : null,
      h("p", { class: "muted", textContent: SW.t("settings_reader_plan_tier") }));
    if (arrived && !have && !s.fresh) check();
  }).catch((e) => say(out, hubText(e.message), false));
  const tok = SW.field({ row: true, type: "secret", label: SW.t("settings_reader_token"), attrs: { "aria-required": "false" } }), set = S.local_intake_token && S.local_intake_token.set;
  const tokSet = SW.btn(SW.t(set ? "common_replace" : "settings_reader_token_set"), { action: "set-token", run: () => settingAct(tokBox, "local_intake_token", { token: tok._control.value },
    () => loadGroup("label-reader", { quiet: true, fresh: true }), [tok._control]) });
  tok._control.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); tokSet.click(); } });
  return h("div", { class: "settings__reader", ...(arrived ? { "data-arrived": "1" } : {}) },
    h("p", { class: "muted", textContent: have ? SW.t("settings_reader_local_installed", { version: have.version, tier: have.tier }) : SW.t("settings_reader_local_not_installed") }),
    h("div", { class: "actions" },
      SW.btn(SW.t(have ? "settings_reader_check_upgrade" : "settings_reader_check_install"), { action: "check-reader", run: check }),
      have ? SW.btn(SW.t("settings_reader_local_remove"), { cls: "btn--ghost btn--danger", action: "remove-reader", run: (e) => runJob(box, "local_intake_remove", { ...destroy("settings_local_remove_title",
        "settings_local_remove_body", "common_remove"), opener: e.currentTarget }) }) : null), box, out,
    h("p", { class: "field__label" }, SW.t("settings_reader_token"), " ", SW.status(set ? "set" : "not_set", { small: true })),
    h("p", { class: "muted", textContent: SW.t("settings_reader_token_hint") }),
    tok, h("div", { class: "actions" }, tokSet,
      set ? SW.btn(SW.t("common_remove"), { cls: "btn--ghost btn--danger", action: "remove-token", run: () => settingAct(tokBox, "local_intake_token/remove", {}, () => loadGroup("label-reader", { quiet: true, fresh: true })) }) : null), tokBox);
}

// ----- Network and addresses (03 10.7) -----
function waitNewPort(newPort, box, previous) {
  const next = new URL(location.href);
  next.port = newPort;
  next.hash = "#settings/network";
  const link = h("a", { class: "btn", href: next.href, textContent: SW.t("common_open_hub_page") });
  const moving = SW.t("port_moving", { port: newPort });
  say(box, [moving, " ", link], "run");
  followApply(true);
  const expires = Date.now() + 90000;
  let seen = false, running = false, disconnected = false;
  const probe = async () => {
    if (Date.now() >= expires) {
      say(box, [SW.t("common_wait_hub_back"), " ", link], "warn");
      return;
    }
    try {
      const job = await api("job");
      running = job.name === "port_change" && job.running;
      if (job.name === "port_change") {
        seen ||= previous.name !== job.name || previous.next !== job.next || previous.text !== job.text
          || previous.exit !== job.exit || job.running;
        if (seen && !job.running) {
          if (!job.lost && job.exit === 0) {
            if (next.origin !== location.origin) location.replace(next.href);
            else say(box, SW.t("port_done", { port: newPort, address: next.href }), true);
          } else {
            const key = job.lost ? "job_failed_stopped" : "job_failed";
            say(box, SW.t(key, { title: SW.t("job_title_port_change") }), false);
          }
          return;
        }
      }
    } catch (e) {
      // Moving the listener cuts off the old origin before it can report the job's exit.
      if (e.unreachable && running && next.origin !== location.origin) {
        location.replace(next.href);
        return;
      }
      if (!disconnected) say(box, [SW.t("job_lost_touch"), " ", link], "warn");
      disconnected = true;
    }
    setTimeout(probe, 1000);
  };
  probe();
}

function openPortDialog(curPort, remoteOn, opener, portBox) {
  const portField = SW.field({
    row: true,
    mono: true,
    cls: "settings__port-field",
    name: "port",
    label: SW.t("port_dialog_field"),
    value: String(curPort),
    hint: setT("settings_network_port_hint"),
    attrs: { inputmode: "numeric" },
    check: (x) => {
      const s = x.trim();
      if (!s) return setT("settings_network_port_empty");
      if (!/^\d+$/.test(s)) return SW.t("port_error_not_a_number", { text: s });
      const n = Number(s);
      if (n !== 443 && (n < 1024 || n > 65535)) return SW.t("port_error_range_change", { port: n });
      if (n === 443 && remoteOn) return SW.t("port_remote_on_443");
      return null;
    },
  });
  portField._control.oninput = () => {
    const dlg = portField.closest("dialog");
    const h2 = dlg && dlg.querySelector(".dialog__title");
    const raw = portField._control.value.trim();
    if (h2) h2.textContent = SW.t("port_dialog_title", { port: /^\d+$/.test(raw) ? raw : curPort });
  };
  return SW.ask({
    kind: "entry",                       // a form dialog: the Port field has the focus, its value selected (07 SR-20, SR-23)
    title: SW.t("port_dialog_title", { port: curPort }),
    text: SW.t("port_dialog_body"),
    fields: [portField],
    confirm: SW.t("port_change"),
    opener,
    run: async ([port], { result }) => {
      let r = null;
      await needCode(async () => {
        try {
          const previous = await api("job");
          r = { ...await api("settings/port", { port: Number(port.trim()), code: theCode() }), previous };
        } catch (e) {
          if (e.locked) throw e;
          if (e.data && e.data.field === "port") {
            SW.fieldError(portField, hubText(e.message));
            portField._control.focus();
          } else {
            SW.result(result, "bad", hubText(e.message));
          }
        }
      });
      return r || false;
    },
  }).then((r) => {
    if (r?.job === "port_change") waitNewPort(r.port, portBox, r.previous);
    return r;
  });
}

function groupNetwork({ g }) {
  const v = g.values, web = !!g.web_on, curPort = Number(g.port) || 8710, portBox = sbox();
  const fields = [
    plainField({ name: "web.public_url", mono: true, label: SW.t("settings_network_public"), value: S.web.public_url || "", placeholder: SW.t("settings_network_public_placeholder"),
      hint: SW.t(web ? "settings_network_public_hint" : "settings_network_public_hint_off"), effect: "applies" }),
    plainField({ name: "station.url", mono: true, label: SW.t("settings_network_station"), value: S.station.url || "", placeholder: SW.t("settings_network_station_placeholder"),
      hint: SW.t("settings_network_station_hint"), effect: "applies" }),
    plainField({ name: "setup.hosts", mono: true, label: SW.t("settings_network_hosts"), value: (v["setup.hosts"] || []).join(" "), placeholder: SW.t("settings_network_hosts_placeholder"),
      hint: SW.t("settings_network_hosts_hint") }),
    h("div", { class: "settings__port-row" },
      h("span", { class: "field__label", textContent: SW.t("port_row_label") }),
      h("span", { class: "mono settings__port-val", textContent: String(curPort) }),
      SW.btn(SW.t("port_change"), { action: "change-port", run: (e) => openPortDialog(curPort, !!g.remote_on, e.currentTarget, portBox) })),
    portBox,
  ];
  if (web) fields.push(SW.check({ name: "web.printer_changes", label: SW.t("settings_network_changes"), desc: SW.t("settings_network_changes_hint"), checked: v["web.printer_changes"] === true }));
  fields.push(heading("settings_network_remote"), remoteBlock(g));
  const el = setGroup("network", { desc: SW.t("settings_network_desc"), save: SW.t("settings_network_save"), body: fields });
  const one = (key) => async (value) => {
    try { await api("settings", { key, value: value.trim(), code: theCode() }); } catch (e) { throw setRefusal(el, Object.assign(e, { data: { ...(e.data || {}), field: key } })); }
    return { applies: true };
  };
  const o = { send: null };
  o.send = sender(() => el, () => o.group, { "web.public_url": one("web.public_url"), "station.url": one("station.url") }, { "setup.hosts": (x) => x.split(/[\s,]+/).filter(Boolean) });
  wireGroup(el, "network", o);
  return el;
}
function remoteBlock(g) {
  if (!g.remote_on) return h("p", { class: "muted" }, SW.t("settings_network_remote_off"));
  const box = sbox(), rules = h("div");
  return h("div", { class: "settings__remote" }, h("p", { class: "muted", textContent: SW.t("settings_network_remote_text") }),
    h("div", { class: "actions" },
      SW.btn(SW.t("settings_network_firewall"), { action: "firewall", run: () => settingAct(box, "remote/firewall", {}, (r) => { SW.result(box, null); rules.replaceChildren(codeBlock(r.text, SW.t("settings_network_firewall"))); }) }),
      SW.btn(SW.t("settings_network_certificate"), { action: "certificate", run: (e) => runJob(box, "remote_issue", { kind: "confirm", title: SW.t("settings_certificate_title"),
        text: SW.t("settings_certificate_body"), confirm: SW.t("settings_network_certificate"), opener: e.currentTarget }) })), box, rules);
}

// ----- Web passphrase (03 10.8) -----
function groupPassphrase() {
  const set = S.passphrase.set, one = SW.field({ row: true, type: "secret", name: "passphrase", label: SW.t("settings_passphrase_new"), hint: SW.t("settings_passphrase_new_hint"),
    attrs: { autocomplete: "new-password", minlength: "12" }, check: (x) => (x.length >= 12 ? null : SW.t("settings_passphrase_new_error", { count: x.length })) });
  const two = SW.field({ row: true, type: "secret", name: "again", label: SW.t("settings_passphrase_again"), attrs: { autocomplete: "new-password" },
    check: (x) => (x === one._control.value ? null : SW.t("field_error_passphrase_mismatch")) });
  const el = setGroup("passphrase", { desc: SW.t("settings_passphrase_desc"), save: SW.t(set ? "settings_passphrase_replace" : "settings_passphrase_set"),
    badges: [SW.status(set ? "set" : "not_set", { small: true })],
    body: [S.passphrase.web ? null : h("p", {}, SW.status("note", { small: true }), " ", SW.t("settings_passphrase_web_off")), one, two] });
  const o = { send: async (key, value) => {
    if (key !== "passphrase") return {};
    try { await api("passphrase", { passphrase: value, code: theCode() }); } catch (e) { throw setRefusal(el, Object.assign(e, { data: { ...(e.data || {}), field: "passphrase" } })); }
    return {};
  }, done: () => SW.t("settings_passphrase_done") };
  wireGroup(el, "passphrase", o);
  return el;
}

// ----- Filament Catalog (03 10.9) -----
function openCustomEntriesDialog(opener, onChanged) {
  const id = SW.uid("cat-entries");
  const body = h("div", { class: "settings__cat-dialog-body" });
  const res = h("p", { class: "result result--block dialog__result", role: "status" });
  const close = () => dlg.close();
  const closeBtn = SW.btn(SW.t("common_close"), { cls: "btn--primary", action: "close", run: close });
  const form = h("form", { class: "dialog__form", method: "dialog", novalidate: true },
    h("div", { class: "dialog__head" },
      h("h2", { class: "dialog__title", id: id + "-title", textContent: setT("settings_catalog_entries_title") }),
      SW.iconBtn("close", SW.t("common_close"), { action: "close", run: close })),
    h("div", { class: "dialog__body" }, res, body),
    h("div", { class: "dialog__foot" }, closeBtn));
  const dlg = h("dialog", { class: "dialog dialog--wide", "aria-labelledby": id + "-title" }, form);
  dlg.addEventListener("close", () => {
    dlg.remove();
    if (opener && opener.isConnected) opener.focus();
  });
  document.body.append(dlg);
  dlg.showModal();

  const loadList = async () => {
    body.replaceChildren(h("p", { class: "muted", textContent: SW.t("common_loading_thing", { thing: setT("settings_catalog_entries_title") }) }));
    let data;
    try {
      data = await api("library/local");
    } catch (e) {
      body.replaceChildren();
      SW.result(res, "bad", hubText(e.message || setT("settings_catalog_entries_error")));
      return;
    }
    const entries = Array.isArray(data && data.entries) ? data.entries : [];
    if (!entries.length) {
      body.replaceChildren(h("p", { class: "muted", textContent: setT("settings_catalog_entries_empty") }));
      return;
    }
    const tbl = h("table", { class: "tbl settings__cat-table" },
      h("thead", {}, h("tr", {},
        h("th", { scope: "col", textContent: SW.t("common_field_brand") }),
        h("th", { scope: "col", textContent: SW.t("common_field_name") }),
        h("th", { scope: "col", textContent: SW.t("common_field_material") }),
        h("th", { scope: "col", textContent: setT("settings_catalog_col_added") }),
        h("th", { scope: "col", textContent: SW.t("common_actions") }))),
      h("tbody", {}, ...entries.map((row, at) => h("tr", {},
        h("td", { textContent: row.brand || "" }),
        h("td", { textContent: row.name || "" }),
        h("td", { textContent: row.material || "" }),
        h("td", { class: "mono", textContent: row.added || "" }),
        h("td", {}, SW.btn(SW.t("common_remove"), {
          cls: "btn--ghost btn--danger btn--small",
          action: "remove-local-entry",
          run: async (ev) => {
            const ok = await SW.ask({
              kind: "destructive",
              title: setT("settings_catalog_remove_title", { brand: row.brand || "", name: row.name || "" }),
              text: setT("settings_catalog_remove_body"),
              confirm: SW.t("common_remove"),
              opener: ev.currentTarget,
              run: async (_, { result }) => {
                let done = false;
                try {
                  await needCode(async () => {
                    await api(`library/local/${encodeURIComponent(row.id)}/remove`, { code: theCode() });
                    done = true;
                  });
                  return done;
                } catch (e) {
                  SW.result(result, "bad", hubText(e.message));
                  return false;
                }
              },
            });
            if (ok) {
              SW.result(res, "ok", setT("settings_catalog_removed"));
              if (typeof onChanged === "function") onChanged();
              await loadList();
              // the pressed Remove went with its row: the focus goes to the Remove now in its place, else to Close (07 KEY-7)
              const left = body.querySelectorAll('[data-action="remove-local-entry"]');
              (left[Math.min(at, left.length - 1)] || closeBtn).focus();
            }
          },
        }))))));
    body.replaceChildren(tbl);
  };

  loadList();
}

function groupCatalog({ lib }) {
  const on = !!S.modules?.find((m) => m.name === "library")?.on;
  const c = lib.last_check, none = lib.source === "none";
  const badges = [];
  if (!on) { /* switched off: no state badge */ } else if (none) badges.push(h("span", { class: "settings__badge", textContent: SW.t("settings_catalog_none") }));
  else if (c && !c.ok) badges.push(h("span", { class: "settings__badge" }, SW.status("needs_look", { small: true }), " " + SW.t("settings_catalog_failed")));
  else badges.push(SW.status("ok", { small: true }));
  const state = h("div", { class: "settings__state-lines" }), job = sbox(), entryBox = sbox();
  const paint = (l) => {
    const k = l.last_check;
    state.replaceChildren(...[h("p", { textContent: l.source === "none" ? SW.t("settings_catalog_none") : SW.t("settings_catalog_state", { version: l.version, source: l.source, count: l.local }) }),
      h("p", { class: k && !k.ok ? "" : "muted" }, k ? (k.ok ? SW.t("settings_catalog_check_ok", { at: k.at }) : SW.status("needs_look", { small: true, text: SW.t("settings_catalog_check_error", { at: k.at, error: k.error }) }))
        : SW.t("settings_catalog_check_never")),
      l.skipped && h("p", {}, SW.status("note", { small: true, text: l.skipped }))].filter(Boolean));
  };
  paint(lib);
  const reloadLib = () => api("library").then((fresh) => {
    setSeen.lib = fresh;
    paint(fresh);
    paintList();
  }, () => {});
  const body = [];
  if (!on) {
    body.push(offNote("settings_catalog_off"));
    return setGroup("catalog", { desc: SW.t("settings_catalog_desc"), badges, body });
  }
  body.push(state, h("div", { class: "actions" },
    SW.btn(SW.t("settings_catalog_update"), { action: "update-catalog", run: () => runJob(job, "library_update") }),
    SW.btn(SW.t("settings_catalog_back"), { cls: "btn--ghost", action: "catalog-back", run: (e) => runJob(job, "library_rollback", { ...destroy("settings_catalog_back_title", "settings_catalog_back_body",
      "settings_catalog_back_confirm"), opener: e.currentTarget }) })), job);

  const materials = Array.isArray(lib.materials) ? lib.materials : [];
  const brandField = requiredField({ name: "brand", label: SW.t("common_field_brand"), attrs: { autocomplete: "off" } });
  const nameField = requiredField({ name: "name", label: SW.t("common_field_name"), attrs: { autocomplete: "off" } });
  const matField = requiredField({
    name: "material",
    type: "select",
    label: SW.t("common_field_material"),
    value: "",
    optional: false,
    options: [
      { value: "", label: setT("settings_catalog_choose_material") },
      ...materials.map((m) => ({ value: m, label: m })),
      { value: "__other__", label: setT("settings_catalog_other") },
    ],
  });
  const otherField = plainField({ name: "other_material", label: setT("settings_catalog_other_material"), attrs: { autocomplete: "off" } });
  otherField.hidden = true;
  matField._control.addEventListener("change", () => {
    const isOther = matField._control.value === "__other__";
    otherField.hidden = !isOther;
    SW.fieldError(matField, null);
    if (!isOther) {
      SW.fieldError(otherField, null);
      otherField._control.value = "";
    } else {
      otherField._control.focus();
    }
  });

  const nozzleMin = plainField({ name: "nozzle_min", type: "number", mono: true, label: setT("settings_catalog_nozzle_min"), attrs: { step: "any" } });
  const nozzleMax = plainField({ name: "nozzle_max", type: "number", mono: true, label: setT("settings_catalog_nozzle_max"), attrs: { step: "any" } });
  const bedMin = plainField({ name: "bed_min", type: "number", mono: true, label: setT("settings_catalog_bed_min"), attrs: { step: "any" } });
  const bedMax = plainField({ name: "bed_max", type: "number", mono: true, label: setT("settings_catalog_bed_max"), attrs: { step: "any" } });
  const densityField = plainField({ name: "density", type: "number", decimal: true, mono: true, label: SW.t("common_field_density"), attrs: { step: "any" } });
  const mvsField = plainField({ name: "max_volumetric_speed", type: "number", decimal: true, mono: true, label: setT("settings_catalog_mvs"), attrs: { step: "any" } });
  const parentField = plainField({ name: "bambu_parent", label: SW.t("common_field_bambu_parent") });
  const notesField = plainField({ name: "notes", type: "textarea", cls: "settings__cat-notes", label: SW.t("common_field_notes") });
  const moreDetails = h("details", { class: "coll settings__cat-more" },
    h("summary", { textContent: setT("settings_catalog_more") }),
    h("div", { class: "settings__cat-grid" },
      nozzleMin, nozzleMax, bedMin, bedMax, densityField, mvsField, parentField, notesField));

  const fieldMap = {
    brand: brandField,
    name: nameField,
    material: matField,
    other_material: otherField,
    nozzle_min: nozzleMin,
    nozzle_max: nozzleMax,
    bed_min: bedMin,
    bed_max: bedMax,
    density: densityField,
    max_volumetric_speed: mvsField,
    bambu_parent: parentField,
    notes: notesField,
  };
  const moreKeys = new Set(["nozzle_min", "nozzle_max", "bed_min", "bed_max", "density", "max_volumetric_speed", "bambu_parent", "notes"]);
  for (const fld of Object.values(fieldMap)) {
    fld._control.oninput = () => SW.fieldError(fld, null);
  }

  const addBtn = SW.btn(SW.t("common_add"), { cls: "btn--primary", action: "add-entry" });
  addBtn.type = "submit";
  const seeBtn = SW.btn(setT("settings_catalog_see_entries"), { action: "see-entries", run: (e) => openCustomEntriesDialog(e.currentTarget, reloadLib) });
  const form = h("form", { class: "settings__cat-form", novalidate: true },
    heading("settings_catalog_entry"),
    brandField, nameField, matField, otherField, moreDetails,
    h("div", { class: "actions" }, addBtn, seeBtn),
    entryBox);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    for (const fld of Object.values(fieldMap)) SW.fieldError(fld, null);
    SW.result(entryBox, null);
    const brand = brandField._control.value.trim();
    const name = nameField._control.value.trim();
    const matRaw = matField._control.value;
    const otherMat = otherField._control.value.trim();
    let firstBad = null;
    if (!brand) { SW.fieldError(brandField, setT("settings_catalog_error_brand")); firstBad = firstBad || brandField; }
    if (!name) { SW.fieldError(nameField, setT("settings_catalog_error_name")); firstBad = firstBad || nameField; }
    if (!matRaw) { SW.fieldError(matField, setT("settings_catalog_error_material")); firstBad = firstBad || matField; }
    if (matRaw === "__other__" && !otherMat) { SW.fieldError(otherField, setT("settings_catalog_error_other_material")); firstBad = firstBad || otherField; }
    if (firstBad) {
      firstBad._control.focus();
      return;
    }
    const payload = { brand, name, material: matRaw === "__other__" ? otherMat : matRaw };
    for (const key of moreKeys) {
      const val = fieldMap[key]._control.value.trim();
      if (val !== "") payload[key] = val;
    }
    addBtn.disabled = true;
    working(entryBox, SW.t("common_adding"));
    try {
      let added = false;
      await needCode(async () => {
        await api("library/local", { ...payload, code: theCode() });
        added = true;
      });
      if (!added) { SW.result(entryBox, null); return; }
      for (const fld of Object.values(fieldMap)) fld._control.value = "";
      otherField.hidden = true;
      moreDetails.open = false;
      say(entryBox, setT("settings_catalog_added"), true);
      reloadLib();
    } catch (err) {
      const probs = err.data && Array.isArray(err.data.problems) ? err.data.problems : [];
      if (probs.length) {
        let focusFld = null;
        for (const p of probs) {
          const key = p.field === "material" && matField._control.value === "__other__" ? "other_material" : p.field;
          const fld = fieldMap[key];
          if (fld) {
            if (moreKeys.has(key)) moreDetails.open = true;
            SW.fieldError(fld, hubText(p.text));
            if (!focusFld) focusFld = fld;
          }
        }
        if (focusFld) { SW.result(entryBox, null); focusFld._control.focus(); }
        else say(entryBox, hubText(err.message), false);
      } else {
        say(entryBox, hubText(err.message), false);
      }
    } finally {
      addBtn.disabled = false;
    }
  });

  body.push(form);
  return setGroup("catalog", { desc: SW.t("settings_catalog_desc"), badges, body });
}
