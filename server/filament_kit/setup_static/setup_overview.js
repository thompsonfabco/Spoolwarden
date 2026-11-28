// The Overview section: the header meta line, Still to do, the four tiles and the checks in groups.
// Loaded after setup.js; everything from the server goes in through textContent, as there.
"use strict";

const OVERVIEW = "overview";      // the section's name in TABS; its catalogue keys start with it
const CHECK_STATUS = { ok: "ok", fail: "needs_look" };      // anything else is a note
const CHECK_ORDER = { fail: 0, info: 1, ok: 2 };
// a fix that is a command (the Hub writes "run: sudo …" or "sudo …") is shown as a code block with Copy
const FIX_COMMAND = /^(?:run: )?((?:sudo|filament-kit) .+)$/;

// how long ago, as the catalogue says it; a day or more is the date
function ovAgo(ms) {
  const s = Math.max(0, Math.floor((Date.now() - ms) / 1000));
  if (s < 60) return SW.t("common_age_seconds", { count: s });
  if (s < 3600) return SW.t("common_age_minutes", { count: Math.floor(s / 60) });
  return s < 172800 ? SW.t("common_age_hours", { count: Math.floor(s / 3600) }) : fmtDate(ms / 1000);
}
const ovExact = (ms) => new Date(ms).toLocaleString();
// the relative time as a <time> with its exact time on hover; it keeps itself right while it is on the page
function ovTime(ms, text) {
  const t = h("time", { tabindex: "0", datetime: new Date(ms).toISOString(), textContent: text(ms) });
  const timer = setInterval(() => { if (t.isConnected) t.textContent = text(ms); else clearInterval(timer); }, 15000);
  return SW.tooltip(t, ovExact(ms));
}

// a command with Copy: `name` is what Copy says it copies
function ovCode(command) {
  const cap = SW.uid("cap");
  return h("figure", { class: "code" },
    h("pre", { class: "code__text", tabindex: "0", "aria-labelledby": cap }, h("code", { textContent: command })),
    h("figcaption", { class: "code__bar" }, h("span", { class: "code__caption mono", id: cap, textContent: SW.t("overview_command") }),
      h("span", { class: "result", role: "status" }),
      h("button", { class: "btn btn--ghost", type: "button", "data-action": "copy",
        "aria-label": SW.t("common_copy_named", { name: SW.t("overview_command_name"), value: command }) }, SW.icon("copy", "icon--s"), SW.t("common_copy"))));
}

// ----- Still to do: a warn mark, one sentence, one default button to the exact place -----
function todoItems() {
  const go = (key, label, to) => ({ text: SW.t(key), go: [label, to] });
  const todo = [];
  if (!S.code_set) todo.push({ text: SW.t("overview_todo_setup_code"), code: SW.t("common_setup_code_command") });
  if (S.printers.on && !S.printers.list.length) todo.push(go("overview_todo_printer", "overview_todo_printer_button", "#printers/add"));
  if (!S.devices.length) todo.push(go("overview_todo_phone", "overview_todo_phone_button", "#devices/pair"));
  if (!S.devices.length && !S.printers.list.length) todo.push(go("overview_todo_restore", "overview_todo_restore_button", "#settings/backups"));
  if (S.anthropic.ai_intake && !S.anthropic.set) todo.push(go("overview_todo_key", "common_open_settings", "#settings/label-reader"));
  if (S.passphrase.web && !S.passphrase.set) todo.push(go("overview_todo_passphrase", "common_open_settings", "#settings/passphrase"));
  return todo;
}
// `codeOnly`: while Getting started shows, only the setup-code row of the card stays drawn; the rest is the panel
function todoCard(codeOnly) {
  const todo = todoItems().filter((t) => !codeOnly || t.code);
  if (!todo.length) return null;
  return h("section", { class: "card overview__todo", "aria-labelledby": "overview-todo-title" },
    h("div", { class: "overview__head" }, h("h2", { id: "overview-todo-title", textContent: SW.t("overview_todo_title") }),
      SW.status("needs_look", { small: true, text: String(todo.length) })),
    h("ul", { class: "todo" }, ...todo.map((t) => h("li", { class: "todo__item" },
      h("span", { class: "status status--warn" }, SW.shape("s-triangle"), h("span", { class: "sr", textContent: SW.t("status_needs_look") })),
      h("div", { class: "todo__text" }, h("p", { textContent: t.text }), t.code ? ovCode(t.code) : null),
      t.go ? h("a", { class: "btn", href: t.go[1], textContent: SW.t(t.go[0]) }) : null))));
}

// ----- the four tiles: each a link to its section; "Switched off" when the feature is off -----
function tile(name, to, value, line, off) {
  return h("li", {}, h("a", { class: "tile", href: to, "data-state": off ? "off" : undefined },
    h("span", { class: "tile__name", textContent: name }),
    h("span", { class: "tile__value" + (off ? " muted" : ""), textContent: off ? SW.t("common_switched_off") : null }, off ? null : value),
    off || !line ? null : h("span", { class: "tile__line muted", textContent: line })));
}
// whether the Getting started panel shows: its Next step then has Overview's one filled button
let ovStartShown = false;
const fieldsCls = () => (ovStartShown ? "" : "btn--primary");
function overviewStart() {
  const checklist = h("div"), todo = h("div", {}, todoCard(true));
  gettingStartedCard(checklist, {
    hubName: S.server_name,
    onChange: (r) => {
      ovStartShown = Boolean(r?.show);
      todo.replaceChildren(todoCard(ovStartShown) || "");
      for (const b of document.querySelectorAll('[data-action="make-fields"]')) b.classList.toggle("btn--primary", !ovStartShown);
    },
    api: async (body) => {
      if (body === undefined) return api("start");
      let answer;
      await needCode(async () => { answer = await api("start", { ...body, code: theCode() }); });
      return answer;
    },
  });
  return h("div", {}, checklist, todo);
}
function printersTile() {
  const P = S.printers.on ? S.printers.list.filter((p) => p.configured) : [];
  const up = P.filter((p) => p.state === "connected").length, down = P.length - up;
  const missing = S.printers.list.length - P.length;
  const word = up ? "connected" : down ? "offline" : "not_configured";
  const line = down ? SW.t("overview_tile_printers_offline", { count: down })
    : missing ? SW.t("overview_tile_printers_not_configured", { count: missing }) : P.length ? "" : SW.t("overview_tile_printers_none");
  return tile(SW.t("nav_printers"), "#printers", SW.status(word, { text: SW.t("overview_tile_printers_connected", { count: up }) }), line, !S.printers.on);
}
function recentTile() {
  const t = tile(SW.t("nav_recently_added"), "#recent", h("span", { class: "skeleton__line", "aria-hidden": "true" }), "", false);
  const value = t.querySelector(".tile__value");
  const none = (key) => { value.textContent = SW.t(key); value.classList.add("muted"); };
  later(api("intake/history").then((d) => {
    const newest = d.records.find((r) => r.spools.length);
    if (!newest) { none("recent_empty_title"); return; }
    value.textContent = SW.t("spool_title", { id: newest.spools[0].spool_id });
    const at = Date.parse(newest.at);
    if (at) t.querySelector("a").append(h("span", { class: "tile__line muted" }, ovTime(at, ovAgo)));
  }, () => none("overview_tile_recent_error")));
  return t;
}
function devicesTile() {
  const list = tile(SW.t("nav_devices"), "#devices", SW.t("overview_tile_devices_paired", { count: S.devices.length }),
    SW.t("overview_tile_devices_readers", { count: S.readers.list.length }), false);
  later(api("devices").then((r) => {
    const count = r.devices.filter((d) => d.out_of_date).length;
    if (count) list.querySelector("a").append(SW.status("needs_look", { small: true, text: SW.t("overview_tile_devices_outdated", { count }) }));
  }, () => {}));
  return list;
}
// the Label Reader tile's last line, "<n> corrections waiting to be shared": only while Share corrections is on and any wait; the only
// reminder there is (no toast, no mark). The tile already goes to Settings > Label Reader; the line also asks the page to scroll to the card
function readerTile() {
  const cloud = S.cloud_reads;
  const t = S.reader_install_pending
    ? tile(SW.t("common_label_reader"), "#settings/label-reader", SW.t("features_reader_not_installed"), SW.t("features_reader_finish"), false)
    : tile(SW.t("common_label_reader"), "#settings/label-reader", SW.t("overview_tile_reader_value", { used: cloud.used, limit: cloud.limit }),
      SW.t("overview_tile_reader_line"), !S.anthropic.ai_intake);
  later(corrSaved().then(async (on) => {
    const n = on ? await corrCount() : 0;
    if (n) t.querySelector("a").append(h("span", { class: "tile__line tile__corr", "data-corr-line": "",
      on: { click: () => { window.__swCorrArrived = true; } }, textContent: SW.t("overview_tile_reader_corrections", { count: n }) }));
  }));
  return t;
}
function tiles() {
  return h("ul", { class: "tiles", "aria-label": SW.t("overview_tiles_label") }, printersTile(), recentTile(), devicesTile(), readerTile());
}

// ----- the header meta line: the version, the setup, an update, the job, and when it was checked -----
const SETUP_NAME = { server: "overview_setup_hub", "all-in-one": "features_setup_all_in_one", station: "features_setup_station" };
function jobTitle(name) {
  const key = "job_title_" + name;
  return SW.t(SW.t(key) === key ? "job_title_other" : key);
}
function metaLine(r, checkedAt) {
  const u = r.update;
  const job = r.job && r.job.running ? h("a", { href: "#job", textContent: SW.t("overview_job_running", { job_title: jobTitle(r.job.name) }),
    on: { click: (e) => { e.preventDefault(); hubJobs().chip.click(); } } }) : null;
  return h("p", { class: "page-head__meta" },
    h("span", { textContent: SW.t("overview_meta_version", { version: r.version_text || r.version }) }),
    h("span", { textContent: SW.t("common_setup_line", { setup: SW.t(SETUP_NAME[S.mode] || "features_setup_custom") }) }),
    u.newer ? h("a", { href: "#updates" }, SW.status("note", { text: SW.t("nav_summary_updates_available", { latest: u.latest }) })) : null,
    job,
    h("span", { class: "overview__checked" }, ...SW.t("overview_meta_checked", { time: "\u0001" }).split("\u0001").flatMap((x, i) => (i ? [ovTime(checkedAt, ovAgo), x] : [x]))));
}

// the half-install banner: when the last install stopped halfway and no job runs
function halfBanner(r) {
  const u = r.update;
  if (!(u.half_install && !r.job.running)) return null;
  const b = SW.banner({ kind: "warn", title: SW.t("overview_half_title"), text: SW.t("overview_half_body"), inline: true });
  b.classList.add("overview__half");
  b.append(ovCode(u.half_install));
  return b;
}

// ----- history (02-components 7.11): the strips of the Checks' "Last 7 days" column, Show history and Show 30 days -----
// The card's range and what `health` answered for it; `state` is loading, ready or failed (then the strips are left out). The
// component is here, not in components.js: that file and its stylesheet are at the size limit of foundations 12 rule 7.
const ovHist = { days: 7, data: null, state: "loading", token: 0 };
const ovPad = (n) => String(n).padStart(2, "0");
// a moment as `2026-11-11 23:10`, in the browser's time
function ovStamp(iso) {
  const d = new Date(iso);
  return `${d.getFullYear()}-${ovPad(d.getMonth() + 1)}-${ovPad(d.getDate())} ${ovPad(d.getHours())}:${ovPad(d.getMinutes())}`;
}
const ovSince = (d) => SW.t("overview_strip_since", { date: ovStamp(d.since || d.to).slice(0, 10) });
// "<check> <from> to <to>", "now" where the stretch still lasts; with `nodes` the times are <time> in mono, for the tooltip
function stripSentence(check, span, end, nodes) {
  const from = ovStamp(span.from), to = span.to === end ? SW.t("overview_strip_now") : ovStamp(span.to);
  if (!nodes) return SW.t("overview_strip_span", { check, from, to });
  return SW.t("overview_strip_span", { check, from: "\u0001", to: "\u0002" }).split(/([\u0001\u0002])/)
    .map((x) => (x === "\u0001" ? h("time", { class: "mono", textContent: from }) : x === "\u0002" ? h("time", { class: "mono", textContent: to }) : x));
}
// a touch that ends outside a strip hides its tooltip
document.addEventListener("pointerup", (e) => {
  for (const tip of document.querySelectorAll(".history-strip__tip:not([hidden])")) if (!tip.parentElement.contains(e.target)) tip.hidden = true;
});
// one strip: `entry` is the check's {spans} from `health` (none: it was fine all along), `data` the whole answer; gives its nodes
function historyStrip(entry, data, text, days) {
  const start = Date.parse(data.from), whole = Date.parse(data.to) - start || 1;
  const at = (iso) => Math.min(100, Math.max(0, ((Date.parse(iso) - start) / whole) * 100));
  const spans = (entry ? entry.spans : []).slice().sort((a, b) => Date.parse(a.from) - Date.parse(b.from));
  const gap = data.since ? at(data.since) : 100;          // the history starts here; before it the track is a dashed line
  // a stretch is never narrower than --s1 (its width is that at least) and never starts less than that from the end
  const place = (el, from, to) => { el.style.insetInlineStart = `min(${from.toFixed(2)}%, calc(100% - var(--s1)))`; el.style.width = `${(to - from).toFixed(2)}%`; return el; };
  const marks = spans.map((sp) => place(h("span", { class: "history-strip__span", "data-state": sp.state }), at(sp.from), at(sp.to)));
  const plain = h("span", { class: "history-strip__plain" }), none = h("span", { class: "history-strip__none" });
  plain.style.insetInlineStart = `${gap.toFixed(2)}%`;
  none.style.width = `${gap.toFixed(2)}%`;
  const track = h("span", { class: "history-strip__track", "aria-hidden": "true" }, plain, gap > 0 ? none : null, ...marks);
  const id = SW.uid("tip"), tip = h("span", { class: "tooltip history-strip__tip", role: "tooltip", id, hidden: true });
  const label = spans.length
    ? SW.t("overview_strip_label_look", { check: text, days, count: spans.length, sentence: stripSentence(text, spans[spans.length - 1], data.to) })
    : SW.t("overview_strip_label_fine", { check: text, days });
  const strip = h("span", { class: "history-strip", role: "img", tabindex: "0", "aria-describedby": id, "aria-label": label },
    track, h("span", { class: "history-strip__caption muted", textContent: SW.t("overview_strip_caption", { days }) }));
  const wrap = h("span", { class: "tip history-strip__wrap" }, strip, tip);
  let cur = -1, timer = 0;
  const show = (i) => {
    clearTimeout(timer); cur = i;
    tip.replaceChildren(...stripSentence(text, spans[i], data.to, true));
    tip.hidden = false;
    tip.dataset.place = wrap.getBoundingClientRect().top < tip.offsetHeight + 16 ? "below" : "above";
  };
  const hide = () => { clearTimeout(timer); tip.hidden = true; };
  const arm = (fn, ms) => { clearTimeout(timer); timer = setTimeout(fn, ms); };
  const under = (e) => { const i = marks.indexOf(e.target); return i < 0 ? spans.length - 1 : i; };
  strip.addEventListener("mouseover", (e) => { const i = marks.indexOf(e.target); if (i >= 0) arm(() => show(i), 500); });
  strip.addEventListener("mouseleave", () => arm(hide, 100));
  tip.addEventListener("mouseenter", () => clearTimeout(timer));
  // a long press: a touch that stays; the finger arriving is the pointer's first event, so no press-down handler is needed
  strip.addEventListener("pointerover", (e) => { if (e.pointerType === "touch" && spans.length) arm(() => show(under(e)), 500); });
  for (const ev of ["pointerup", "pointercancel"]) strip.addEventListener(ev, () => clearTimeout(timer));
  strip.addEventListener("focus", () => { if (spans.length && strip.matches(":focus-visible")) show(spans.length - 1); });
  strip.addEventListener("blur", hide);
  strip.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !tip.hidden) { hide(); e.stopPropagation(); return; }
    if (!spans.length || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
    e.preventDefault();
    const to = tip.hidden || cur < 0 ? spans.length - 1 : cur + (e.key === "ArrowRight" ? 1 : -1);
    show(Math.min(spans.length - 1, Math.max(0, to)));
    SW.announce(stripSentence(text, spans[cur], data.to));
  });
  return [wrap, gap > 0 ? h("span", { class: "history-strip__since muted", textContent: ovSince(data) }) : null];
}
// the empty cell of a row (a row with no key has none, and none is filled while the history cannot be read)
const ovSlot = (r) => h("div", { class: "history", "data-strip": r.key || "", "data-text": r.text });
// draws every strip of `box` from ovHist; the column's title says the range
function paintStrips(box) {
  for (const th of box.querySelectorAll('.coll__head [data-col="history"]')) th.textContent = SW.t("overview_strip_caption", { days: ovHist.days });
  const by = new Map(((ovHist.data && ovHist.data.checks) || []).map((c) => [c.key, c]));
  for (const slot of box.querySelectorAll("[data-strip]")) {
    if (!slot.dataset.strip || ovHist.state === "failed") slot.replaceChildren();
    else if (ovHist.state === "loading") slot.replaceChildren(h("span", { class: "skeleton__line history__wait", "aria-hidden": "true" }));
    else slot.replaceChildren(...historyStrip(by.get(slot.dataset.strip), ovHist.data, slot.dataset.text, ovHist.days).filter(Boolean));
  }
}
// "History of the checks" (#overview/history): the changes of the card's range as a table, newest first
function ovHistoryTable(d, days) {
  const word = (s) => SW.t(s === "bad" ? "overview_history_bad" : s === "look" ? "status_needs_look" : "status_ok");
  const rows = [...(d.changes || [])].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  const cols = [["time", "overview_history_col_time"], ["check", "overview_col_check"], ["from", "overview_history_col_from"], ["to", "overview_history_col_to"]];
  const since = !d.since || Date.parse(d.since) > Date.parse(d.from) ? h("p", { class: "history__foot muted", textContent: ovSince(d) }) : null;
  return h("div", { class: "history__body" },
    rows.length ? h("table", { class: "history__table" },
      h("thead", {}, h("tr", {}, cols.map(([col, key]) => h("th", { scope: "col", "data-col": col, textContent: SW.t(key) })))),
      h("tbody", {}, rows.map((c) => h("tr", {}, h("td", {}, h("time", { class: "mono", datetime: c.at, textContent: ovStamp(c.at) })),
        h("td", { textContent: c.text || c.key }), h("td", { textContent: word(c.from) }), h("td", { textContent: word(c.to) })))))
      : h("p", { class: "muted", textContent: SW.t("overview_history_empty", { days }) }),
    rows.length ? h("p", { class: "history__foot muted", textContent: SW.t("overview_history_foot", { days }) }) : null, since);
}
async function ovHistoryBody(region) {
  const days = ovHist.days, title = SW.t("overview_history_error", { reason: "" }).trim();
  const L = SW.loading(region, { thing: SW.t("overview_history_title"), items: 3, onTry: () => ovHistoryBody(region), title });
  try {
    L.done(ovHistoryTable(await api("health?days=" + days), days));
  } catch (e) {
    L.done(SW.errorState({ title, text: hubText(e.message), onTry: () => ovHistoryBody(region) }));
  }
}
AX_PLACES.history = (opener) => axDialog("history", SW.t("overview_history_title"), ovHistoryBody, opener, SW.phone());

// ----- the checks: the Hub's rows in groups, a detail row under the row before it -----
function checkItems(rows) {
  const out = [];
  for (const r of rows) {
    if (r.state === "detail" && out.length) out[out.length - 1].details.push(r.text);
    else out.push({ ...r, state: r.state === "detail" ? "info" : r.state, details: [], group: r.group || "" });
  }
  return out;
}
// the fix column: the row's action button, else its fix (a command as a code block)
function fixCell(r) {
  if (r.action === "storage_use") return ovStorageFix();      // the Storage row's panel, whatever its state (setup_overview_alerts.js)
  if (r.state === "ok") return null;
  if (r.action === "ensure_fields") {
    const box = boxFor("check:" + r.action);
    return h("div", { class: "actions" }, SW.btn(SW.t("overview_make_fields"), { cls: fieldsCls(), action: "make-fields", run: () => change(box, "fields", {}, (d) => say(box,
      d.made.length ? SW.t("overview_made_fields", { made: d.made.join(", ") }) : SW.t("overview_fields_present"), true)) }));
  }
  if (!r.fix) return null;
  const m = FIX_COMMAND.exec(r.fix);
  if (!m) return h("p", { textContent: hubText(r.fix) });
  const [command, ...rest] = m[1].split(/\s{2,}/);
  return h("div", {}, ovCode(command), rest.length ? h("p", { class: "muted", textContent: rest.join(" ") }) : null);
}
const checkCell = (col, prio, kids, key) => h("div", { class: "coll__cell", role: "cell", "data-col": col, "data-prio": prio },
  key ? h("span", { class: "coll__key", "aria-hidden": "true", textContent: key }) : null, kids);
function checkItem(r, i) {
  const fix = fixCell(r);
  const box = r.action ? boxFor("check:" + r.action) : null;
  const item = h("div", { class: "coll__item", role: "rowgroup", "data-key": "check-" + i, "data-state": r.state },
    h("div", { class: "coll__row", role: "row" },
      checkCell("status", 1, SW.status(CHECK_STATUS[r.state] || "note", { small: true, prefix: true })),
      checkCell("title", 1, [h("span", { textContent: r.text }), ...r.details.map((d) => h("p", { class: "muted coll__detail", textContent: d }))]),
      fix ? checkCell("fix", 2, fix, SW.t("common_fix")) : null,
      checkCell("history", 2, ovSlot(r))));
  if (box) item.append(h("div", { class: "coll__row coll__row--result", role: "row", hidden: !box.dataset.state },
    h("div", { class: "coll__cell", role: "cell", "aria-colspan": 4 }, box)));
  return item;
}
function checksTable(items, label) {
  const cols = [["status", "common_field_status", 1], ["title", "overview_col_check", 1], ["fix", "common_fix", 2], ["history", "overview_strip_caption", 2]];
  const kids = [];
  let group = null;
  items.forEach((r) => {
    if (r.group && r.group !== group) {
      group = r.group;
      kids.push(h("div", { class: "coll__item coll__group", role: "rowgroup" }, h("div", { class: "coll__row", role: "row" },
        h("div", { class: "coll__cell", role: "cell", "aria-colspan": 4, textContent: group }))));
    }
    kids.push(checkItem(r, r.index));
  });
  return h("div", { class: "coll coll--checks", role: "table", "aria-label": label },
    h("div", { class: "coll__head", role: "rowgroup" }, h("div", { class: "coll__row", role: "row" }, cols.map(([col, key, prio]) =>
      h("div", { class: "coll__cell", role: "columnheader", "data-col": col, "data-prio": prio, textContent: SW.t(key, { days: ovHist.days }) })))),
    kids);
}
// groups in the order of their first row; the worst state first within a group, and the groups with a problem first
function grouped(items) {
  const groups = new Map();
  for (const r of items) {
    if (!groups.has(r.group)) groups.set(r.group, []);
    groups.get(r.group).push(r);
  }
  const worst = (list) => Math.min(...list.map((r) => CHECK_ORDER[r.state] ?? 1));
  return [...groups.values()].sort((a, b) => worst(a) - worst(b))
    .flatMap((list) => [...list].sort((a, b) => (CHECK_ORDER[a.state] ?? 1) - (CHECK_ORDER[b.state] ?? 1)));
}
function checksView(rows) {
  const items = checkItems(rows).map((r, index) => ({ ...r, index }));
  const bad = items.filter((r) => r.state === "fail"), ok = items.filter((r) => r.state === "ok");
  const shown = items.filter((r) => r.state !== "ok");
  const label = SW.t("overview_checks_title");
  const fold = ok.length ? h("details", { class: "overview__ok", open: !bad.length },
    h("summary", { textContent: SW.t("overview_show_ok", { count: ok.length }) }), checksTable(grouped(ok), label)) : null;
  return { bad: bad.length, ok: ok.length, nodes: [!bad.length ? h("p", { class: "overview__clear", textContent: SW.t("overview_all_ok") }) : null,
    shown.length ? checksTable(grouped(shown), label) : null, fold] };
}

function overviewTab() {
  const banner = h("div"), body = h("div"), badges = h("span", { class: "overview__badges" });
  const note = h("div", { role: "status" });     // the hidden-values note
  const histNote = h("div", { class: "overview__history", role: "status" });      // "Could not read the history", with Try again
  Object.assign(ovHist, { days: 7, data: null, state: "loading", token: ovHist.token + 1 });
  let checkedAt = Date.now();
  const show = (r) => {
    checkedAt = Date.now();
    const view = checksView(r.rows);
    if (page.dataset.section === OVERVIEW) pageHead(metaLine(r, checkedAt), head);      // not once another section is open
    banner.replaceChildren(...[halfBanner(r)].filter(Boolean));
    const marks = [];
    if (view.bad) marks.push(SW.status("needs_look", { small: true, text: SW.t("overview_badge_look", { count: view.bad }) }));
    if (view.ok || !view.bad) marks.push(SW.status("ok", { small: true, text: view.ok ? SW.t("overview_badge_ok", { count: view.ok }) : undefined }));
    badges.replaceChildren(...marks);
    fill(body, ...view.nodes);
    paintStrips(body);
    note.replaceChildren(...(r.redacted ? [lockNote("lock_hidden_note")] : []));
  };
  // addresses and serials load by themselves once the page is open, and are read again without the code when it locks
  const read = (open) => {
    const done = readRegion(body, OVERVIEW, () => (open ? api("overview", { code: theCode() }) : api("overview")), show);
    const sr = body.querySelector(".skeleton .sr");
    if (sr) sr.textContent = SW.t("overview_loading");      // the skeleton says what the design, Hub page says
    return done;
  };
  let open = SW.lock.state !== "locked";
  const again = SW.btn(SW.t("overview_run_checks"), { cls: "btn--ghost", icon: "refresh", run: async () => {
    SW.busy(again, SW.t("common_checking"));
    try { await read(open); } finally { SW.idle(again); }
  } });
  // the strips come from `health`; a refusal leaves them out and the table stays
  const readHistory = async () => {
    const token = ++ovHist.token;
    Object.assign(ovHist, { data: null, state: "loading" });
    histNote.replaceChildren();
    paintStrips(body);
    try {
      const d = await api("health?days=" + ovHist.days);
      if (token === ovHist.token) Object.assign(ovHist, { data: d, state: "ready" });
    } catch (e) {
      if (token !== ovHist.token) return;
      ovHist.state = "failed";
      histNote.replaceChildren(SW.banner({ kind: "warn", inline: true, text: SW.t("overview_history_error", { reason: hubText(e.message) }),
        actions: [SW.btn(SW.t("common_try_again"), { action: "history-again", run: readHistory })] }));
    }
    if (token === ovHist.token) paintStrips(body);
  };
  const days = SW.btn(SW.t("overview_history_30"), { cls: "btn--ghost", action: "history-days", run: () => {
    ovHist.days = ovHist.days === 7 ? 30 : 7;
    days.textContent = SW.t(ovHist.days === 7 ? "overview_history_30" : "overview_history_7");
    return readHistory();
  } });
  const head = h("div", { class: "page-head__actions" }, again);      // Run the checks again, in the section header
  pageHead(null, head);
  whenOpen(body, (now) => { open = now; return read(now); });
  later(readHistory());
  if (S.reader_install_pending === false) window.__swReaderLeftEarly = false;
  const reader = S.reader_install_pending && window.__swReaderLeftEarly === true ? h("div", { class: "banner banner--warn" },
    SW.shape("s-triangle"), h("p", { class: "banner__text", textContent: SW.t("reader_not_installed") }),
    h("a", { class: "btn", href: "#settings/label-reader", textContent: SW.t("features_reader_finish") })) : null;
  return [banner, reader, overviewStart(), ovLowCard(), tiles(), hubFilamentLine(),
    h("section", { class: "card overview__checks", "aria-labelledby": "overview-checks-title" },
      h("div", { class: "overview__head" }, h("h2", { id: "overview-checks-title", textContent: SW.t("overview_checks_title") }),
        SW.btn(SW.t("overview_history_show"), { cls: "btn--ghost", action: "history-show", run: (e) => openPlace("history", e.currentTarget) }), days, badges),
      histNote, body), note];
}
