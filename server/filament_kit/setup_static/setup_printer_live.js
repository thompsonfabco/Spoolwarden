// The live printer view of the Printers section (the 20:58 ruling point 3): what an open printer shows in
// its Detail region. Loaded after setup.js; everything from the Hub goes in through textContent. It exports ONE function, printerLive(p, ctx),
// which returns the view's element; the printer detail puts it in its region. The view asks `printers/{serial}/live` every 2 s while it
// is on the page and the tab is visible, and stops by itself when it leaves the page. Looking (state, slots, usage) needs nothing; the
// camera, the controls and every slot action go through needCode(), so a Locked page opens the unlock dialog first.
//   ctx (all optional): path(what) the page route of a printer; api(path, body) the Hub call; pickSpool(slot) a spool picker (-> spool | null)
(() => {
"use strict";

const POLL_MS = 2000;
const ACTIVE = ["RUNNING", "PAUSE", "PREPARE", "SLICING"];
const UP = ["connected", "unknown_model"];
const plFIX = { offline: "printers_fix_offline", wrong_code: "printers_fix_wrong_code", needs_developer_mode: "printers_fix_developer_mode",
  cert_changed: "printers_fix_cert_changed", unauthorized: "printers_fix_unauthorized", klippy_not_ready: "printers_fix_klippy" };
const JOB = { RUNNING: ["printer_state_printing", "info", "s-diamond"], PAUSE: ["printer_state_paused", "warn", "s-triangle"],
  FINISH: ["printer_state_finished", "ok", "s-filled"], FAILED: ["printer_state_failed", "bad", "s-octagon"],
  PREPARE: ["printer_state_preparing", "info", "s-diamond"], SLICING: ["printer_state_preparing", "info", "s-diamond"] };
const JOB_WORD = { RUNNING: "printer_job_printing", PAUSE: "printer_job_paused", FINISH: "printer_job_finished", FAILED: "printer_job_failed",
  PREPARE: "printer_job_preparing", SLICING: "printer_job_slicing", IDLE: "printer_job_idle" };
const DONE = { pause: "printer_result_paused", resume: "printer_resumed_line", stop: "printer_stopped_line", light_on: "printer_light_on_line",
  light_off: "printer_result_light_off" };

// The live texts are in the catalogue (strings/en.json); keep the empty STUB fallback table only if an entry is added later.
const STUB = {
};
const fillText = (text, vars) => (vars ? String(text).replace(/\{([a-z][a-z0-9_]*)\}/g, (m, n) => (vars[n] != null ? String(vars[n]) : m)) : String(text));
const t = (key, vars) => {
  const v = SW.t(key, vars);
  return v !== key || !(key in STUB) ? v : fillText(STUB[key], vars);
};
const hx = (...args) => SW.h(...args);
const plE = (tag, cls, ...kids) => hx(tag, { class: cls }, ...kids);
const enc = encodeURIComponent;
const plhexOf = (s) => (/^[0-9A-Fa-f]{6}$/.test(s.color_hex || "") ? "#" + s.color_hex : "");
const plnum = (v) => (v == null || v === "" || Number.isNaN(Number(v)) ? "–" : String(Math.round(Number(v))));
const timeLeft = (min) => (!min || min < 1 ? "" : min >= 60 ? t("printer_time_hours", { hours: Math.floor(min / 60), minutes: min % 60 }) : t("printer_time_minutes", { minutes: min }));
const clockOf = (iso) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? "" : `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`; };
const plstamp = (at) => String(at || "").replace("T", " ").slice(0, 16);
const spoolName = (s) => `${s.vendor || ""} ${s.name || ""}`.trim() || t("spool_title", { id: s.id });
const plsentence = (kind, shape, text) => plE("span", "status status--" + kind, SW.shape(shape), text);
const plHubText = (m) => (typeof hubText === "function" ? hubText(m) : String(m || "").trim());

function printerLive(p, ctx = {}) {
  const path = (what) => (ctx.path ? ctx.path(what) : `printers/${enc(p.serial)}/${what}`);
  const call = ctx.api || api;
  const S = { live: null, usage: null, sig: {}, ctl: "", camOn: false, busy: false, timer: 0, tries: 0, seen: false, wasActive: false,
    noLight: false, results: new Map(), undone: new Map() };
  const head = plE("div", "plive__state"), job = plE("div", "plive__job"), bar = SW.progress({ value: 0, name: t("printer_progress_name") });
  const pct = plE("span", "plive__pct mono"), prog = plE("div", "plive__prog", bar, pct), detail = plE("p", "plive__detail muted"), temps = plE("p", "plive__temps mono");
  const alerts = plE("div", "plive__alerts"), notes = plE("div", "plive__notes"), msg = hx("p", { class: "result plive__msg", role: "status" });
  const locked = plE("div", "plive__locked");
  const controls = plE("div", "plive__controls actions"), camBtn = SW.btn(t("printer_camera_show"), { icon: "camera", action: "camera", run: () => cameraToggle() });
  const loadAnyBtn = SW.btn(t("live_load_any"), { action: "load-any", run: () => runLoadAny(loadAnyBtn) });
  const camBox = hx("div", { class: "plive__cam", hidden: true });
  const autoNotice = hx("div", { class: "result result--block plive__autonotice", role: "status" });
  const units = plE("div", "plive__units"), usageBox = plE("div", "plive__log"), usageMsg = hx("p", { class: "result", role: "status" });
  // Download as CSV: GET export/usage.csv for this printer, unlocked as a file leaves the Hub
  async function downloadUsage() {
    const r = await fetch(`/setup/api/export/usage.csv?printer=${enc(p.serial)}`, { credentials: "same-origin", headers: { "X-Setup-Code": theCode() } });
    if (!r.ok) { let d = {}; try { d = await r.json(); } catch { /* not JSON */ } throw hubError(r.status, d); }
    const m = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "");
    const url = URL.createObjectURL(await r.blob());
    const a = hx("a", { href: url, download: m ? m[1] : "usage.csv" });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }
  function usageMenu() {
    if (typeof hubCanExport === "function" && !hubCanExport("usage")) return null;
    const more = SW.iconBtn("more", t("live_usage_more"), { cls: "plive__usagemore" });
    SW.menu(more, () => [{ label: t("live_download_csv"), icon: "download", action: "download-csv",
      run: () => needCode(async () => { try { await downloadUsage(); } catch (e) { if (e.locked) throw e; SW.toast(t("live_csv_failed", { reason: plHubText(e.message) }), "bad"); } }) }], { label: t("live_usage_more") });
    return plE("div", "actions plive__usagebar", more);
  }
  const titled = (key, ...kids) => { const id = SW.uid("pl"); return hx("section", { class: "plive__group", "aria-labelledby": id }, hx("h3", { class: "plive__title", id }, t(key)), ...kids); };
  const root = hx("section", { class: "plive", "aria-label": t("live_head"), data: { printer: p.serial != null ? "live" : null } },
    locked, plE("div", "plive__camrow", head, loadAnyBtn), job, prog, detail, temps, alerts, notes, controls, msg, plE("div", "plive__camrow", camBtn), camBox,
    titled("live_slots", autoNotice, units), titled("printer_usage_title", usageMenu(), usageBox, usageMsg));
  prog.hidden = detail.hidden = temps.hidden = true;
  camBtn.hidden = true;
  loadAnyBtn.hidden = true;

  // ----- the looks of one read -----
  const up = () => !!S.live && UP.includes(S.live.state);
  const gcode = () => (S.live && S.live.status && S.live.status.gcode_state) || "";
  const active = () => up() && ACTIVE.includes(gcode());
  const printerName = () => p.name || (S.live && S.live.name) || p.serial || "";
  const autoState = () => (S.live && ((S.live.status && S.live.status.auto) || S.live.auto)) || null;
  const canAuto = () => !!(S.live && ((S.live.status && S.live.status.auto_possible) || S.live.auto_possible));
  const autoSpoolName = (a) => {
    const sp = a && a.spool;
    if (sp && (sp.vendor || sp.name)) return `${sp.vendor || ""} ${sp.name || ""}`.trim();
    const id = sp && sp.id != null ? sp.id : (a && a.spool_id);
    return id != null ? t("spool_title", { id }) : t("live_the_spool");
  };
  function paintState() {
    const L = S.live, s = (L && L.status) || {}, st = L ? L.state : "";
    if (!L) { head.replaceChildren(plE("span", "muted", t("printer_waiting"))); return; }
    const word = st === "offline" ? SW.status("offline") : up() ? SW.status("connected") : SW.status("needs_look");
    head.replaceChildren(word, ...(!up() ? [plE("span", "muted", plFIX[st] ? t(plFIX[st]) : String(st).replace(/_/g, " "))] : []));
    const g = gcode(), jt = up() && g ? JOB[g] : null;
    job.replaceChildren(...(up() && g ? [jt ? plsentence(jt[1], jt[2], s.job ? t(jt[0], { job: s.job }) : t(JOB_WORD[g])) : plE("span", "muted", t(JOB_WORD[g] || "printer_job_idle"))] : []));
    const live = active();
    prog.hidden = !live;
    if (live) {
      const pc = Math.max(0, Math.min(100, Number(s.percent) || 0));
      SW.setProgress(bar, pc / 100, t("printer_percent", { percent: pc }));
      pct.textContent = t("printer_percent", { percent: pc });
    }
    const left = timeLeft(s.remaining_min), clock = s.eta ? clockOf(s.eta) : "";
    const layer = s.total_layers ? t("printer_progress_layer", { layer: s.layer ?? "?", layers: s.total_layers }) : "";
    detail.textContent = live ? [layer, left ? t("printer_progress_left", { time: left }) : "", clock ? t("printer_progress_done", { clock }) : "", s.stage].filter(Boolean).join(" · ") : "";
    detail.hidden = !detail.textContent;
    const tp = s.temps || {}, lines = [];
    (tp.nozzle || []).forEach((n, i) => lines.push(t(tp.nozzle.length > 1 ? "printer_temp_nozzle_n" : "printer_temp_nozzle", { n: i + 1, now: plnum(n.current), target: plnum(n.target) })));
    if (tp.bed) lines.push(t("printer_temp_bed", { now: plnum(tp.bed.current), target: plnum(tp.bed.target) }));
    if (tp.chamber) lines.push(t("printer_temp_chamber", { now: plnum(tp.chamber.current) }));
    temps.textContent = up() ? lines.join(" · ") : "";
    temps.hidden = !temps.textContent;
    const hms = s.hms || [], err = s.print_error;
    alerts.replaceChildren(...(up() ? [...hms.map((x) => plsentence("bad", "s-octagon", x.text || t("printer_alert_code", { code: x.code }))),
      ...(err && !hms.length ? [plsentence("bad", "s-octagon", t("printer_alert_code", { code: err }))] : [])] : []));
    alerts.setAttribute("aria-label", t("live_alerts"));
    notes.replaceChildren(...[...(s.notes || []), s.spoolman_warning].filter((n) => typeof n === "string" && n).map((n) => plE("p", "muted", n)));
    loadAnyBtn.hidden = !up();
    camBtn.hidden = !S.camOn && !(L.camera && L.camera.kind);
  }

  // ----- the controls -----
  function paintControls() {
    const L = S.live, s = (L && L.status) || {}, g = gcode(), light = (L && L.controls && L.controls.light) && !S.noLight;
    const sig = [up(), g, s.light, light, SW.lock.state === "locked"].join("|");
    locked.replaceChildren(...(SW.lock.state === "locked" ? [plE("p", "muted", t("live_locked"), " ", unlockToShow())] : []));
    if (sig === S.ctl) return;
    S.ctl = sig;
    const ctl = (action, key, cls) => { const b = SW.btn(t(key), { cls, action, run: () => command(action, b) }); return b; };
    const row = [];
    if (up() && g === "RUNNING") row.push(ctl("pause", "printer_pause", ""));
    if (up() && g === "PAUSE") row.push(ctl("resume", "printer_resume", "btn--primary"));
    if (up() && light) {
      if (s.light === "on" || s.light === "off") row.push(ctl(s.light === "on" ? "light_off" : "light_on", s.light === "on" ? "printer_light_off" : "printer_light_on", "btn--ghost"));
      else row.push(ctl("light_on", "printer_light_on", "btn--ghost"), ctl("light_off", "printer_light_off", "btn--ghost"));
    }
    if (active()) { const b = SW.btn(t("printer_stop"), { cls: "btn--danger", action: "stop", run: () => stop(b) }); row.push(b); }
    controls.replaceChildren(...row);
  }
  const post = (what, body) => call(path(what), { ...body, code: theCode() });
  // ----- Undo: the answer names its event; the toast says what happened and offers Undo for 10 seconds; none named: no Undo -----
  const eventId = (r) => {
    const e = r && r.event;
    if (e && e.id != null) return e.undo && e.undo.possible === false ? null : e.id;
    return r && r.event_id != null ? r.event_id : null;
  };
  const undoEvent = (id) => needCode(async () => {
    try {
      const r = await call(`events/${enc(id)}/undo`, { code: theCode() });
      SW.toast([t("live_put_back"), r.note].filter(Boolean).join(" "), "ok");
      refresh();
    } catch (e) {
      if (e.locked) throw e;
      const raw = e.data && e.data.error && e.data.error !== "bad origin" ? e.data.error : "";
      if (e.status === 409) SW.toast(raw && !/^[a-z0-9_]+$/.test(raw) ? plHubText(raw) : t("live_undo_refused"), "warn");
      else SW.toast(t("live_undo_failed", { reason: plHubText(e.message) }), "bad");
    }
  });
  const slotToast = (text, r) => {
    const id = typeof hubHasHistory !== "function" || hubHasHistory("event_undo") ? eventId(r) : null;
    SW.toast(text, "ok", id == null ? {} : { action: { label: t("common_undo"), name: t("live_undo_name", { what: text }), run: () => undoEvent(id) } });
  };
  // a change goes through the lock: Locked opens the unlock dialog and the work carries on after the code
  const unlocked = (run) => needCode(run);
  async function command(action, button, confirm) {
    await unlocked(async () => {
      if (S.busy) return;
      S.busy = true;
      SW.busy(button, t("common_working"));
      SW.result(msg, "run", t("common_working"));
      try {
        await post("command", confirm ? { action, confirm: true } : { action });
        SW.result(msg, "ok", t(DONE[action]));
        S.ctl = "";
        refresh();
      } catch (e) {
        if (e.locked) throw e;
        if (e.data && e.data.code === "not_supported") { S.noLight = S.noLight || action.startsWith("light"); S.ctl = ""; SW.result(msg, "warn", t("live_not_supported")); paintControls(); }
        else SW.result(msg, "bad", e.data && e.data.code === "refused" && typeof e.message === "string" ? t("printer_said_no", { error: e.message }) : e.message);
      } finally { S.busy = false; SW.idle(button); }
    });
  }
  // asked first, and the page is unlocked before the question, so a Locked page is not asked twice
  const stop = (button) => unlocked(async () => {
    const job_ = ((S.live || {}).status || {}).job, printer = p.name || (S.live && S.live.name) || "";
    const go = await SW.ask({ kind: "destructive", title: t("printer_stop_dialog_title"), opener: button, cancel: t("printer_stop_keep"), confirm: t("printer_stop"),
      text: job_ ? t("printer_stop_dialog_body", { job: job_, printer }) : t("printer_stop_dialog_body_nojob", { printer }) });
    if (go) await command("stop", button, true);
  });

  // ----- the camera: an image request carries no code, so a trusted computer asks directly (its cookie goes along) and any other
  // gets a ticket first (POST camera/ticket, with the code). The FIRST camera address carries ?ticket=; the Hub then keeps a camera
  // session (a cookie for this printer's camera routes, 10 minutes from the last request), so the playlist's segments need no
  // ticket. The page ends the session (POST camera/close) when the camera is closed -----
  async function firstAddress(file) {
    const base = "/setup/api/" + path(file);
    if (SW.lock.state === "trusted") return base;
    const r = await post("camera/ticket", {});
    S.session = true;
    return `${base}?ticket=${enc(r.ticket)}`;
  }
  function closeSession() {
    if (!S.session) return;
    S.session = false;
    // keepalive: it still goes when the page is being left; the answer is of no interest
    fetch("/setup/api/" + path("camera/close"), { method: "POST", credentials: "same-origin", keepalive: true, headers: { "Content-Type": "application/json" }, body: "{}" }).catch(() => {});
  }
  function cameraStop() {
    for (const m of camBox.querySelectorAll("img, video")) { m.removeAttribute("src"); if (m.load) m.load(); }   // drops the connection: the camera stops
    camBox.replaceChildren();
    camBox.hidden = true;
    S.camOn = false;
    camBtn.replaceChildren(SW.icon("camera", "icon--s"), t("printer_camera_show"));
    closeSession();
  }
  function cameraFail(text) {
    camBox.hidden = false;
    camBox.replaceChildren(plE("div", "plive__camtext", plE("p", "", text), SW.btn(t("common_try_again"), { action: "try-again", run: () => cameraStart() })));
  }
  async function cameraStart() {
    const kind = S.live && S.live.camera && S.live.camera.kind, name = p.name || (S.live && S.live.name) || "";
    closeSession();                                          // Try again: the old session goes before a new ticket is asked
    S.camOn = true;
    camBtn.replaceChildren(SW.icon("camera", "icon--s"), t("printer_camera_hide"));
    camBox.hidden = false;
    camBox.replaceChildren(plE("div", "plive__camtext", SW.spinner(), t("printer_camera_starting")));
    try {
      if (kind === "hls") {
        const v = hx("video", { class: "plive__video", playsInline: true, muted: true, autoplay: true, controls: true, "aria-label": t("printer_camera_alt", { printer: name }) });
        if (!v.canPlayType("application/vnd.apple.mpegurl")) { cameraFail(t("printer_camera_no_hls")); return; }
        const src = await firstAddress("camera/index.m3u8");
        if (!S.camOn) return;
        v.addEventListener("error", () => { if (S.camOn) cameraFail(t("printer_camera_failed")); });
        v.src = src;                                         // its segments are asked with the session's cookie
        camBox.replaceChildren(v);
        return;
      }
      const src = await firstAddress("camera.mjpeg");
      if (!S.camOn) return;
      const img = hx("img", { class: "plive__img", alt: t("printer_camera_alt", { printer: name }) });
      img.addEventListener("load", () => camBox.querySelector(".plive__camtext")?.remove(), { once: true });
      img.addEventListener("error", () => { if (S.camOn) cameraFail(t("printer_camera_failed")); });
      camBox.append(img);                                   // the starting line stays until the first picture
      img.src = src;
    } catch (e) {
      if (e.locked) { cameraStop(); throw e; }
      cameraFail(e.data && e.data.code === "too_many_views" ? t("live_too_many") : e.message);
    }
  }
  function cameraToggle() {
    if (S.camOn) { cameraStop(); return; }
    return unlocked(() => cameraStart());
  }

  // ----- slots -----
  const lineOf = (label) => {
    const el = hx("div", { class: "result plive__slotline", role: "status", data: { slot: label } }), r = S.results.get(label);
    if (r) SW.result(el, r[0], r[1].cloneNode ? r[1].cloneNode(true) : r[1]);
    return el;
  };
  const slotSay = (label, kind, lines) => {
    const body = plE("span", "plive__lines", [].concat(lines).filter(Boolean).map((x, i) => plE("span", "plive__linetext", x)));
    S.results.set(label, [kind, body]);
    const el = units.querySelector(`.plive__slotline[data-slot="${CSS.escape(label)}"]`);
    if (el) SW.result(el, kind, body.cloneNode(true));
  };
  async function doAutoCancel(a) {
    await unlocked(async () => {
      if (S.busy) return;
      S.busy = true;
      try {
        await post("auto/cancel", {});
        SW.result(msg, "info", t("live_auto_stopped", { name: autoSpoolName(a) }));
        if (S.live && S.live.status && S.live.status.auto) delete S.live.status.auto;
        if (S.live && S.live.auto) delete S.live.auto;
        S.sig.auto = "";
        paintAuto();
      } catch (e) {
        if (e.locked) throw e;
        SW.result(msg, e.status === 409 ? "info" : "bad", plHubText(e.message));
      } finally { S.busy = false; }
    });
    refresh();
  }
  async function doAutoChoose(label, a) {
    await unlocked(async () => {
      if (S.busy) return;
      S.busy = true;
      try {
        const done = await post("auto/choose", { slot: label });
        const name = autoSpoolName(a);
        const text = done.pending ? t("live_load_waiting", { slot: label }) : t(done.confirmed ? "live_load_done" : "live_load_sent", { name, slot: label });
        const moved = (done.moved_back || []).map((m) => (m.location ? t("spool_load_plan_goes_back", { id: m.id ?? "?", slot: label, location: m.location }) : t("spool_load_plan_goes_back_noplace", { id: m.id ?? "?", slot: label })));
        slotToast(text, done);
        slotSay(label, "ok", [text, ...moved, ...(done.problems || [])]);
        if (S.live && S.live.status && S.live.status.auto) delete S.live.status.auto;
        if (S.live && S.live.auto) delete S.live.auto;
        S.sig.auto = "";
        paintAuto();
      } catch (e) {
        if (e.locked) throw e;
        SW.result(msg, e.status === 409 && e.data && e.data.code === "nothing_to_choose" ? "info" : "bad", plHubText(e.message));
      } finally { S.busy = false; }
    });
    refresh();
  }
  function paintAuto() {
    const a = autoState();
    const key = JSON.stringify([a && a.state, a && a.spool_id, a && a.spool, a && a.slots, up(), printerName()]);
    if (key === S.sig.auto) return;
    S.sig.auto = key;
    if (!a) { autoNotice.replaceChildren(); autoNotice.removeAttribute("data-state"); return; }
    const stopBtn = SW.btn(t("live_auto_stop"), { action: "auto-cancel", run: () => doAutoCancel(a) });
    if (a.state === "choose") {
      autoNotice.dataset.state = "warn";
      const choices = (a.slots || []).map((label) => {
        const b = SW.btn(label, { action: "auto-choose", run: () => doAutoChoose(label, a) });
        b.dataset.slot = label;
        if (!up()) SW.disable(b, t("slot_load_disabled"));
        return b;
      });
      autoNotice.replaceChildren(SW.shape("s-triangle"), plE("span", "result__text", t("live_auto_choose", { name: autoSpoolName(a) })),
        plE("div", "actions plive__autochoices", ...choices), stopBtn);
      return;
    }
    autoNotice.dataset.state = "info";
    autoNotice.replaceChildren(SW.shape("s-hollow"), plE("span", "result__text", t("live_auto_waiting", { name: autoSpoolName(a), printer: printerName() })), stopBtn);
  }
  function paintSlots() {
    const L = S.live, st = (L && L.status) || {}, list = st.ams || [], key = JSON.stringify([list, up()]);
    if (key === S.sig.slots) return;
    S.sig.slots = key;
    const focus = document.activeElement && units.contains(document.activeElement) ? document.activeElement.dataset.key : null;
    const unitNode = (u) => hx("div", { class: "plive__unit" },
      hx("h4", { class: "plive__unittitle" }, [u.label, u.type_name].filter(Boolean).join(" · ")),
      hx("ul", { class: "slot-strip plive__slots", "aria-label": t("slot_list_label", { name: p.name || (L && L.name) || "" }) }, (u.slots || []).map((sl) => {
        const sp = sl.spool, waiting = sl.pending, hex = plhexOf(sl), grams = sp && sp.remaining_weight != null ? Math.round(sp.remaining_weight) : null;
        const mk = (what, key_, run, cls) => { const b = SW.btn(t(key_), { action: what, cls, name: t(`live_${what}_slot`, { slot: sl.label }), run: () => run(b) }); b.dataset.key = `${what}:${sl.label}`; return b; };
        const load = mk("load", "common_load", (b) => slotLoad(sl, b));
        const unload = sp ? mk("unload", "common_unload", (b) => slotUnload(sl, b)) : null;
        const clear = mk("clear", "live_clear", (b) => slotClear(sl, b));
        if (!up()) for (const b of [load, unload, clear]) if (b) SW.disable(b, t("slot_load_disabled"));
        const what = waiting && !sp && !sl.type ? null : sl.present === false ? plE("span", "muted", t("common_empty")) : [sp ? spoolName(sp) : sl.type || t("printer_slot_unknown"), " ", plE("span", "mono muted", hex)];
        return hx("li", { class: "slot-strip__row plive__slot", data: { state: waiting ? "waiting" : sl.active ? "active" : null, slot: sl.label } },
          plE("span", "slot-strip__label mono", sl.label), SW.swatch(hex, { empty: sl.present === false && !waiting }),
          plE("span", "slot-strip__name plive__what", what, waiting ? plE("span", "plive__waiting", t("slot_waiting"), waiting.spool_id != null ? [" ", plE("span", "mono", "#" + waiting.spool_id)] : "") : null),
          plE("span", "slot-strip__level", grams != null ? plE("span", "mono", t("printer_slot_left", { left: grams })) : null),
          plE("span", "slot-strip__use", sl.active ? t("slot_in_use") : ""),
          plE("span", "actions plive__actions", load, unload, clear), lineOf(sl.label));
      })));
    units.replaceChildren(...(list.length ? list.map(unitNode) : [plE("p", "muted", t(!L ? "printer_waiting" : up() ? "spool_load_no_slots" : "slot_none_offline"))]));
    if (focus) { const b = units.querySelector(`[data-key="${CSS.escape(focus)}"]`); if (b) b.focus(); }
  }
  const slotPost = (sl, what, body) => post(`slots/${enc(sl.label)}/${what}`, body);
  const whereOf = (r) => r.location || ((r.moved_back || [])[0] || {}).location || (typeof r.moved_back === "string" ? r.moved_back : "");
  async function runSlot(sl, button, work) {
    await unlocked(async () => {
      if (S.busy) return;
      S.busy = true;
      const line = units.querySelector(`.plive__slotline[data-slot="${CSS.escape(sl.label)}"]`);
      try { await work(line); } catch (e) {
        if (e.locked) throw e;
        S.results.set(sl.label, ["bad", e.message]);
        if (line) SW.result(line, "bad", e.message);
      } finally { S.busy = false; }
    });
    refresh();
  }
  // the dry run first: what would change is said before anything is sent; the confirmation then sends the same with dry_run false
  const slotUnload = (sl, button) => runSlot(sl, button, async (line) => {
    if (line) SW.result(line, "run", t("common_checking"));
    const plan = await slotPost(sl, "unload", { dry_run: true });
    if (line) SW.result(line, null);
    const name = (sl.spool && sl.spool.name) || t("spool_title", { id: (plan.spool || {}).id ?? "?" }), where = whereOf(plan);
    const done = await SW.ask({ kind: "confirm", title: t("printer_unload_title", { slot: sl.label }), opener: button, confirm: t("common_unload"),
      text: where ? t("printer_unload_body", { name, slot: sl.label, location: where }) : t("printer_unload_body_noplace", { name, slot: sl.label }),
      run: () => slotPost(sl, "unload", { dry_run: false }) });
    if (!done) { S.results.delete(sl.label); return; }
    slotToast(t("printer_unload_line", { name, slot: sl.label }), done);
    slotSay(sl.label, "ok", [t("printer_unload_line", { name, slot: sl.label }), ...(done.moved_back || []).map((m) => (m.location ? t("printer_unload_moved_back", { id: m.id ?? "?", location: m.location }) : t("printer_unload_moved_back_noplace", { id: m.id ?? "?" }))),
      done.waiting_ended ? t("live_unload_waiting") : ""]);
  });
  const slotClear = (sl, button) => runSlot(sl, button, async (line) => {
    if (line) SW.result(line, "run", t("common_checking"));
    const plan = await slotPost(sl, "clear", { dry_run: true });
    if (line) SW.result(line, null);
    const sp = plan.spool, where = plan.moved_back || "";
    const text = !sp ? t("live_clear_body", { slot: sl.label }) : where ? t("live_clear_body_spool", { slot: sl.label, name: spoolName(sp), location: where })
      : t("live_clear_body_spool_noplace", { slot: sl.label, name: spoolName(sp) });
    const done = await SW.ask({ kind: "confirm", title: t("live_clear_title", { slot: sl.label }), opener: button, confirm: t("live_clear"), text,
      body: plan.waiting_ended ? plE("p", "", t("live_clear_waiting")) : null, run: () => slotPost(sl, "clear", { dry_run: false }) });
    if (!done) { S.results.delete(sl.label); return; }
    slotToast(t("live_clear_done", { slot: sl.label }), done);
    slotSay(sl.label, "ok", [t("live_clear_done", { slot: sl.label }), done.waiting_ended ? t("live_clear_waiting") : "", done.printer_cleared ? t("live_clear_printer") : "", done.text || ""]);
  });
  // Load: a spool is picked (ctx.pickSpool, else the search below), the dry run says what would happen, then Load sends it
  async function pickSpool(sl, button) {
    if (ctx.pickSpool) return ctx.pickSpool(sl);
    let chosen = null;
    const results = hx("div", { class: "plive__picks" }), note = hx("p", { class: "result", role: "status" });
    const find = SW.field({ type: "search", label: t("common_search"), placeholder: t("common_search_placeholder"), onSearch: (q) => search(q) });
    let asked = 0;
    const row = (s) => hx("button", { class: "btn plive__pick", type: "button", "aria-pressed": "false", data: { spool: s.id }, on: { click: (ev) => {
      chosen = s;
      for (const b of results.children) b.setAttribute("aria-pressed", String(b === ev.currentTarget));
    } } }, t("printer_load_row_id", { id: s.id }) + " " + spoolName(s), " ",
    plE("span", "muted", t("printer_load_row_where", { location: s.location || "?", grams: s.remaining_g != null ? Math.round(s.remaining_g) : "?" })));
    const search = async (q) => {
      const mine = ++asked;
      try {
        const r = await call(`spools?q=${enc(q)}&limit=12`);
        if (mine !== asked) return;
        SW.result(note, null);
        results.replaceChildren(...((r.spools || []).length ? r.spools.map(row) : [plE("p", "muted", t("spools_empty_filtered"))]));
      } catch (e) { if (mine === asked) SW.result(note, "bad", t("live_pick_failed")); }
    };
    search("");
    const picked = await SW.ask({ kind: "confirm", title: t("live_pick_title", { slot: sl.label }), opener: button, confirm: t("common_load"), body: [find, results, note],
      run: () => { if (!chosen) { SW.result(note, "warn", t("live_pick_none")); return false; } return chosen; } });
    return picked || null;
  }
  const slotLoad = (sl, button) => runSlot(sl, button, async (line) => {
    const sp = await pickSpool(sl, button);
    if (!sp) return;
    if (line) SW.result(line, "run", t("common_checking"));
    const plan = await slotPost(sl, "load", { spool_id: sp.id, dry_run: true });
    if (line) SW.result(line, null);
    const moved = (plan.moved_back || []).filter((m) => m.id !== sp.id).map((m) => (m.location ? t("spool_load_plan_goes_back", { id: m.id ?? "?", slot: sl.label, location: m.location }) : t("spool_load_plan_goes_back_noplace", { id: m.id ?? "?", slot: sl.label })));
    const done = await SW.ask({ kind: "confirm", title: t("spool_load_title", { slot: sl.label }), opener: button, confirm: t("common_load"),
      text: t("live_plan", { name: spoolName(sp), slot: sl.label }), body: [plan.preset && plan.preset.name ? plE("p", "", t("printer_load_preset", { name: plan.preset.name })) : null, ...moved.map((m) => plE("p", "", m)),
        ...(plan.problems || []).map((x) => plE("p", "", x))], run: () => slotPost(sl, "load", { spool_id: sp.id, dry_run: false }) });
    if (!done) return;
    slotToast(done.pending ? t("live_load_waiting", { slot: sl.label }) : t(done.confirmed ? "live_load_done" : "live_load_sent", { name: spoolName(sp), slot: sl.label }), done);
    slotSay(sl.label, "ok", [done.pending ? t("live_load_waiting", { slot: sl.label }) : t(done.confirmed ? "live_load_done" : "live_load_sent", { name: spoolName(sp), slot: sl.label }), ...moved, ...(done.problems || [])]);
  });

  // Load a spool (header button): Step 1 picks a spool (last tapped tag or search); Step 2 chooses Automatic or a slot and previews
  const allSlots = () => ((S.live && S.live.status && S.live.status.ams) || []).flatMap((u) => u.slots || []);
  const normTag = (x) => String(x || "").replace(/[:\s-]/g, "").toLowerCase();
  const runLoadAny = (button) => unlocked(async () => {
    let chosen = null, selectedSlot = null, plan = null, planBusy = false, planSeq = 0, asked = 0;
    let dlgRef = null, confirmBtn = null;
    const syncBtn = () => {
      if (!confirmBtn) return;
      if (!chosen) SW.disable(confirmBtn, t("spool_load_pick_first"));
      else if (planBusy) SW.disable(confirmBtn, t("spool_load_working_out"));
      else if (!selectedSlot || !plan) SW.disable(confirmBtn, t("spool_load_pick_first"));
      else SW.disable(confirmBtn, null);
    };
    // Step 1
    const step1Note = hx("p", { class: "result", role: "status" }), picks = hx("div", { class: "plive__picks" });
    const tagBtn = SW.btn(t("spool_load_last_tag"), { action: "last-tag", run: () => useLastTag() });
    const find = SW.field({ type: "search", label: t("common_search"), placeholder: t("common_search_placeholder"), onSearch: (q) => search(q) });
    const spoolRow = (s) => hx("button", { class: "btn plive__pick", type: "button", data: { spool: s.id }, on: { click: () => chooseSpool(s) } },
      t("printer_load_row_id", { id: s.id }) + " " + spoolName(s), " ",
      plE("span", "muted", t("printer_load_row_where", { location: s.location || "?", grams: s.remaining_g != null ? Math.round(s.remaining_g) : "?" })));
    const search = async (q) => {
      const mine = ++asked;
      try {
        const r = await call(`spools?q=${enc(q)}&limit=12`);
        if (mine !== asked) return;
        picks.replaceChildren(...((r.spools || []).length ? r.spools.map(spoolRow) : [plE("p", "muted", t("spools_empty_filtered"))]));
      } catch { if (mine === asked) SW.result(step1Note, "bad", t("live_pick_failed")); }
    };
    const useLastTag = async () => {
      SW.busy(tagBtn, t("common_working"));
      SW.result(step1Note, null);
      try {
        const r = await call("spools/last-tag");
        if (r && r.spool && r.spool.id != null) { chooseSpool(r.spool); return; }
        const uid = r && r.tag && (typeof r.tag === "string" ? r.tag : r.tag.uid);
        if (!uid) { SW.result(step1Note, "info", t("spool_load_no_tag")); return; }
        const sr = await call(`spools?q=${enc(uid)}&limit=10`);
        const hit = (sr.spools || []).find((s) => s.tag && normTag(s.tag) === normTag(uid));
        if (!hit) { SW.result(step1Note, "warn", t("spool_load_tag_unlinked", { tag: uid })); return; }
        chooseSpool(hit);
      } catch (e) { if (e.locked) throw e; SW.result(step1Note, "bad", plHubText(e.message)); }
      finally { SW.idle(tagBtn); }
    };
    const step1 = plE("div", "plive__group", plE("div", "actions", tagBtn, plE("span", "muted", t("spool_load_or"))),
      find, picks, plE("p", "muted", t("spool_load_pick_hint")), step1Note);
    // Step 2
    const pickedBar = plE("div", "plive__camrow"), slotList = hx("div", { class: "plive__picks" });
    const errLine = hx("p", { class: "result", role: "status" }), planBox = plE("div", "plive__lines"), planNote = hx("p", { class: "result", role: "status" });
    const step2 = plE("div", "plive__group", pickedBar, plE("p", "", t("live_which_slot")), slotList, errLine, planNote, planBox);
    step2.hidden = true;
    const renderPlan = (target, p_) => {
      if (target === "auto") {
        const rep = p_.replaces;
        const repName = rep ? (rep.name || (rep.spool_id != null ? t("spool_title", { id: rep.spool_id }) : t("live_the_spool"))) : "";
        const lines = [
          t("live_auto_plan", { name: spoolName(chosen), printer: printerName() }),
          (p_.watching || []).length ? t("live_auto_watching", { slots: p_.watching.join(", ") }) : null,
          rep ? t("live_auto_replaces", { name: repName }) : null,
          ...(p_.problems || []),
        ].filter(Boolean);
        planBox.replaceChildren(...lines.map((ln) => plE("p", "", ln)));
        return;
      }
      const moved = (p_.moved_back || []).filter((m) => m.id !== chosen.id).map((m) => (m.location ? t("spool_load_plan_goes_back", { id: m.id ?? "?", slot: target, location: m.location }) : t("spool_load_plan_goes_back_noplace", { id: m.id ?? "?", slot: target })));
      const lines = [
        t("live_plan", { name: spoolName(chosen), slot: target }),
        p_.preset && p_.preset.name ? t("printer_load_preset", { name: p_.preset.name }) : null,
        ...moved,
        ...(p_.problems || []),
      ].filter(Boolean);
      planBox.replaceChildren(...lines.map((ln) => plE("p", "", ln)));
    };
    const selectTarget = async (target, clearError = true) => {
      selectedSlot = target;
      plan = null;
      for (const b of slotList.children) b.setAttribute("aria-pressed", String(b.dataset.slot === target));
      if (clearError) SW.result(errLine, null);
      planBox.replaceChildren();
      if (!target) { SW.result(planNote, null); syncBtn(); return; }
      const mine = ++planSeq;
      planBusy = true;
      syncBtn();
      SW.result(planNote, "run", t("spool_load_working_out"));
      try {
        const r = target === "auto"
          ? await post("slots/auto/load", { spool_id: chosen.id, dry_run: true })
          : await post(`slots/${enc(target)}/load`, { spool_id: chosen.id, dry_run: true });
        if (mine !== planSeq || !dlgRef || !dlgRef.open) return;
        plan = r;
        planBusy = false;
        SW.result(planNote, null);
        renderPlan(target, r);
        syncBtn();
      } catch (e) {
        if (mine !== planSeq || !dlgRef || !dlgRef.open) return;
        planBusy = false;
        SW.result(planNote, null);
        if (target === "auto" && e.data && e.data.code === "auto_not_possible") {
          SW.result(errLine, "bad", e.message || t("live_auto_refused"));
          const first = allSlots()[0];
          if (first) { selectTarget(first.label, false); return; }
        } else {
          SW.result(errLine, "bad", plHubText(e.message));
        }
        syncBtn();
      }
    };
    const buildChoices = () => {
      const slots = allSlots(), rows_ = [];
      if (canAuto()) {
        rows_.push(hx("button", { class: "btn plive__pick", type: "button", "aria-pressed": "false", data: { slot: "auto" },
          on: { click: () => selectTarget("auto", true) } }, plE("span", "", t("live_auto_option")), " ", plE("span", "muted", t("live_auto_hint"))));
      }
      for (const sl of slots) {
        const sp = sl.spool;
        const what = sp ? spoolName(sp) : sl.pending ? t("slot_waiting") : sl.present === false ? t("common_empty") : (sl.type || t("printer_slot_unknown"));
        rows_.push(hx("button", { class: "btn plive__pick", type: "button", "aria-pressed": "false", data: { slot: sl.label },
          on: { click: () => selectTarget(sl.label, true) } }, plE("span", "mono", sl.label), " ", what, sl.active ? " · " + t("slot_in_use") : null));
      }
      slotList.replaceChildren(...rows_);
      if (!canAuto() && !slots.length) { SW.result(errLine, "bad", t("spool_load_no_slots")); return null; }
      return canAuto() ? "auto" : slots[0].label;
    };
    const backToStep1 = () => {
      ++planSeq; chosen = null; selectedSlot = null; plan = null; planBusy = false;
      step1.hidden = false; step2.hidden = true;
      SW.result(errLine, null); SW.result(planNote, null); planBox.replaceChildren();
      syncBtn();
      if (find._control) find._control.focus();
    };
    const chooseSpool = (s) => {
      chosen = s;
      step1.hidden = true; step2.hidden = false;
      SW.result(step1Note, null);
      pickedBar.replaceChildren(
        plE("span", "", t("spool_load_picked", { id: s.id, name: spoolName(s), location: s.location || t("common_unknown"), grams: s.remaining_g != null ? Math.round(s.remaining_g) : "?" })),
        SW.btn(t("common_change"), { cls: "btn--ghost", action: "change-spool", run: backToStep1 })
      );
      const first = buildChoices();
      selectTarget(first, true);
      const sel = slotList.querySelector('[aria-pressed="true"]'); if (sel) sel.focus();
    };
    search("");
    const res = await SW.ask({
      kind: "confirm", title: t("live_load_any"), opener: button, confirm: t("common_load"), body: [step1, step2],
      open: (dlg, opener) => { SW.openModal(dlg, opener); dlgRef = dlg; confirmBtn = dlg.querySelector('[data-action="confirm"]'); syncBtn(); },
      run: async () => {
        if (!chosen || !selectedSlot || planBusy || !plan) return false;
        try {
          if (selectedSlot === "auto") {
            const done = await post("slots/auto/load", { spool_id: chosen.id, dry_run: false });
            return { mode: "auto", chosen, done };
          }
          const sl = allSlots().find((x) => x.label === selectedSlot) || { label: selectedSlot };
          const done = await slotPost(sl, "load", { spool_id: chosen.id, dry_run: false });
          return { mode: "slot", sl, chosen, moved: (plan.moved_back || []).filter((m) => m.id !== chosen.id), done };
        } catch (e) {
          if (e.locked) throw e;
          SW.result(errLine, "bad", plHubText(e.message));
          return false;
        }
      }
    });
    if (!res) return;
    if (res.mode === "auto") {
      SW.result(msg, "info", t("live_auto_waiting", { name: spoolName(res.chosen), printer: printerName() }));
      refresh();
      return;
    }
    const label = res.sl.label, text = res.done.pending ? t("live_load_waiting", { slot: label }) : t(res.done.confirmed ? "live_load_done" : "live_load_sent", { name: spoolName(res.chosen), slot: label });
    const moved = res.moved.map((m) => (m.location ? t("spool_load_plan_goes_back", { id: m.id ?? "?", slot: label, location: m.location }) : t("spool_load_plan_goes_back_noplace", { id: m.id ?? "?", slot: label })));
    slotToast(text, res.done);
    slotSay(label, "ok", [text, ...moved, ...(res.done.problems || [])]);
    refresh();
  });

  // ----- Filament used, with Undo per entry -----
  async function loadUsage() {
    try {
      const r = await call(path("usage?days=7"));
      S.usage = [...(r.entries || [])].sort((a, b) => String(b.at || "").localeCompare(String(a.at || ""))).slice(0, 20);
    } catch (e) { SW.result(usageMsg, "bad", e.message); return; }
    SW.result(usageMsg, null);
    usageBox.replaceChildren(S.usage.length ? hx("ul", { class: "plive__usage" }, S.usage.map(usageRow)) : plE("p", "muted", t("printer_usage_empty")));
  }
  function usageNote(e) {
    if (e.undone) return t("printer_usage_note_undone");
    if (e.observe) return t("printer_usage_note_logged");
    if (e.spool_id == null) return t("printer_usage_note_no_spool");
    if (e.error) { const k = "printer_usage_reason_" + e.error; return t("printer_usage_note_not_taken", { reason: SW.t(k) !== k ? t(k) : String(e.error).replace(/_/g, " ") }); }
    return "";
  }
  function usageRow(e) {
    const line = hx("div", { class: "result", role: "status" }), said = S.undone.get(e.id);
    if (said) SW.result(line, "ok", said);
    const counted = !e.undone && !e.observe && e.spool_id != null && !e.error && !String(e.id).startsWith("k-");
    const b = counted ? SW.btn(t("common_undo"), { action: "undo", name: t("printer_used_undo_name", { grams: e.grams ?? "?", job: e.job || t("printer_used_job_default") }), run: () => undo(e, b, line) }) : null;
    const from = e.spool ? t("printer_used_from_spool", { id: e.spool.id, name: spoolName(e.spool) }) : e.spool === null && e.spool_id != null ? t("printer_used_spool_gone") : "";
    return hx("li", { class: "plive__used", data: { state: e.undone ? "undone" : null, entry: e.id } },
      plE("span", "plive__grams mono", t("common_grams", { grams: e.grams ?? "?" })), plE("span", "plive__what", [e.job || t("printer_used_job_default"), e.slot].filter(Boolean).join(" · ")),
      plE("span", "plive__when mono muted", plstamp(e.at)), plE("span", "plive__from muted", [from, usageNote(e)].filter(Boolean).join(" · ")), b, line);
  }
  async function undo(e, button, line) {
    await unlocked(async () => {
      const back = await SW.ask({ kind: "confirm", title: t("printer_usage_undo_title"), opener: button, cancel: t("common_keep"), confirm: t("printer_usage_undo_confirm"),
        text: t("printer_usage_undo_body", { grams: e.grams ?? "?", job: e.job || "?", slot: e.slot || "?", time: plstamp(e.at) }), run: () => call(`usage/${enc(e.id)}/undo`, { code: theCode() }) });
      if (!back) return;
      S.undone.set(e.id, t("printer_usage_undone", { grams: e.grams ?? "?", id: e.spool_id ?? "?" }));
      loadUsage();
    });
  }

  // ----- the read, every 2 s while this is on the page and the tab is visible -----
  async function refresh() {
    try { S.live = await call(path("live")); } catch (e) { SW.result(msg, "bad", e.message); return; }
    const was = S.wasActive;
    S.wasActive = active();
    paintState(); paintControls(); paintAuto(); paintSlots();
    if (!S.usage || (was && !S.wasActive)) loadUsage();           // a job ended: its entry is new
  }
  function schedule(ms) {
    clearTimeout(S.timer);
    S.timer = setTimeout(tick, ms);
  }
  async function tick() {
    if (!root.isConnected) {
      if (S.seen || ++S.tries > 40) { teardown(); return; }       // never put on the page, or taken off it: stop
      schedule(50); return;
    }
    S.seen = true;
    if (!root.getClientRects().length) {                           // the detail is closed (or the tab is another): no read, no picture
      if (S.camOn) cameraStop();
      schedule(POLL_MS / 5); return;
    }
    if (!document.hidden) await refresh();
    schedule(POLL_MS);
  }
  function teardown() {
    clearTimeout(S.timer);
    cameraStop();
    document.removeEventListener("visibilitychange", onVisible);
    document.removeEventListener("sw:lock", onLock);
    window.removeEventListener("pagehide", closeSession);
  }
  function onVisible() { if (!document.hidden && root.isConnected) { clearTimeout(S.timer); tick(); } else if (document.hidden) cameraStop(); }
  function onLock() { S.ctl = ""; if (S.live) paintControls(); }                 // a running picture is not cut by a lock: the next Show camera asks for the code
  document.addEventListener("visibilitychange", onVisible);
  document.addEventListener("sw:lock", onLock);
  window.addEventListener("pagehide", closeSession);
  paintState(); paintAuto(); paintSlots(); paintControls();
  schedule(0);
  root.stop = teardown;
  return root;
}

window.printerLive = printerLive;
})();
