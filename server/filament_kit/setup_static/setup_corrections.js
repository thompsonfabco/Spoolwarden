// Share corrections on the Hub page: the switch in Settings > Label Reader (the group's own Save sets it), the card "Corrections
// waiting" under that group (everything in it applies at once), and the small parts the Recently added and Features sections use.
// Loaded after setup_settings.js, setup_intake.js and setup_features.js, which call it; everything from the Hub goes in through
// textContent, as there. The Hub's routes are `settings/general` (the setting), `label-reader/corrections` and its removals, retries
// and package, and the job `corrections_send` (the reader does the upload). A refusal says the Hub's text; its `code` is kept on the
// control that said it (data-code) and in the details of an error state.
"use strict";

const CORR_KEY = "label_reader.share_corrections";
const CORR_AFTER_MS = 2500;        // the Hub queues a correction in the background: the record is read again this long after a save
let corrOn = false;                // Share corrections is on as saved: the notes in Recently added

// Share corrections as saved (the view of the setting, no code); a Hub that does not answer says off
async function corrSaved() {
  try { const g = await api("settings/general"); corrOn = !!(g.values && g.values[CORR_KEY]); } catch { corrOn = false; }
  return corrOn;
}
// how many corrections wait, or null when the reader does not say
async function corrCount() {
  try { return Number((await api("label-reader/corrections")).count) || 0; } catch { return null; }
}
// a refusal in words, with its code kept on the control that says it
function corrFail(box, e) {
  say(box, hubText(e.message), false);
  const code = e.data && e.data.code;
  if (code) box.setAttribute("data-code", code); else box.removeAttribute("data-code");
}
const corrNote = (key, vars) => SW.banner({ kind: "info", inline: true, text: SW.t(key, vars) });
// a link to the card in Settings > Label Reader (the page scrolls to the card when it arrives)
const corrLink = (text) => h("a", { href: "#settings/label-reader", class: "recent__corr-link", on: { click: () => { window.__swCorrArrived = true; } } }, text);
// a dialog whose run sends a request with the code and shows a refusal in the dialog; resolves with the reply or null
function corrAsk(q, path, body) {
  return SW.ask({ ...q, run: async (_values, { result }) => {
    let r = null;
    await needCode(async () => {
      try { r = await api(path, { ...body, code: theCode() }); } catch (e) {
        if (e.locked) throw e;
        corrFail(result, e);
      }
    });
    return r || false;
  } });
}

// Features, turning Label Reader: On this Hub off while corrections wait: a warn line, or null
async function corrWarn() {
  const n = await corrCount();
  return n ? SW.banner({ kind: "warn", inline: true, text: SW.t("features_corr_warn", { count: n }) }) : null;
}

// ----- the switch, in the Label Reader group (10.5.1) -----
// Resolves with {node, send, before, done, again}: `node` goes in the group's body; `send` is the group's sender for the key;
// `before(keys)` may stop a save (switching off while corrections wait asks first); `done(keys)` words what the save also did;
// `again()` is what Save does when nothing changed but the reader is set differently from the Hub.
function corrSwitch(g) {
  const saved = !!(g.values && g.values[CORR_KEY]), local = !!g.local_intake_on, here = !!g.local_intake_installed;
  const s = { deleteWaiting: false, deleted: 0, restarted: false, differs: false };
  const check = SW.check({ name: CORR_KEY, label: SW.t("settings_corr_label"), desc: SW.t("settings_corr_text"), checked: saved, disabled: !local || !here });
  const input = check._control, line = h("div", { class: "settings__corr-line" });
  input.setAttribute("role", "switch");
  input.dataset.effect = "applies";            // the reader is set in a short job: the group waits for it
  const needs = () => SW.banner({ kind: "info", inline: true, text: SW.t("settings_corr_needs_reader"),
    actions: [h("a", { class: "btn", href: "#features", textContent: SW.t("nav_features") })] });
  const paint = (st) => {
    if (st.reason === "off" || st.reason === "not_installed") line.replaceChildren(needs());
    else if (st.reason === "too_old") line.replaceChildren(SW.banner({ kind: "info", inline: true, text: SW.t("settings_corr_too_old"),
      actions: [h("a", { class: "btn", href: "#updates", textContent: SW.t("nav_updates") })] }));
    else if (st.reason === "no_answer") line.replaceChildren(SW.banner({ kind: "warn", inline: true, text: SW.t("settings_corr_no_answer") }));
    else if (st.differs) { s.differs = true; line.replaceChildren(SW.banner({ kind: "warn", inline: true, text: SW.t("settings_corr_differs") })); }
    else line.replaceChildren();
    if (["off", "not_installed", "too_old", "no_answer"].includes(st.reason)) input.disabled = true;
  };
  if (!local) paint({ reason: "off" });
  else if (!here) paint({ reason: "not_installed" });
  else {
    input.disabled = true;                      // until the reader has said how it is set
    // a reader that is not well answers with its `error` too, so the state is in the refusal's data then
    const known = (d) => { input.disabled = false; if (d && d.share_corrections) paint(d.share_corrections); };
    api("settings/local-intake").then(known, (e) => known(e.data));
  }
  const node = h("div", { class: "settings__corr" }, check, line);
  const form = () => node.closest("form");
  const send = async (value) => {
    const body = { values: { [CORR_KEY]: value }, code: theCode() };
    if (!value && s.deleteWaiting) body.delete_waiting = true;
    let r;
    try { r = await api("settings/general", body); } catch (e) {
      if (e.locked) throw e;
      throw Object.assign(e, { text: () => hubText(e.message), field: undefined });
    }
    s.deleted = Number(r.deleted) || 0;
    s.restarted = !!(r.jobs && r.jobs.length);
    return s.restarted ? { applies: true } : {};
  };
  // Cancel saves nothing; Keep them and Delete them save the group
  const before = async (keys) => {
    s.deleteWaiting = false;
    if (!keys.includes(CORR_KEY) || input.checked || !saved) return true;
    const n = await corrCount();
    if (!n) return true;
    let choice = "keep";
    const del = SW.btn(SW.t("settings_corr_delete"), { cls: "btn--danger btn--solid", action: "delete-corrections", run: () => { choice = "delete"; del.closest("form").requestSubmit(); } });
    const yes = await SW.ask({ kind: "confirm", title: SW.t("settings_corr_stop_title"), text: SW.t("settings_corr_stop_body", { count: n }), confirm: SW.t("settings_corr_keep"),
      extra: [del], opener: input,
      open: (dlg, opener) => {
        const foot = dlg.querySelector(".dialog__foot");
        foot.replaceChildren(foot.querySelector('[data-action="cancel"]'), foot.querySelector('[data-action="confirm"]'), del);
        SW.openModal(dlg, opener);
      } });
    if (!yes) return false;
    s.deleteWaiting = choice === "delete";
    return true;
  };
  // the group names a saved field by its label; the design's result says "Share corrections"
  const named = (text) => text.split(SW.t("settings_corr_label")).join(SW.t("settings_corr_heading"));
  const done = (keys) => [keys.includes(CORR_KEY) && s.deleted ? SW.t("settings_corr_deleted", { count: s.deleted }) : null,
    keys.includes(CORR_KEY) && s.restarted ? SW.t("settings_corr_restarted") : null].filter(Boolean).join(" ");
  // the reader is set differently from the Hub: Save sets it again, with no change of the switch
  const again = async () => {
    if (!s.differs) return false;
    await needCode(async () => {
      const res = form().querySelector(".group__foot .result");
      working(res, SW.t("common_saving"));
      try { await api("job", { name: "local_intake_share", code: theCode() }); } catch (e) {
        if (e.locked) { SW.result(res, null); throw e; }
        say(res, hubText(e.message), false);
        return;
      }
      const end = await SW.setting("waitJob")();
      if (end && end.state === "failed") { say(res, SW.t("settings_corr_set_failed"), false); return; }
      loadGroup("label-reader", { quiet: true, fresh: true, result: ["ok", [SW.t("form_saved_fields", { fields: SW.t("settings_corr_heading") }), SW.t("settings_corr_restarted")].join(" ")] });
    });
    return true;
  };
  return { node, send, before, done, named, again };
}

// ----- Corrections waiting (10.5.2): a card under the Label Reader group -----
// the entry's own thumbnail as a picture of this origin: the page allows images from itself and blob: only
function corrThumbSrc(t) {
  if (typeof t !== "string" || !t) return null;
  const m = /^(?:data:(image\/[a-z0-9.+-]+);base64,)?([A-Za-z0-9+/=\s]+)$/i.exec(t);
  if (!m) return null;
  try {
    const bin = atob(m[2].replace(/\s+/g, "")), bytes = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return URL.createObjectURL(new Blob([bytes], { type: m[1] || "image/jpeg" }));
  } catch { return null; }
}
const corrWhen = (at) => (typeof at === "number" ? recTime(new Date(at * 1000).toISOString()) : recTime(String(at || "")));
const corrReason = (r) => (typeof r === "string" ? r.trim() : "");

// the log of the job that just ended: its lines without the step marks
async function corrJobLines() {
  try { return String((await api("job")).text || "").split("\n").map((l) => l.trim()).filter((l) => l && !l.startsWith("==>")); } catch { return []; }
}

function correctionsCard(on) {
  // a card of its own, not a group: it has no Save and is not one of the ten
  const tid = "set-corr-title", count = h("span", { class: "corr__count muted mono", role: "status" });
  const res = h("p", { class: "result", role: "status" }), body = h("div", { class: "corr__body" });
  const el = h("section", { class: "card corr", id: "set-corrections", "aria-labelledby": tid, hidden: !on },
    h("div", { class: "corr__head" }, h("h2", { id: tid, tabindex: "-1", textContent: SW.t("settings_corr_title") }), count),
    body);
  let sending = false;

  // `record` is the Hub's: the id of the Recently added record, absent once Recently added no longer keeps it
  function entryRow(e) {
    const rec = e.record || null, c = e.corrected || {}, was = e.predicted || {}, name = [c.vendor || c.brand, c.name].filter(Boolean).join(" ") || String(e.id);
    const kept = rec && e.photos > 0, src = kept ? recPhoto(rec, 1) : corrThumbSrc(e.thumbnail_b64);
    const more = e.photos > 1 ? h("span", { class: "thumb__count mono", "aria-hidden": "true" }, e.photos) : null;
    const thumb = kept
      ? h("button", { class: "thumb", type: "button", "aria-label": SW.t("photo_enlarge", { number: 1, total: e.photos }), on: { click: (ev) => recViewer(rec, e.photos, 0, name, ev.currentTarget) } },
        h("img", { class: "thumb__img", src, alt: "", width: 48, height: 48, loading: "lazy" }), more)
      : src ? h("span", { class: "thumb" }, h("img", { class: "thumb__img", src, alt: "", width: 48, height: 48 }))
        : h("span", { class: "thumb thumb--none" }, SW.icon("camera", "icon--s"), h("span", { class: "sr" }, SW.t("photo_none")));
    const fields = Object.keys(c).filter((k) => JSON.stringify(c[k]) !== JSON.stringify(was[k]));
    const remove = SW.btn(SW.t("common_remove"), { name: SW.t("settings_corr_remove_named", { name }), action: "remove-correction", run: () => removeOne(e.id) });
    return h("li", { class: "revisions__item", "data-id": String(e.id) },
      h("div", { class: "actions" }, thumb, h("div", {},
        rec ? h("a", { href: "#recent/" + encodeURIComponent(rec), textContent: name }) : h("span", { textContent: name }),
        h("p", { class: "muted" }, e.created_at ? [h("time", { class: "mono", textContent: corrWhen(e.created_at) }), " · "] : null,
          SW.t("settings_corr_read_by", { engine: SW.t(e.source === "cloud" ? "settings_reader_group_cloud" : "settings_reader_group_local") })),
        !rec ? h("p", { class: "muted", textContent: SW.t("settings_corr_record_gone") }) : null,
        !rec || !e.photos ? h("p", { class: "muted", textContent: SW.t("settings_corr_photos_gone") }) : null)),
      fields.length ? h("ul", { class: "changes changes--compact" }, fields.map((k) => recChange(k, was[k], c[k]))) : null,
      e.note ? h("p", { class: "muted", textContent: SW.t("recent_revision_note", { note: e.note }) }) : null,
      remove);
  }
  function notQueuedRow(q) {
    const name = q.name || String(q.record || ""), reason = corrReason(q.reason);
    return h("li", { class: "revisions__item", "data-record": String(q.record || "") },
      h("div", { class: "actions" }, h("span", { class: "thumb thumb--none" }, SW.icon("camera", "icon--s"), h("span", { class: "sr" }, SW.t("photo_none"))), h("div", {},
        q.record ? h("a", { href: "#recent/" + encodeURIComponent(q.record), textContent: name }) : h("span", { textContent: name }),
        q.at ? h("p", { class: "muted" }, h("time", { class: "mono", textContent: recTime(q.at) })) : null)),
      SW.banner({ kind: "warn", inline: true, text: reason ? SW.t("settings_corr_not_waiting", { reason }) : SW.t("settings_corr_not_waiting_plain") }),
      SW.btn(SW.t("common_try_again"), { action: "try-correction-again", run: () => tryAgain(q.record) }));
  }

  const removeOne = (id) => needCode(async () => {
    working(res, SW.t("common_working"));
    try { await api(`label-reader/corrections/${encodeURIComponent(id)}/remove`, { code: theCode() }); } catch (e) {
      if (e.locked) { SW.result(res, null); throw e; }
      corrFail(res, e);
      return;
    }
    await load(false);
    say(res, SW.t("settings_corr_removed"), true);
  });
  const tryAgain = (record) => needCode(async () => {
    working(res, SW.t("common_working"));
    let r;
    try { r = await api(`label-reader/corrections/${encodeURIComponent(record)}/retry`, { code: theCode() }); } catch (e) {
      if (e.locked) { SW.result(res, null); throw e; }
      corrFail(res, e);
      return;
    }
    await load(false);
    if (r.state === "waiting") say(res, SW.t("recent_corr_waiting"), true);
    else if (r.state === "not_queued") say(res, corrReason(r.reason) ? SW.t("settings_corr_not_waiting", { reason: corrReason(r.reason) }) : SW.t("settings_corr_not_waiting_plain"), "warn");
    else SW.result(res, null);
  });
  async function removeAll(n, opener) {
    const r = await corrAsk({ kind: "destructive", title: SW.t("settings_corr_remove_all_title"), text: SW.t("settings_corr_remove_all_body", { count: n }),
      confirm: SW.t("settings_corr_remove_all"), opener }, "label-reader/corrections/remove-all", {});
    if (!r) return;
    await load(false);
    say(res, SW.t("settings_corr_removed_all", { count: r.removed }), true);
  }
  async function download() {
    working(res, SW.t("common_working"));
    try {
      const r = await fetch("/setup/api/label-reader/corrections/package", { credentials: "same-origin" });
      if (!r.ok) { let d = {}; try { d = await r.json(); } catch { /* not JSON */ } throw hubError(r.status, d); }
      const m = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "");
      const url = URL.createObjectURL(await r.blob());
      const a = h("a", { href: url, download: m ? m[1] : "corrections.zip" });
      document.body.append(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
      SW.result(res, null);
    } catch (e) {
      corrFail(res, Object.assign(e, { message: SW.t("settings_corr_package_failed", { reason: hubText(e.message) }) }));
    }
  }
  // Send now: the job strip follows the job; the list is not editable while it runs, and is read again when it ends
  async function sendNow(n, opener) {
    const r = await corrAsk({ kind: "confirm", title: SW.t("settings_corr_send_title", { count: n }), text: SW.t("settings_corr_send_body"),
      confirm: SW.t("settings_corr_send_confirm"), opener }, "label-reader/corrections/send", {});
    if (!r) return;
    const asked = r.count || n, wrap = el.closest(".settings__group");
    sending = true;
    for (const b of el.querySelectorAll(".btn")) b.disabled = true;
    if (wrap) wrap._hold = true;
    say(res, SW.t("settings_corr_sending", { count: asked }), "run");
    const end = await SW.setting("waitJob")();
    const lines = await corrJobLines();
    sending = false;
    if (wrap) wrap._hold = false;
    await load(false);
    if (end && end.state === "failed") {
      const at = lines.map((l) => /^Error:/.test(l)).lastIndexOf(true);      // the command's own sentence, as it wrote it (it may wrap)
      const why = at < 0 ? "" : corrReason(lines.slice(at).join(" ").replace(/^Error:\s*/, ""));
      say(res, why ? SW.t("settings_corr_not_sent", { reason: hubText(why) }) : SW.t("settings_corr_not_sent_plain"), false);
      return;
    }
    const got = lines.map((l) => /^Sent (\d+) /.exec(l)).find(Boolean);
    say(res, SW.t("settings_corr_sent", { count: got ? Number(got[1]) : asked }), true);
  }

  function paint(d) {
    const entries = Array.isArray(d.entries) ? d.entries : [], nq = Array.isArray(d.not_queued) ? d.not_queued.filter((q) => q && typeof q === "object") : [];
    const n = Number(d.count) || 0;
    count.textContent = n ? SW.t("settings_corr_count", { count: n, size: megabytes(Number(d.bytes) || 0) }) : "";
    const rmAll = n ? SW.btn(SW.t("settings_corr_remove_all"), { cls: "btn--danger", action: "remove-all-corrections", run: (e) => removeAll(n, e.currentTarget) }) : null;
    const pack = SW.btn(SW.t("settings_corr_package"), { action: "download-package", run: download });
    const go = SW.btn(SW.t("settings_corr_send"), { cls: "btn--primary", action: "send-corrections", run: (e) => sendNow(n, e.currentTarget) });
    const list = entries.length || nq.length
      ? h("ul", { class: "revisions__list" }, ...entries.map(entryRow), ...nq.map(notQueuedRow))
      : SW.empty({ icon: "camera", title: SW.t("settings_corr_empty_title"), text: SW.t("settings_corr_empty_text") });
    body.replaceChildren(h("p", { class: "muted", textContent: SW.t(on ? "settings_corr_intro" : "settings_corr_intro_off") }), list,
      h("div", { class: "actions" }, ...[rmAll, pack, go].filter(Boolean)), h("p", { class: "field__hint", textContent: SW.t("settings_corr_package_hint") }), res);
    if (!n) { SW.disable(pack, SW.t("settings_corr_nothing")); SW.disable(go, SW.t("settings_corr_nothing")); }      // beside the buttons, so after they are in
    el.hidden = !(on || n > 0 || nq.length > 0);
    if (sending) for (const b of el.querySelectorAll(".btn")) b.disabled = true;
  }
  function failed(e) {
    const code = e.data && e.data.code, reason = hubText(e.message);
    const text = code === "unreachable" ? SW.t("settings_corr_reader_down") : SW.t("settings_corr_read_error", { reason });
    const state = SW.errorState({ title: SW.t("settings_corr_title"), text, onTry: () => load(false),
      details: (e.path ? `/setup/api/${e.path}\n` : "") + (e.status ? `HTTP ${e.status}: ` : "") + e.message + (code ? `\ncode: ${code}` : "") });
    if (code) state.setAttribute("data-code", code);
    body.replaceChildren(state, res);
    count.textContent = "";
    setSeen.cw = null;
  }
  async function load(first) {
    const L = first && on ? SW.loading(body, { thing: SW.t("settings_corr_title"), items: 3, onTry: () => load(true), title: SW.t("settings_corr_title") }) : null;
    let d = null, err = null;
    try { d = await api("label-reader/corrections"); } catch (e) { err = e; }
    if (L) L.done();
    if (err) {
      if (err.locked) return;
      if (on) { failed(err); el.hidden = false; }                 // switched off and unreadable: nothing is known to wait
    } else {
      setSeen.cw = Number(d.count) || 0;
      paint(d);
    }
    paintList();
    if (first && window.__swCorrArrived && !el.hidden && el.isConnected) { window.__swCorrArrived = false; el.scrollIntoView({ behavior: "auto", block: "start" }); }
  }
  later(load(true));
  return el;
}

// ----- Recently added (6.2, 6.4) -----
// the list's header: "Share corrections", On, and the link "<n> waiting" while it is on
function corrMeta() {
  if (!corrOn) return null;
  const box = h("span", {}, SW.t("recent_corr_meta"), " ", SW.status("on", { small: true }));
  corrCount().then((n) => { if (n) box.append(" ", corrLink(SW.t("recent_corr_meta_waiting", { count: n }))); });
  return box;
}
// after Save changes or Accept: what became of the correction, said in `slot` once the Hub has had time to queue it
function corrAfter(id, slot) {
  SW.result(slot, null);
  if (!corrOn) return;
  setTimeout(async () => {
    if (!slot.isConnected) return;
    try {
      const d = await api(histPath(id)), st = d && d.share;
      if (!st) return;
      if (st.state === "waiting") say(slot, [SW.t("recent_corr_waiting"), " ", corrLink(SW.t("recent_corr_link"))], "info");
      else if (st.state === "not_queued") say(slot, corrReason(st.reason) ? SW.t("recent_corr_not_taken", { reason: corrReason(st.reason) }) : SW.t("recent_corr_not_taken_plain"), "warn");
      else if (d.photos === 0) say(slot, SW.t("recent_corr_no_photo"), "info");
    } catch { /* the save stands; this line is only news */ }
  }, CORR_AFTER_MS);
}
