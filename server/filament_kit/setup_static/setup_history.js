// History-backed summaries for the Hub's navigation and Overview.
"use strict";

const hubCanExport = (name) => !S || !S.history || (S.history.exports || []).includes(name);
const hubHasHistory = (kind) => !S || !S.history || !!S.history[kind];
const hubHistorySummary = {};
let hubMonthUsage = Promise.resolve(null);

function hubReadHistorySummaries() {
  const state = S;
  const set = (name, text) => { if (S === state) { hubHistorySummary[name] = text; summaries(); } };
  hubMonthUsage = api("analytics?months=1").then((r) => {
    const grams = r.months?.[0]?.grams;
    return grams > 0 ? anGrams(grams) : "";
  }, () => null);
  hubMonthUsage.then((weight) => set("analytics", weight ? SW.t("nav_summary_analytics_used", { weight })
    : weight === "" ? SW.t("nav_summary_analytics_empty") : ""));
  api("spools/low").then((r) => {
    if (r.spools.length) return SW.t("nav_summary_spools_low", { count: r.spools.length });
    return api("spools?limit=1").then((all) => SW.t("nav_summary_spools_count", { count: all.total }));
  }).then((text) => set("spools", text), () => set("spools", ""));
}

function hubFilamentLine() {
  const line = h("p", { class: "muted", hidden: true });
  later(hubMonthUsage.then((weight) => {
    if (!weight) return;
    line.append(h("a", { href: "#analytics", textContent: SW.t("overview_filament_used", { weight }) }));
    line.hidden = false;
  }));
  return line;
}
