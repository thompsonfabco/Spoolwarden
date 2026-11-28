// The Analytics section: the six insight sentences, filament used per month as bars (inline SVG, no chart
// library) or a table, and a table per material for one month. Loaded after setup.js; everything from the server goes in
// through textContent, as there. The section is opened by the registry (TABS in setup.js), which is not this file's.
"use strict";

// The texts of this section until the catalogue has them (only opens strings/): a key the catalogue has wins.
const AN_TEXT = {
  analytics_error: "Could not read the analytics{reason}",
  analytics_title: "Analytics",
  analytics_insights_title: "Highlights",
  analytics_chart_title: "Filament used per month",
  analytics_chart_label: "Filament used per month in the last 12 months",
  analytics_printer_label: "Printers",
  analytics_printer_all: "All printers",
  analytics_show_table: "Show as table",
  analytics_grams: "{grams} g",
  analytics_kilograms: "{kg} kg",
  analytics_col_month: "Month",
  analytics_col_grams: "Filament used",
  analytics_col_prints: "Prints",
  analytics_col_unfinished: "Did not finish",
  analytics_col_material: "Most used material",
  analytics_col_share: "Share",
  analytics_unfinished_cell: "{count} ({grams})",
  analytics_nothing: "None",
  analytics_material_title: "Filament used by material",
  analytics_month_label: "Month",
  analytics_material_col: "Material",
  analytics_share: "{percent}%",
  analytics_material_none: "No filament was used in {month}.",
  analytics_material_unknown: "No materials are recorded for {month}.",
  analytics_empty_title: "No usage yet",
  analytics_empty_text: "Filament your printers use is counted here, month by month, once a print has been logged.",
  analytics_note_logged_only: "Includes {grams} g the Hub logged but took off no spool.",
  analytics_note_no_weight_one: "{count} print has no weight.",
  analytics_note_no_weight_other: "{count} prints have no weight.",
  analytics_cost_title: "Cost and waste",
  analytics_cost_summary: "Estimated filament cost: {cost}. Unfinished prints: {waste} ({percent}%).",
  analytics_cost_empty: "No spool prices are recorded yet. Add a price to a spool to track print cost and waste.",
  analytics_col_cost: "Cost",
  analytics_col_waste: "Waste",
  analytics_currency_amount: "${amount}",
  analytics_orders_title: "Order history",
  analytics_orders_summary: "{count} orders saved ({spools} spools).",
  analytics_orders_empty: "No filament orders imported yet.",
  analytics_orders_format: "Format",
  analytics_orders_format_auto: "Detect automatically",
  analytics_orders_format_csv: "CSV",
  analytics_orders_format_json: "JSON",
  analytics_orders_paste: "Order data",
  analytics_orders_placeholder: "date,seller,material,weight_g,price,currency,qty",
  analytics_orders_hint: "Paste CSV rows or a JSON array of filament orders.",
  analytics_orders_import_btn: "Import orders",
  analytics_orders_importing: "Importing orders...",
  analytics_orders_imported: "Imported {imported} orders ({skipped} already saved, {total} total).",
  analytics_orders_error_empty: "Paste CSV or JSON order data first.",
  analytics_orders_upload: "Choose file…",
};
const anT = (key, vars) => (window.SW_TEXT && window.SW_TEXT[key] != null ? SW.t(key, vars)
  : AN_TEXT[key].replace(/\{([a-z0-9_]+)\}/g, (m, n) => (vars && vars[n] != null ? String(vars[n]) : m)));

// the insights come in a fixed order, whatever order the Hub sends them in
const AN_ORDER = ["total", "favourite_material", "busiest_printer", "unfinished", "biggest_print", "spools_finished"];
const AN_MONTHS = 12;

// what the section keeps while it is drawn again (a job ended, the lock changed): the choices and the printers seen
const anState = { printer: "", table: false, month: "", printers: new Map() };

const anKey = (y, m) => y + "-" + String(m).padStart(2, "0");
// the last `count` months, oldest first, ending with the newest the Hub answered or this month
function anSpan(months, count) {
  const now = new Date();
  let end = anKey(now.getFullYear(), now.getMonth() + 1);
  for (const m of months) if (m.month > end) end = m.month;
  let [y, mo] = end.split("-").map(Number);
  const keys = [];
  for (let i = 0; i < count; i++) { keys.unshift(anKey(y, mo)); if (--mo === 0) { mo = 12; y--; } }
  return keys;
}
const anMonth = (key, long) => {
  const [y, m] = key.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, 15)).toLocaleDateString(undefined, { month: long ? "long" : "short", year: "numeric", timeZone: "UTC" });
};
const anGrams = (g) => (g >= 1000 ? anT("analytics_kilograms", { kg: (g / 1000).toFixed(2) }) : anT("analytics_grams", { grams: Math.round(g) }));
const anMoney = (v, currency) => {
  const n = Number(v || 0), code = String(currency || "").trim().toUpperCase();
  if (/^[A-Z]{3}$/.test(code)) {
    try { return new Intl.NumberFormat(undefined, { style: "currency", currency: code }).format(n); } catch { /* fall through */ }
  }
  return anT("analytics_currency_amount", { amount: n.toFixed(2) });
};
const anBlank = (month) => ({ month, grams: 0, prints: 0, unfinished_prints: 0, unfinished_grams: 0, cost: 0, unfinished_cost: 0,
  logged_only_grams: 0, no_weight_prints: 0, by_printer: [], by_material: [] });

// one bar per month: the grams above it in words, the bar itself an SVG that is told its length by --level (0 to 1) and is
// laid out upright, or lengthwise when the room is narrow (setup_analytics.css)
function anBar(m, level) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(NS, "svg"), rect = document.createElementNS(NS, "rect");
  svg.setAttribute("viewBox", "0 0 1 1");
  svg.setAttribute("preserveAspectRatio", "none");
  svg.setAttribute("aria-hidden", "true");
  svg.setAttribute("focusable", "false");
  svg.setAttribute("class", "an-bar__svg");
  for (const [k, v] of [["x", "0"], ["y", "0"], ["width", "1"], ["height", "1"], ["class", "an-bar__rect"]]) rect.setAttribute(k, v);
  svg.append(rect);
  const li = h("li", { class: "an-bar", "data-month": m.month },
    h("span", { class: "an-bar__grams", textContent: anGrams(m.grams) }),
    h("span", { class: "an-bar__plot" }, svg),
    h("span", { class: "an-bar__month", textContent: anMonth(m.month) }));
  li.style.setProperty("--level", String(level));
  if (!m.grams) li.dataset.empty = "true";
  return li;
}

function anChart(rows) {
  const top = Math.max(0, ...rows.map((m) => m.grams));
  return h("div", { class: "an-chartbox" },
    h("ul", { class: "an-chart", "aria-label": anT("analytics_chart_label") }, rows.map((m) => anBar(m, top ? m.grams / top : 0))));
}

// a table that stacks into labelled blocks when the room is narrow (the cells carry their column's name in data-label)
function anTable(caption, head, rows) {
  return h("div", { class: "an-tablebox" }, h("table", { class: "an-table" },
    h("caption", { class: "sr", textContent: caption }),
    h("thead", {}, h("tr", {}, head.map((c) => h("th", { scope: "col", textContent: c })))),
    h("tbody", {}, rows.map((cells) => h("tr", {}, cells.map((c, i) => (i ? h("td", { "data-label": head[i], textContent: c })
      : h("th", { scope: "row", textContent: c }))))))));
}

function anMonthTable(rows) {
  const head = ["month", "grams", "prints", "unfinished", "material"].map((k) => anT("analytics_col_" + k));
  return anTable(anT("analytics_chart_title"), head, [...rows].reverse().map((m) => [
    anMonth(m.month, true), anGrams(m.grams), String(m.prints),
    m.unfinished_grams ? anT("analytics_unfinished_cell", { count: m.unfinished_prints, grams: anGrams(m.unfinished_grams) }) : String(m.unfinished_prints),
    m.top_material || anT("analytics_nothing")]));
}

function anMaterials(m) {
  const list = [...(m.by_material || [])].sort((a, b) => b.grams - a.grams);
  if (!list.length) return h("p", { class: "muted", textContent: anT(m.grams ? "analytics_material_unknown" : "analytics_material_none", { month: anMonth(m.month, true) }) });
  const sum = list.reduce((s, x) => s + x.grams, 0) || 1;
  const head = [anT("analytics_material_col"), anT("analytics_col_grams"), anT("analytics_col_share")];
  return anTable(anT("analytics_material_title"), head,
    list.map((x) => [x.material, anGrams(x.grams), anT("analytics_share", { percent: Math.round((x.grams / sum) * 100) })]));
}

// the notes under the bars: the Hub's own, else the same two drawn from the months
function anNotes(data, rows) {
  const given = (data.notes || []).filter(Boolean);
  if (given.length) return given;
  const logged = rows.reduce((s, m) => s + (m.logged_only_grams || 0), 0), bare = rows.reduce((s, m) => s + (m.no_weight_prints || 0), 0);
  return [logged ? anT("analytics_note_logged_only", { grams: Math.round(logged) }) : null,
    bare ? anT(bare === 1 ? "analytics_note_no_weight_one" : "analytics_note_no_weight_other", { count: bare }) : null].filter(Boolean);
}

function analyticsTab() {
  const out = h("div", { class: "an" });
  const gate = h("div", { class: "an__gate" }), main = h("div", { class: "an__main", hidden: true });
  const insights = h("section", { class: "card an__insights", hidden: true });
  const printerField = SW.field({ label: anT("analytics_printer_label"), type: "select", name: "printer", options: [{ value: "", label: anT("analytics_printer_all") }] });
  const tableCheck = SW.check({ label: anT("analytics_show_table"), name: "table", checked: anState.table });
  const monthField = SW.field({ label: anT("analytics_month_label"), type: "select", name: "month", options: [] });
  const chartArea = h("div", { class: "an__chart" }), notes = h("ul", { class: "an__notes muted" }), materials = h("div", { class: "an__materials" });
  main.append(insights,
    h("section", { class: "card an__months" }, h("h2", { textContent: anT("analytics_chart_title") }),
      h("div", { class: "an__controls" }, printerField, tableCheck), chartArea, notes),
    h("section", { class: "card an__by-material" }, h("h2", { textContent: anT("analytics_material_title") }), monthField, materials));

  const costBody = h("div", { class: "an__cost-body" });
  const costCard = h("section", { class: "card an__cost", hidden: true },
    h("h2", { textContent: anT("analytics_cost_title") }), costBody);

  const ordersSummary = h("p", { class: "muted an__orders-summary" });
  const orderFormat = SW.field({
    label: anT("analytics_orders_format"),
    type: "select",
    name: "order_format",
    value: "auto",
    options: [
      { value: "auto", label: anT("analytics_orders_format_auto") },
      { value: "csv", label: anT("analytics_orders_format_csv") },
      { value: "json", label: anT("analytics_orders_format_json") },
    ],
  });
  const orderData = SW.field({
    label: anT("analytics_orders_paste"),
    type: "textarea",
    name: "order_data",
    placeholder: anT("analytics_orders_placeholder"),
    hint: anT("analytics_orders_hint"),
  });
  const orderResult = h("div", { class: "an__orders-result" });
  const fileField = SW.field({ type: "file", label: anT("analytics_orders_upload"), accept: ".csv,.json,text/csv,application/json", optional: true });
  fileField.hidden = true;
  const fileInput = fileField._control;
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files && fileInput.files[0];
    fileInput.value = "";
    if (!file) return;
    try {
      orderData._control.value = await file.text();
      SW.fieldError(orderData, null);
    } catch (e) { SW.result(orderResult, "bad", hubText(e.message)); }
  });
  const uploadBtn = SW.btn(anT("analytics_orders_upload"), { cls: "btn--ghost", action: "upload-orders", run: () => fileInput.click() });
  let importing = false;
  const importBtn = SW.btn(anT("analytics_orders_import_btn"), {
    action: "import-orders",
    cls: "btn--primary",
    run: async () => {
      if (importing) return;
      const raw = orderData._control.value;
      if (!raw.trim()) {
        SW.fieldError(orderData, anT("analytics_orders_error_empty"));
        orderData._control.focus();
        return;
      }
      SW.fieldError(orderData, null);
      importing = true;
      SW.busy(importBtn, anT("analytics_orders_importing"));
      SW.result(orderResult, "run", anT("analytics_orders_importing"));
      try {
        let res;
        await needCode(async () => {
          res = await api("orders/import", { data: raw, format: orderFormat._control.value, code: theCode() });
        });
        if (!res) { SW.result(orderResult, null); return; }
        orderData._control.value = "";
        SW.result(orderResult, "ok", anT("analytics_orders_imported", {
          imported: res.imported,
          skipped: res.skipped,
          total: res.total,
        }));
        if (data && res.summary) {
          data.orders = res.summary;
          drawOrders(res.summary);
        }
      } catch (e) {
        if (e.locked) { SW.result(orderResult, null); throw e; }
        SW.result(orderResult, "bad", hubText(e.message));
      } finally {
        importing = false;
        SW.idle(importBtn);
      }
    },
  });
  const ordersCard = h("section", { class: "card an__orders", hidden: true },
    h("h2", { textContent: anT("analytics_orders_title") }),
    ordersSummary,
    orderFormat,
    orderData,
    fileField,
    h("div", { class: "actions" }, uploadBtn, importBtn),
    orderResult);

  out.append(gate, main, costCard, ordersCard);

  let data = null, seq = 0;

  const drawCost = (answer, rows) => {
    const totals = answer.totals || {}, cur = answer.currency || totals.currency || (S && S.currency) || "";
    const totalCost = Number(totals.cost || rows.reduce((s, m) => s + (m.cost || 0), 0));
    const totalWaste = Number(totals.unfinished_cost || rows.reduce((s, m) => s + (m.unfinished_cost || 0), 0));
    const pricedRows = [...rows].reverse().filter((m) => (m.cost || 0) > 0 || (m.unfinished_cost || 0) > 0);
    if (!totalCost && !totalWaste && !pricedRows.length) {
      costBody.replaceChildren(h("p", { class: "muted", textContent: anT("analytics_cost_empty") }));
      return;
    }
    const pct = totalCost > 0 ? Math.round((totalWaste / totalCost) * 100) : 0;
    const summary = h("p", {
      textContent: anT("analytics_cost_summary", { cost: anMoney(totalCost, cur), waste: anMoney(totalWaste, cur), percent: pct }),
    });
    const head = [anT("analytics_col_month"), anT("analytics_col_cost"), anT("analytics_col_waste")];
    const table = anTable(anT("analytics_cost_title"), head, pricedRows.map((m) => [
      anMonth(m.month, true), anMoney(m.cost, cur), anMoney(m.unfinished_cost, cur),
    ]));
    costBody.replaceChildren(summary, table);
  };

  const drawOrders = (summary) => {
    if (!summary || !summary.count) {
      ordersSummary.textContent = anT("analytics_orders_empty");
      return;
    }
    ordersSummary.textContent = anT("analytics_orders_summary", { count: summary.count, spools: summary.total_spools || 0 });
  };

  const syncPrinters = (answer) => {
    if (!anState.printer) for (const m of answer.months || []) for (const p of m.by_printer || []) anState.printers.set(String(p.id), p.name);
    printerField._control.replaceChildren(...[["", anT("analytics_printer_all")], ...anState.printers].map(([value, label]) => h("option", { value, textContent: label })));
    printerField._control.value = anState.printer;
    printerField.hidden = anState.printers.size === 0;
  };

  const drawMaterials = (rows) => {
    const m = rows.find((x) => x.month === monthField._control.value) || rows[rows.length - 1];
    materials.replaceChildren(anMaterials(m));
  };

  const draw = () => {
    const answer = data, keys = anSpan(answer.months || [], AN_MONTHS), by = new Map((answer.months || []).map((m) => [m.month, m]));
    const rows = keys.map((k) => by.get(k) || anBlank(k));
    const used = rows.some((m) => m.grams || m.prints);
    const lines = [...(answer.insights || [])].sort((a, b) => (AN_ORDER.indexOf(a.key) + 1 || 99) - (AN_ORDER.indexOf(b.key) + 1 || 99));
    insights.hidden = !lines.length;
    insights.replaceChildren(...(lines.length ? [h("h2", { textContent: anT("analytics_insights_title") }),
      h("ul", { class: "an__list" }, lines.map((l) => h("li", { "data-key": l.key, textContent: l.text })))] : []));
    tableCheck.hidden = !used;
    drawCost(answer, rows);
    drawOrders(answer.orders);
    if (!used) {
      // nothing logged in these months: one calm line, not twelve empty bars
      chartArea.replaceChildren(SW.empty({ icon: "info", title: anT("analytics_empty_title"), text: anT("analytics_empty_text"), level: 3 }));
      notes.replaceChildren();
      monthField.closest(".card").hidden = true;
      return;
    }
    monthField.closest(".card").hidden = false;
    chartArea.replaceChildren(anState.table ? anMonthTable(rows) : anChart(rows));
    notes.replaceChildren(...anNotes(answer, rows).map((t) => h("li", { textContent: t })));
    const latest = [...rows].reverse().find((m) => m.grams) || rows[rows.length - 1];
    if (!rows.some((m) => m.month === anState.month)) anState.month = latest.month;
    monthField._control.replaceChildren(...[...rows].reverse().map((m) => h("option", { value: m.month, textContent: anMonth(m.month, true) })));
    monthField._control.value = anState.month;
    drawMaterials(rows);
  };

  // `where`: the part that shows the wait or the failure (the whole section the first time, the bars when a printer is chosen)
  const load = (where) => {
    const mine = ++seq, L = SW.loading(where, { thing: anT("analytics_title"), items: 3, onTry: () => load(where) });
    const query = "analytics?months=" + AN_MONTHS + (anState.printer ? "&printer=" + encodeURIComponent(anState.printer) : "");
    return Promise.resolve().then(() => api(query)).then((answer) => {
      if (mine !== seq) return;
      L.done();
      data = answer;
      syncPrinters(answer);
      gate.replaceChildren();
      main.hidden = false;
      costCard.hidden = false;
      ordersCard.hidden = false;
      draw();
    }, (e) => {
      if (mine !== seq) return;
      if (e.locked) { L.done(); throw e; }
      L.done(SW.errorState({ title: anT("analytics_error", { reason: "" }).trim(), text: hubText(e.message), onTry: () => load(where),
        details: (e.path ? "/setup/api/" + e.path + "\n" : "") + (e.status ? "HTTP " + e.status + ": " : "") + e.message }));
    });
  };

  printerField._control.addEventListener("change", () => { anState.printer = printerField._control.value; load(chartArea); });
  tableCheck._control.addEventListener("change", () => { anState.table = tableCheck._control.checked; if (data) draw(); });
  monthField._control.addEventListener("change", () => { anState.month = monthField._control.value; if (data) draw(); });

  later(load(gate));
  return [out];
}
