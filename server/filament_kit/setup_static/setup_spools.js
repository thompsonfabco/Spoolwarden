// The Spools section of the Hub page: the spools as a Collection with a search field and
// four chips, 50 rows at a time, and a spool's detail with Update weight, Move, Link the last tapped tag and Mark empty. Every change
// shows what it would do first (the Hub's dry run) and is applied from that preview; looking needs no code, a change does.
// Loaded after setup.js (it uses its api, needCode, theCode and say); everything from the Hub goes in through textContent, as there.
// The section is `#spools`, one spool `#spools/<number>`. Its registration (a TABS entry, GROUPS, the script and style tags) is the
// registry step's: this file only offers `spoolsTab()`.
"use strict";
(() => {
const PAGE = 50;
const SECTION = "spools";

// ----- texts -----
const tx = (key, vars) => SW.t(key, vars);

// ----- small helpers -----
const spX = (...args) => SW.h(...args);
const spE = (tag, cls, ...kids) => {
  const props = kids[0] && kids[0].constructor === Object ? kids.shift() : {};
  return spX(tag, { class: cls, ...props }, ...kids);
};
const spName = (v) => [v.vendor, v.name].filter(Boolean).join(" ") || tx("spools_unnamed");
// who did it: the actor's name, else the Hub's word for its type (the Logs' Who words), never a raw type
const SP_WHO = { page: "activity_who_page", cli: "activity_who_cli", hub: "activity_who_hub", device: "activity_who_device", connector: "activity_who_connector" };
const spWho = (actor) => (actor && actor.name) || SW.t(SP_WHO[actor && actor.type] || "activity_who_unknown");
const sphexOf = (v) => (/^[0-9A-Fa-f]{6}$/.test(v.color_hex || "") ? "#" + v.color_hex : "");
const num = (n) => (typeof n === "number" && Number.isFinite(n) ? n : null);
const grams = (n) => (num(n) == null ? null : Math.round(n * 10) / 10);
const levelOf = (v) => (num(v.remaining_g) != null && num(v.net_g) > 0 ? v.remaining_g / v.net_g : null);
const pad2 = (n) => String(n).padStart(2, "0");
const when = (iso) => { const d = new Date(iso); return Number.isNaN(d.getTime()) ? String(iso || "") : `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`; };
const ago = (iso) => {
  const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  return s < 90 ? tx("common_age_seconds", { count: s }) : s < 5400 ? tx("common_age_minutes", { count: Math.round(s / 60) }) : when(iso);
};
const slotBox = () => spE("p", "result result--block", { role: "status" });
const spCanCO = () => Array.isArray(st.filters) && st.filters.includes("checked_out");       // an older Hub's answer has no `filters`: none of Checked out, Return, Home
const spaddressOf = (id) => `#${SECTION}${id != null ? "/" + id : ""}`;
const addressId = () => { const m = /^#spools\/(\d+)$/.exec(location.hash); return m ? Number(m[1]) : null; };

// ----- Undo: the answer of a change names its event; the toast says what happened and offers Undo for 10 seconds -----
const spEventId = (r) => {
  const e = r && r.event;
  if (e && e.id != null) return e.undo && e.undo.possible === false ? null : e.id;
  return r && r.event_id != null ? r.event_id : null;      // the Hub's own words for it; none: no Undo
};
// Undo answers "Put back." (and the Hub's note), or the Hub's refusal (409) in its words
const spUndo = (id, view) => needCode(async () => {
  try {
    const r = await api(`events/${encodeURIComponent(id)}/undo`, { code: theCode() });
    SW.toast([tx("spools_put_back"), r.note].filter(Boolean).join(" "), "ok");
    const one = await api(`spools/${view.v.id}`).catch(() => null);
    if (one && one.spool) view.update(one.spool);
    view.events(true);
    spRecount();
  } catch (e) {
    if (e.locked) throw e;
    const raw = e.data && e.data.error && e.data.error !== "bad origin" ? e.data.error : "";
    if (e.status === 409) SW.toast(raw && !/^[a-z0-9_]+$/.test(raw) ? hubText(raw) : tx("spools_undo_refused"), "warn");
    else SW.toast(tx("spools_undo_failed", { reason: hubText(e.message) }), "bad");
  }
});
function spToast(text, r, view) {
  const id = hubHasHistory("event_undo") ? spEventId(r) : null;
  SW.toast(text, "ok", id == null ? {} : { action: { label: tx("common_undo"), name: tx("spools_undo_name", { what: text }), run: () => spUndo(id, view) } });
}

// ----- Return: sends a checked-out spool back to its home; asks nothing; the result is an Undo toast -----
const spReturn = (id, button) => needCode(async () => {
  const view = views.get(id);
  if (!view) return;
  SW.busy(button, tx("common_working"));
  try {
    const r = await api(`spools/${id}/return`, { code: theCode() });
    view.update(r.spool);
    const msg = r.location ? tx("spools_returned_to", { location: r.location }) : tx("spools_returned");
    spToast(r.printer_notified === false ? `${msg} ${tx("spools_return_printer_not_told")}` : msg, r, view);
    view.events(true);
    spRecount();
  } catch (e) {
    SW.idle(button);
    if (e.locked) throw e;
    SW.toast(hubText(e.message), "warn");           // the Hub's sentence ("This spool is not checked out.")
    spRecount();
  }
});

// ----- Download as CSV: Spoolman's own files, passed on by the Hub; unlocked, as a file leaves the Hub -----
async function spDownloadCsv(set, fallback) {
  const r = await fetch(`/setup/api/export/${set}.csv`, { credentials: "same-origin", headers: { "X-Setup-Code": theCode() } });
  if (!r.ok) { let d = {}; try { d = await r.json(); } catch { /* not JSON */ } throw hubError(r.status, d); }
  const m = /filename="([^"]+)"/.exec(r.headers.get("Content-Disposition") || "");
  const url = URL.createObjectURL(await r.blob());
  const a = spX("a", { href: url, download: m ? m[1] : fallback });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
const spCsvItem = (set, key) => ({ label: tx(key), icon: "download", action: `download-${set}`,
  run: () => needCode(async () => { try { await spDownloadCsv(set, set + ".csv"); } catch (e) { if (e.locked) throw e; SW.toast(tx("spools_csv_failed", { reason: hubText(e.message) }), "bad"); } }) });

// ----- Print a label / Print labels: the Hub's print page opens in a new tab with the size choice and a preview; open, so no code -----
const spPrintAt = (ids) => window.open(`/setup/api/labels?${new URLSearchParams({ ids: ids.join(","), size: "62x29" })}`, "_blank", "noopener");

// ----- what the section knows -----
const st = { q: "", filter: "", rows: [], total: 0, more: false, url: null, ready: false, filters: null, checked: null };
const wanted = new Set();            // the rows that are open: kept across a new read
const chosen = new Set();            // the spools selected for Print labels: kept by Show more, dropped by a new read
let lastPick = null;                 // the spool whose checkbox was pressed last: Shift+click selects from it
const views = new Map();             // spool number -> {v, update(v), ...}: the parts of a drawn row that a change patches
let box = null;                      // the section's region
let parts = null;                    // {head, sel, list, foot}
let seq = 0;                         // the newest read: an older answer that comes later is dropped
let storage = null;                  // the storage list for Move, read once per section drawing

const queryOf = (offset) => `spools?${new URLSearchParams({ ...(st.q ? { q: st.q } : {}), ...(st.filter ? { filter: st.filter } : {}), offset, limit: PAGE })}`;

// ----- the list -----
const spPICK = "select";             // the column of the checkboxes (its name is the collection's reserved one, the design, components)
const spCOLUMNS = [[spPICK, "spools_select_all", 1], ["expand", "common_details", 1, true], ["visual", "spools_col_colour", 1, true], ["number", "spools_col_number", 1], ["title", "spools_col_spool", 1],
  ["material", "common_field_material", 2], ["left", "spools_col_left", 1], ["where", "spools_col_where", 3], ["state", "common_field_state", 1]];
const spCell = (col, prio, ...kids) => spX("div", { class: "coll__cell", role: "cell", "data-col": col, "data-prio": prio }, ...kids);
const key = (k) => spE("span", "coll__key", { "aria-hidden": "true" }, tx(k));

// a checked-out row: where the spool is, its home and since when, and Return (asks nothing; the toast offers Undo). They sit under
// the spool's name, a cell every look and width keeps in the row (the Where cell moves into the expansion on a tablet and phone)
const spHomeText = (v) => (v.home ? tx("spools_home", { home: v.home }) : tx("spools_home_none"));
function spCheckedLines(v) {
  const what = v.home ? tx("spools_return_to", { home: v.home }) : tx("spools_return");
  return [v.location && v.loaded ? spE("span", "coll__sub spools-in", tx("spools_in_place", { place: v.location })) : null,
    spE("span", "coll__sub spools-home", spHomeText(v)),
    v.checked_out_since ? spE("span", "coll__sub spools-since", tx("spools_since", { at: when(v.checked_out_since) })) : null,
    spX("button", { class: "btn btn--ghost spools-return", type: "button", "data-id": String(v.id), "aria-label": tx("spools_return_name", { what, name: spName(v) }), text: what })];
}
function cellsOf(v) {
  const left = grams(v.remaining_g);
  return {
    visual: [SW.swatch(sphexOf(v), { level: levelOf(v) })],
    number: [spE("span", "mono", `#${v.id}`)],
    title: [spName(v)],
    material: [key("common_field_material"), v.material || null],
    left: [key("spools_col_left"), left == null ? spE("span", "muted", tx("spools_not_weighed")) : spE("span", "mono", tx("common_grams", { grams: left }))],
    where: [key("spools_col_where"), v.location ? v.location : spE("span", "muted", tx("spools_no_place"))],
    state: [SW.status(v.state === "sealed" ? "note" : v.state === "opened" ? "ok" : "off", { small: true, text: tx(v.state === "sealed" ? "common_sealed" : v.state === "opened" ? "spool_state_opened" : "common_empty") }),
      v.low ? SW.status("needs_look", { small: true, text: tx("spools_low") }) : null,
      spE("span", "coll__sub", tx(v.tag ? "spools_tagged" : "spools_untagged"))],
  };
}
function spsetCell(c, kids) {
  const k = c.querySelector(":scope > .coll__key");
  c.replaceChildren(...(k && !kids.some((x) => x && x.classList && x.classList.contains("coll__key")) ? [k] : []), ...kids.flat().filter((x) => x != null && x !== false));
}

function item(v) {
  const id = `spool-detail-${v.id}`, open = wanted.has(v.id) && SW.profile().detail !== "sheet";
  const view = { v };
  views.set(v.id, view);
  const pick = SW.check({ label: tx("spools_select_name", { name: spName(v) }), sr: true, checked: chosen.has(v.id) });
  pick._control.classList.add("spools-pick");
  pick._control.dataset.id = String(v.id);
  const expand = spX("button", { class: "btn btn--ghost btn--icon coll__expand", type: "button", "aria-expanded": String(open), "aria-controls": id, "aria-label": tx("detail_for", { name: spName(v) }) },
    SW.icon("chevron"), spE("span", "spools-expand__label", tx("common_details")));
  const co = spE("span", "spools-co");
  const title = spX("div", { class: "coll__cell", role: "cell", "data-col": "title", "data-prio": 1 }, spX("a", { class: "coll__title", href: spaddressOf(v.id) }), co);
  const c = { title, visual: spCell("visual", 1), number: spCell("number", 1), material: spCell("material", 2), left: spCell("left", 1), where: spCell("where", 3), state: spCell("state", 1) };
  const region = detail(v, view, id);
  view.update = (nv) => {
    view.v = v = nv;
    const k = cellsOf(nv);
    title.querySelector("a").textContent = k.title[0];
    co.replaceChildren(...(spCanCO() && nv.checked_out ? spCheckedLines(nv) : []));
    pick.querySelector(".check__label").textContent = tx("spools_select_name", { name: spName(nv) });
    for (const col of ["visual", "number", "material", "left", "where", "state"]) spsetCell(c[col], k[col]);
    it.dataset.state = nv.state === "empty" ? "off" : "";
    if (!it.dataset.state) delete it.dataset.state;
    view.paintDetail(nv);
  };
  if (open) view.events();
  const it = spX("div", { class: "coll__item", role: "rowgroup", "data-id": String(v.id) },
    spE("div", "coll__row", { role: "row" }, spCell(spPICK, 1, pick), spCell("expand", 1, expand), c.visual, c.number, title, c.material, c.left, c.where, c.state),
    spE("div", "coll__row coll__row--detail", { role: "row", hidden: !open }, spX("div", { class: "coll__cell", role: "cell", "aria-colspan": spCOLUMNS.length }, region)));
  const k = cellsOf(v);
  title.querySelector("a").textContent = k.title[0];
  co.replaceChildren(...(spCanCO() && v.checked_out ? spCheckedLines(v) : []));
  for (const col of ["visual", "number", "material", "left", "where", "state"]) spsetCell(c[col], k[col]);
  if (v.state === "empty") it.dataset.state = "off";
  if (chosen.has(v.id)) it.dataset.selected = "true";
  return it;
}

function table(rows) {
  const all = SW.check({ label: tx("spools_select_all"), sr: true });
  all._control.classList.add("spools-pickall");
  const head = spE("div", "coll__head", { role: "rowgroup" }, spE("div", "coll__row", { role: "row" }, spCOLUMNS.map(([col, k, prio, hidden]) => spX("div",
    { class: "coll__cell", role: "columnheader", "data-col": col, "data-prio": prio }, col === spPICK ? all : hidden ? spE("span", "sr", tx(k)) : tx(k)))));
  views.clear();
  const el = spX("div", { class: "coll coll--spools", role: "table", "aria-label": tx("nav_spools") }, head, rows.map((v) => item(v)));
  SW.collection(el, { name: SECTION, total: st.total, first: 0, emptyPane: tx("spools_pane_empty") });
  el.addEventListener("click", (e) => {
    const ret = e.target.closest(".spools-return");
    if (ret) { spReturn(Number(ret.dataset.id), ret); return; }
    const box = e.target.closest(".spools-pick"), allBox = e.target.closest(".spools-pickall");
    if (box || allBox) {
      const ids = st.rows.map((v) => v.id);
      if (allBox) for (const id of ids) (allBox.checked ? chosen.add(id) : chosen.delete(id));
      else {
        const id = Number(box.dataset.id), a = ids.indexOf(lastPick), b = ids.indexOf(id);
        const span = e.shiftKey && a >= 0 ? ids.slice(Math.min(a, b), Math.max(a, b) + 1) : [id];
        for (const x of span) (box.checked ? chosen.add(x) : chosen.delete(x));
        lastPick = id;
      }
      spSync(true);
      return;
    }
    const link = e.target.closest("a.coll__title"), btn = e.target.closest(".coll__expand") || (link && link.closest(".coll__item").querySelector(".coll__expand"));
    if (!btn) return;
    if (link) { e.preventDefault(); btn.click(); return; }
    const id = Number(btn.getAttribute("aria-controls").replace("spool-detail-", "")), open = btn.getAttribute("aria-expanded") === "true";
    if (open && views.has(id)) views.get(id).events();
    if (open) {
      if (SW.phone()) for (const o of el.querySelectorAll('.coll__expand[aria-expanded="true"]')) if (o !== btn) { wanted.delete(Number(o.getAttribute("aria-controls").replace("spool-detail-", ""))); o.click(); }
      wanted.add(id);
    } else wanted.delete(id);
    if (/^#spools(\/\d*)?$/.test(location.hash)) history.replaceState(null, "", open ? spaddressOf(id) : spaddressOf());
  });
  return el;
}

// ----- the header, the search and the chips -----
function spHeader() {
  const meta = spE("p", "page-head__meta spools-head", spE("span", "spools-count", tx("spools_count", { count: st.total })));
  if (st.url) meta.append(spX("a", { class: "btn btn--ghost spools-link", href: st.url, target: "_blank", rel: "noopener" }, tx("spools_open_spoolman")));
  const csv = [hubCanExport("spoolman-spools") ? spCsvItem("spoolman-spools", "spools_csv_spools") : null,
    hubCanExport("spoolman-filaments") ? spCsvItem("spoolman-filaments", "spools_csv_filaments") : null].filter(Boolean);
  const more = SW.iconBtn("more", tx("spools_more_actions"), { cls: "spools-more" });
  SW.menu(more, () => [
    ...(csv.length ? [{ heading: tx("spools_download_csv") }, ...csv, { separator: true }] : []),
    { label: SW.t("csv_import_menu"), icon: "upload", action: "import-csv",
      run: () => needCode(() => window.SW_CSV_IMPORT && window.SW_CSV_IMPORT({ kind: "spools", opener: more, onDone: () => spLoad() })) },
  ], { label: tx("spools_more_actions") });
  meta.append(more);
  return meta;
}
// ----- the selection: the checkboxes, the bar with "<n> selected", Print labels and Clear selection; Shift+click selects a range, Escape clears -----
function spSync(say) {
  const el = parts.list.querySelector(".coll--spools");
  if (el) {
    for (const b of el.querySelectorAll(".spools-pick")) {
      const on = chosen.has(Number(b.dataset.id));
      b.checked = on;
      const it = b.closest(".coll__item");
      if (on) it.dataset.selected = "true"; else delete it.dataset.selected;
    }
    const some = st.rows.some((v) => chosen.has(v.id)), every = st.rows.length > 0 && st.rows.every((v) => chosen.has(v.id)), all = el.querySelector(".spools-pickall");
    all.checked = every;
    all.indeterminate = some && !every;
  }
  parts.sel.hidden = !chosen.size;
  parts.sel.querySelector(".spools-selcount").textContent = tx("spools_selected", { count: chosen.size });
  if (say && chosen.size) SW.announce(tx("spools_selected", { count: chosen.size }));
}
function spClear(focus) {
  chosen.clear();
  lastPick = null;
  spSync();
  SW.announce(tx("spools_selection_cleared"));
  const all = focus && parts.list.querySelector(".spools-pickall");
  if (all) all.focus();
}
function spSelectionBar() {
  const bar = spE("div", "spools-selbar", { role: "group", "aria-label": tx("spools_selection"), hidden: true }, spE("span", "spools-selcount"),
    spX("button", { class: "btn spools-print", type: "button", on: { click: () => { const ids = st.rows.filter((v) => chosen.has(v.id) && Number.isFinite(v.id)).map((v) => v.id); if (ids.length) spPrintAt(ids); } } }, tx("spools_print_labels")),
    spX("button", { class: "btn btn--ghost spools-clearsel", type: "button", on: { click: () => spClear(true) } }, tx("spools_clear_selection")));
  return bar;
}
const CHIPS = [["sealed", "common_sealed"], ["opened", "spool_state_opened"], ["loaded", "spools_chip_loaded"], ["low", "spools_chip_low"]];
// the chips: Checked out (<n>) comes only when the Hub says it applies the filter; <n> is the `total` of the list read with filter=checked_out
const spChipText = (f, k) => (f === "checked_out" ? (st.checked == null ? tx("spools_chip_checked_out_plain") : tx("spools_chip_checked_out", { count: st.checked })) : tx(k));
function spPaintChips() {
  const group = parts.chips;
  group.replaceChildren(...(spCanCO() ? [...CHIPS.slice(0, 3), ["checked_out", "spools_chip_checked_out"], CHIPS[3]] : CHIPS).map(([f, k]) => spX("button", {
    class: "chip spools-chip", type: "button", "aria-pressed": String(st.filter === f), "data-filter": f,
    on: { click: () => { st.filter = st.filter === f ? "" : f; for (const b of group.children) b.setAttribute("aria-pressed", String(b.dataset.filter === st.filter)); reload(); } } }, spChipText(f, k))));
}
async function spCount() {
  if (!spCanCO()) return;
  try { st.checked = (await api("spools?filter=checked_out&limit=1")).total; } catch { return; }
  if (parts && parts.chips) spPaintChips();
}
// after a change to a spool's checked-out state (Return, Undo): the count, and the list when it is the Checked out filter
const spRecount = () => { if (st.filter === "checked_out") reload(); else spCount(); };
function spToolbar() {
  const search = SW.field({ type: "search", label: tx("spools_search"), name: "q", hint: tx("spools_search_hint"), value: st.q, onSearch: (q) => { st.q = q; reload(); } });
  parts.chips = spE("div", "spools-chips", { role: "group", "aria-label": tx("spools_filters") });
  spPaintChips();
  return spE("div", "coll__toolbar spools-toolbar", search, parts.chips);
}

function paint() {
  parts.head.replaceChildren(spHeader());
  if (!st.rows.length) {
    views.clear();
    const filtered = st.q || st.filter;
    parts.list.replaceChildren(filtered
      ? SW.empty({ icon: "spool", title: st.filter === "checked_out" && !st.q ? tx("spools_empty_checked_out") : tx("spools_empty_filtered"), actions: [SW.btn(tx("spools_clear"), { run: () => { st.q = ""; st.filter = ""; box.replaceWith(region()); } })] })
      : SW.empty({ icon: "spool", title: tx("spools_empty_title"), text: tx("spools_empty_text") }));
    parts.foot.replaceChildren();
    chosen.clear();
    spSync();
    return;
  }
  parts.list.replaceChildren(table(st.rows));
  spSync();
  paintFoot();
}
function paintFoot() {
  parts.foot.replaceChildren(spE("p", "muted spools-shown", tx("spools_shown", { shown: st.rows.length, total: st.total })),
    st.more ? SW.btn(tx("spools_show_more"), { run: (e) => more(e.currentTarget) }) : null);
}

// ----- reads -----
async function reload() {
  const mine = ++seq;
  parts.list.setAttribute("aria-busy", "true");
  try {
    const r = await api(queryOf(0));
    if (mine !== seq) return;
    Object.assign(st, { rows: r.spools, total: r.total, more: r.has_more, url: r.spoolman_url || null, ready: true, filters: r.filters || null });
    parts.list.removeAttribute("aria-busy");
    spPaintChips();
    spCount();
    chosen.clear();
    lastPick = null;
    paint();
    SW.announce(tx("spools_count", { count: st.total }));
  } catch (e) {
    if (mine !== seq) return;
    parts.list.removeAttribute("aria-busy");
    SW.toast(hubText(e.message), "bad");
  }
}
async function more(button) {
  SW.busy(button, tx("common_working"));
  const mine = seq;
  try {
    const r = await api(queryOf(st.rows.length));
    if (mine !== seq) return;
    const known = new Set(st.rows.map((v) => v.id));
    st.rows.push(...r.spools.filter((v) => !known.has(v.id)));
    Object.assign(st, { total: r.total, more: r.has_more });
    const tableEl = parts.list.querySelector(".coll--spools");
    if (tableEl) for (const v of r.spools) if (!known.has(v.id)) tableEl.append(item(v));
    tableEl && SW.collection(tableEl, { name: SECTION, total: st.total, first: 0 });
    spSync();
    parts.head.replaceChildren(spHeader());
    paintFoot();
    const first = tableEl && tableEl.querySelector(`.coll__item[data-id="${r.spools[0] && r.spools[0].id}"] .coll__title`);
    if (first) first.focus({ preventScroll: true });
  } catch (e) {
    SW.idle(button);
    SW.toast(tx("spools_more_failed", { reason: hubText(e.message) }), "bad");
  }
}
// the places Move offers: the storage list, then Automatic
async function spPlaces() {
  if (!storage) storage = api("settings/storage").then((s) => (s.locations || []).map((l) => l.name), () => []);
  return storage;
}

// Change home: the sheet "Home" with the storage locations as the Move choice does (each with how full), Save, "Home is now <location>."
async function spHomeSheet(view, opener) {
  let rows = [];
  try { rows = ((await api("settings/storage")).locations || []); } catch { /* the choice then holds the current home only */ }
  const label = (l) => { if (l.used == null) return l.name + ": " + SW.t("common_unknown"); const cap = l.limit != null ? l.limit : l.count; return cap != null ? tx("spools_home_option_of", { name: l.name, used: l.used ?? 0, capacity: cap }) : tx("spools_home_option", { name: l.name, used: l.used ?? 0 }); };
  const field = SW.field({ type: "select", label: tx("spools_home_place"), name: "home", options: rows.map((l) => ({ value: l.name, label: label(l) })) });
  if (view.v.home && rows.some((l) => l.name === view.v.home)) field._control.value = view.v.home;
  const out = slotBox();
  const save = SW.btn(tx("spools_home_save"), { cls: "btn--primary", name: `${tx("spools_home_save")}: ${spName(view.v)}` });
  const dlg = SW.sheet({ title: tx("spools_home_title"), body: spE("div", "spools-homesheet", field, out), foot: save });
  save.addEventListener("click", () => needCode(async () => {
    if (!field._control.value) return;
    SW.result(out, "run", tx("common_working"));
    try {
      const r = await api(`spools/${view.v.id}`, { home: field._control.value, dry_run: false, code: theCode() });
      view.update(r.spool);
      view.events(true);
      SW.result(out, "ok", tx("spools_home_done", { location: r.spool.home || field._control.value }));
    } catch (e) {
      if (e.locked) { SW.result(out, null); throw e; }
      SW.result(out, "bad", hubText(e.message));                 // the Hub's sentence (409 not checked out, 400 unknown place)
    }
  }));
  SW.openModal(dlg, opener);
}

// ----- one spool: the facts and the four changes -----
function spKv(rows) {
  return spX("dl", { class: "kv" }, rows.map(([k, value, o = {}]) => spE("div", "kv__row", spE("dt", "kv__key", k),
    spE("dd", "kv__value", value == null ? spE("span", "muted", tx("common_empty")) : spE("span", o.mono ? "mono" : null, value)))));
}
function factsOf(v, use) {
  const left = grams(v.remaining_g), net = grams(v.net_g);
  return spKv([
    [tx("spools_col_number"), `#${v.id}`, { mono: true }], [tx("common_field_brand"), v.vendor], [tx("common_field_name"), v.name], [tx("common_field_material"), v.material],
    [tx("common_field_colour"), v.color_hex ? "#" + v.color_hex : null, { mono: true }],
    [tx("spools_fact_left"), left == null ? tx("spools_not_weighed") : net ? `${tx("common_grams", { grams: left })} / ${tx("common_grams", { grams: net })}` : tx("common_grams", { grams: left }), { mono: true }],
    [tx("spools_fact_state"), [tx(v.state === "sealed" ? "common_sealed" : v.state === "opened" ? "spool_state_opened" : "common_empty"), v.low ? ", " + tx("spools_low") : ""].join("")],
    [tx("common_field_location"), v.location], [tx("spool_field_tag"), v.tag, { mono: true }], [tx("common_field_lot"), v.lot],
    [tx("spools_fact_last_use"), use === undefined ? "…" : use ? tx("spools_last_use", { grams: grams(use.grams) ?? "?", printer: use.printer, at: when(use.at) }) : tx("spools_last_use_none")]]);
}

// A change: the press asks the Hub what it would do (the code first), the answer is shown beside the button with Apply, and Apply sends
// it for real. `make(dry)` gives the request; `say(r)` the preview sentence (null: nothing would change); `done(r)` the result sentence.
function spChange(view, out, o) {
  const send = (dry) => api(`spools/${view.v.id}${o.path || ""}`, { ...o.body(), dry_run: dry, code: theCode() });
  const fail = (e) => { if (e.locked) { SW.result(out, null); throw e; } out._said = null; SW.result(out, "bad", hubText(e.message)); };
  const apply = async () => {
    if (o.ask && !(await SW.ask(o.ask(view.v)))) return;
    await needCode(async () => {
      SW.result(out, "run", tx("common_working"));
      let r;
      try { r = await send(false); } catch (e) { fail(e); return; }
      view.update(r.spool);
      if (o.after) o.after(r);
      SW.result(out, "ok", o.done(r));
      view.events(true);
      if (o.toast) spToast(o.toast(r), r, view);
    });
  };
  return async () => {
    await needCode(async () => {
      SW.result(out, "run", tx("spools_previewing"));
      let r;
      try { r = await send(true); } catch (e) { fail(e); return; }
      const text = o.say(r);
      if (!text) SW.result(out, "neutral", tx("spools_nothing"));
      else SW.result(out, "info", text, { action: { label: o.applyLabel || tx("spools_apply"), name: tx("spools_apply_name", { what: o.applyLabel || o.label }), run: apply } });
    });
  };
}

function detail(v0, view, id) {
  let use;            // undefined: not read yet; null: nothing
  const facts = spE("div", "spools-facts");
  const paintFacts = (v) => facts.replaceChildren(factsOf(v, use));
  const back = SW.iconBtn("chevron", tx("detail_back", { name: tx("nav_spools") }), { cls: "detail__back" });
  const closeBtn = SW.iconBtn("close", tx("detail_close", { name: spName(v0) }), { cls: "detail__close" });
  const title = spX("h2", { class: "detail__title", id: id + "-title", tabindex: "-1", text: spName(v0) });

  // Update weight
  const weightBox = slotBox(), weight = SW.field({ type: "number", decimal: true, label: tx("common_field_weight_with_spool"), name: "gross", unit: "g", short: true, mono: true,
    hint: tx("spools_weight_hint"), check: (s) => { const n = Number(String(s).replace(",", ".")); return String(s).trim() && Number.isFinite(n) && n > 0 ? null : tx("spools_weight_error"); } });
  const weightGo = SW.btn(tx("spool_update_weight"), { cls: "btn--primary", name: `${tx("spool_update_weight")}: ${spName(v0)}` });
  weightGo.addEventListener("click", () => {
    const bad = weight._check(); SW.fieldError(weight, bad);
    if (bad) { weight._control.focus(); return; }
    spChange(view, weightBox, { label: tx("spool_update_weight"), body: () => ({ gross: Number(weight._control.value.replace(",", ".")) }),
      say: (r) => (r.spool.remaining_g === r.before.remaining_g ? null : tx("spools_preview_weight", { left: grams(r.before.remaining_g) ?? "?", after: grams(r.spool.remaining_g) })),
      done: (r) => tx("spools_done_weight", { grams: grams(r.spool.remaining_g) }),
      toast: (r) => tx("spools_toast_weight", { name: spName(r.spool), id: r.spool.id, grams: grams(r.spool.remaining_g) }) })();
  });

  // Move
  const moveBox = slotBox(), move = SW.field({ type: "select", label: tx("spool_move_title"), name: "location", options: [{ value: "", label: tx("storage_automatic") }] });
  spPlaces().then((names) => { for (const n of names) move._control.append(spX("option", { value: n, text: n })); if (v0.location && names.includes(v0.location)) move._control.value = v0.location; });
  const moveGo = SW.btn(tx("spool_move"), { cls: "btn--primary", name: `${tx("spool_move")}: ${spName(v0)}` });
  moveGo.addEventListener("click", spChange(view, moveBox, { label: tx("spool_move"), body: () => (move._control.value ? { location: move._control.value } : { auto_location: true }),
    say: (r) => (r.spool.location === r.before.location ? null : tx(r.placed_text ? "spools_preview_move_why" : "spools_preview_move", { before: r.before.location || tx("spools_no_place"), after: r.spool.location, why: r.placed_text })),
    done: (r) => tx("spool_result_moved", { location: r.spool.location }),
    toast: (r) => tx("spools_toast_move", { name: spName(r.spool), id: r.spool.id, location: r.spool.location || tx("spools_no_place") }) }));

  // Link the last tapped tag
  const linkBox = slotBox(), linkGo = SW.btn(tx("spools_link_last"), { name: `${tx("spools_link_last")}: ${spName(v0)}` });
  let tapped = null;
  linkGo.addEventListener("click", async () => {
    let last;
    try { last = await api("spools/last-tag"); } catch (e) { SW.result(linkBox, "bad", hubText(e.message)); return; }
    if (!last.tag) { SW.result(linkBox, "warn", tx("spool_sheet_no_tag")); return; }
    tapped = last.tag;
    spChange(view, linkBox, { label: tx("spools_link_last"), path: "/link", body: () => ({ tag: tapped.uid }),
      say: (r) => (r.spool.tag === r.before.tag ? null : [tx("spools_preview_link", { tag: r.spool.tag }), r.before.state === "sealed" && r.spool.state === "opened" ? " " + tx("spools_preview_link_opens") : ""].join("")),
      done: () => tx("spool_result_tag_linked"),
      toast: (r) => tx("spools_toast_link", { tag: r.spool.tag, name: spName(r.spool), id: r.spool.id }) })();
  });

  // Mark empty: apart, and it asks first
  const emptyBox = slotBox(), emptyGo = SW.btn(tx("spool_mark_empty"), { cls: "btn--danger", name: `${tx("spool_mark_empty")}: ${spName(v0)}` });
  emptyGo.addEventListener("click", spChange(view, emptyBox, { label: tx("spool_mark_empty"), applyLabel: tx("spool_mark_empty"), body: () => ({ empty: true }),
    say: (r) => (r.before.state === "empty" ? null : tx("spools_preview_empty", { left: grams(r.before.remaining_g) ?? 0 })),
    ask: (v) => ({ kind: "destructive", title: tx("spools_ask_empty_title", { id: v.id }), text: tx("spools_ask_empty_text", { name: spName(v) }), confirm: tx("spool_mark_empty") }),
    done: () => tx("spools_done_empty"),
    toast: (r) => tx("spools_toast_empty", { name: spName(r.spool), id: r.spool.id }) }));

  // Print a label: the Hub's print page for this spool (open: no code)
  const printGo = SW.btn(tx("spools_print_label"), { cls: "spools-print-one", name: tx("spools_print_label_name", { name: spName(v0) }) });
  printGo.addEventListener("click", () => spPrintAt([view.v.id]));

  // the latest events of this spool: the newest ten (D1: `newest`, answered oldest of them first), shown newest
  // first; read when the detail first opens, and again after each change. Who and what are the Hub's words, never an id
  const eventsBox = spE("div", "spools-events");
  let asked = false;
  view.events = async (again) => {
    if (!hubHasHistory("activity") || (asked && !again)) return;
    asked = true;
    let r;
    try { r = await api(`activity?subject=${encodeURIComponent("spool:" + v0.id)}&newest=10`); } catch { eventsBox.replaceChildren(spE("p", "muted", tx("spools_events_error"))); return; }
    const list = (r.events || []).slice(-10).reverse();
    eventsBox.replaceChildren(list.length ? spKv(list.map((ev) => [when(ev.at), [ev.text || ev.title || "", " · " + spWho(ev.actor), ev.undone ? ` (${tx("spools_event_undone")})` : ""].join("")]))
      : spE("p", "muted", tx("spools_events_none")));
  };
  // the Home line: while the spool is checked out, with Change home (a sheet of the storage locations, each with how full)
  const homeLine = spE("p", "spools-homeline", { hidden: true });
  const paintHome = (v) => {
    const on = spCanCO() && v.checked_out === true;
    homeLine.hidden = !on;
    if (!on) { homeLine.replaceChildren(); return; }
    homeLine.replaceChildren(spE("span", "spools-homeline__text", spHomeText(v)), " ",
      SW.btn(tx("spools_change_home"), { cls: "btn--ghost", name: tx("spools_change_home_name", { name: spName(v) }), run: (e) => spHomeSheet(view, e.currentTarget) }));
  };
  const actions = (field, button, out) => spE("div", "spools-act", spE("div", "spools-act__row", field, button), out);
  view.paintDetail = (nv) => { title.textContent = spName(nv); paintFacts(nv); paintHome(nv); };
  paintFacts(v0);
  paintHome(v0);
  api(`spools/${v0.id}`).then((r) => { use = r.last_use || null; paintFacts(view.v); }, () => { use = null; paintFacts(view.v); });
  const region = spX("section", { class: "detail", id, "aria-labelledby": id + "-title", hidden: true }, spE("header", "detail__head", back, title, closeBtn),
    spE("div", "detail__body",
      spE("div", "detail__section", spE("h3", "detail__heading", tx("spools_fact_state")), facts, homeLine),
      hubHasHistory("activity") ? spE("div", "detail__section", spE("h3", "detail__heading", tx("spools_events_title")), eventsBox) : null,
      spE("div", "detail__section",
        spE("h3", "detail__heading", tx("spools_group_change")),
        actions(weight, weightGo, weightBox), actions(move, moveGo, moveBox), spE("div", "spools-act", spE("div", "spools-act__row", linkGo), linkBox),
        spE("div", "spools-act", spE("div", "spools-act__row", printGo)),
        spE("div", "detail__remove", spE("h3", "detail__heading", tx("spools_group_empty")), spE("div", "spools-act", spE("div", "spools-act__row", emptyGo), emptyBox)))));
  region.addEventListener("sw:detail-close", () => {
    wanted.delete(view.v.id);
    if (location.hash === spaddressOf(view.v.id)) history.replaceState(null, "", spaddressOf());
  });
  return region;
}

// ----- the section -----
function region() {
  box = spE("div", "spools");
  chosen.clear();
  lastPick = null;
  parts = { head: spE("div", "spools-headbox"), sel: spSelectionBar(), list: spE("div", "spools-listbox"), foot: spE("div", "spools-foot") };
  box.append(parts.head, spToolbar(), parts.sel, parts.list, parts.foot);
  box.addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || e.defaultPrevented || !chosen.size || document.querySelector("dialog[open]") || e.target.closest("input:not([type=checkbox]), select, textarea")) return;
    spClear(false);
  });
  const L = SW.loading(parts.list, { thing: tx("nav_spools"), items: 3, onTry: () => { start(); }, title: tx("spools_error") });
  const mine = ++seq;
  start();
  async function start() {
    try {
      const at = addressId();
      const [r, one] = await Promise.all([api(queryOf(0)), at != null ? api(`spools/${at}`).catch(() => null) : null]);
      if (mine !== seq) return;
      Object.assign(st, { rows: r.spools, total: r.total, more: r.has_more, url: r.spoolman_url || null, ready: true, filters: r.filters || null });
      if (one && !st.rows.some((v) => v.id === one.spool.id)) st.rows.unshift(one.spool);
      if (at != null) wanted.add(at);
      L.done();
      spPaintChips();
      spCount();
      paint();
      if (at != null) { const it = parts.list.querySelector(`.coll__item[data-id="${at}"]`); if (it) it.scrollIntoView({ block: "start" }); }
    } catch (e) {
      if (mine !== seq) return;
      L.done(SW.errorState({ title: tx("spools_error"), text: hubText(e.message), onTry: start }));
    }
  }
  storage = null;
  return box;
}

window.spoolsTab = () => [region];
})();
