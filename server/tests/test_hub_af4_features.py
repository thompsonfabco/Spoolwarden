"""AF4 in the Features section of the Hub page (design 03 sections 9.4, 9.5, 4.2): Label Reader: On this Hub lead-in in
the preview drawer, Remote access port-443 conflict refusal with #settings/network link, "Not installed yet" row action,
and apply completion / leave-early / failure lead-in behavior. Uses tests/page_drive.mjs and test_hub_page_audit.hub_app."""
from __future__ import annotations

from test_hub_page_audit import hub_app  # noqa: F401
from test_hub_presets_ui import T, _drive, hub  # noqa: F401
from test_setup_page import CODE, STATIC

PRE = """const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function until(look, what, ms = 10000) {
  for (let i = 0; i < ms / 100; i++) { const v = look(); if (v) return v; await sleep(100); }
  throw new Error("waited for " + what);
}
const $ = (s, root = document) => root.querySelector(s);
const $$ = (s, root = document) => [...root.querySelectorAll(s)];
const text = (el) => (el ? el.textContent.trim().replace(/\\s+/g, " ") : null);
await until(() => $("main") && !$(".chip--lock .skeleton__line"), "the page and the lock");
"""
UNLOCK = f'SW.setLock("unlocked", {{ code: {CODE!r} }});\n'


def test_af4_static_symbols_and_catalogue_keys():
    """setup_features.js uses the AF5 catalogue keys and keeps no duplicate top-level JS symbols or innerHTML."""
    js = (STATIC / "setup_features.js").read_text(encoding="utf-8")
    for key in ("features_reader_lead_in", "features_reader_not_installed", "features_reader_finish",
                "features_reader_switched_on", "port_remote_refusal", "common_open_settings"):
        assert key in js, key
    assert "innerHTML" not in js and "eval(" not in js


def test_af4_preview_drawer_lead_in_and_remote_443_refusal_link(hub, tmp_path):
    """Turning on local_intake shows features_reader_lead_in; turning off does not. Remote access port-443 refusal shows
    the refusal with a #settings/network link and omits Apply."""
    page = _drive(hub, tmp_path, PRE + UNLOCK + """
const realFetch = window.fetch.bind(window);
let readerOn = false;
window.fetch = async (u, init) => {
  const s = String(u);
  if (s.includes("/setup/api/modules/preview?name=local_intake")) {
    return Response.json({
      name: "local_intake", on: !readerOn,
      notes: [], after: readerOn ? ["server"] : ["server", "local_intake"], needs: [], refused: null,
    });
  }
  if (s.includes("/setup/api/modules/preview?name=remote")) {
    return Response.json({
      name: "remote", on: true, notes: [], after: ["server", "remote"], needs: [],
      refused: "__REMOTE_REFUSAL__", code: "remote_443",
    });
  }
  return realFetch(u, init);
};
location.hash = "#features";
await until(() => $('[data-key="local_intake"] button'), "features table");
// Turning ON local_intake: lead-in shown
$('[data-key="local_intake"] button').click();
let dlg = await until(() => $("dialog[open].dialog--drawer"), "local_intake ON drawer");
await until(() => dlg.querySelector(".features__lead"), "lead-in line");
const leadOn = text(dlg.querySelector(".features__lead"));
const hasApplyOn = !!dlg.querySelector('[data-action="confirm"]');
dlg.querySelector('[data-action="cancel"]').click();
await until(() => !$("dialog[open].dialog--drawer"), "drawer closed");

// Turning OFF local_intake: lead-in omitted
readerOn = true;
$('[data-key="local_intake"] button').click();
dlg = await until(() => $("dialog[open].dialog--drawer"), "local_intake OFF drawer");
await until(() => dlg.querySelector(".features__after"), "preview loaded");
const leadOff = !!dlg.querySelector(".features__lead");
dlg.querySelector('[data-action="cancel"]').click();
await until(() => !$("dialog[open].dialog--drawer"), "drawer closed 2");

// Remote access port 443 conflict: refusal + #settings/network link + no Apply
$('[data-key="remote"] button').click();
dlg = await until(() => $("dialog[open].dialog--drawer"), "remote drawer");
const netLink = await until(() => dlg.querySelector('a[href="#settings/network"]'), "network link");
const remoteBanner = text(dlg.querySelector(".banner"));
const remoteHasApply = !!dlg.querySelector('[data-action="confirm"]');
netLink.click();
await until(() => location.hash === "#settings/network" && !$("dialog[open].dialog--drawer"), "navigated to settings/network");
return { leadOn, hasApplyOn, leadOff, remoteBanner, remoteHasApply, hash: location.hash };
""".replace("__REMOTE_REFUSAL__", T("port_remote_refusal")))
    assert page["leadOn"] == T("features_reader_lead_in") and page["hasApplyOn"] is True, page
    assert page["leadOff"] is False, page
    assert T("port_remote_refusal") in page["remoteBanner"] and page["remoteHasApply"] is False, page
    assert page["hash"] == "#settings/network", page


def test_af4_feature_row_shows_not_installed_yet_when_pending_and_hides_when_installed(hub, tmp_path):
    """When local_intake is on and reader_install_pending is true (or local_intake_installed is null via settings/general),
    the row shows 'Not installed yet' and Finish in Settings -> #settings/label-reader; when installed / false, it hides."""
    page = _drive(hub, tmp_path, PRE + """
const realFetch = window.fetch.bind(window);
let mode = "state_pending";
window.fetch = async (u, init) => {
  const s = String(u);
  if (s.endsWith("/setup/api/state")) {
    const r = await realFetch(u, init);
    const data = await r.json();
    data.modules = data.modules.map((m) => (m.name === "local_intake" ? { ...m, on: true } : m));
    if (mode === "state_pending") data.reader_install_pending = true;
    else if (mode === "state_done") data.reader_install_pending = false;
    else delete data.reader_install_pending;
    return Response.json(data);
  }
  if (s.endsWith("/setup/api/settings/general")) {
    const r = await realFetch(u, init);
    const data = await r.json();
    delete data.reader_install_pending;
    data.local_intake_installed = mode === "general_done" ? { version: "0.1.0", tier: "cpu" } : null;
    if (data.values) data.values["local_intake.url"] = "http://127.0.0.1:7915";
    return Response.json(data);
  }
  return realFetch(u, init);
};
await refresh();
location.hash = "#features";
const row = () => $('[data-key="local_intake"]');
await until(() => row() && row().querySelector(".features__finish"), "pending row link");
const shown = {
  text: text(row().querySelector(".features__pending")),
  href: row().querySelector(".features__finish").getAttribute("href"),
};
mode = "state_done";
await refresh();
await until(() => row() && !row().querySelector(".features__finish"), "cleared pending after installed");
const hiddenState = {
  hasPendingLink: !!row().querySelector(".features__finish"),
  pendingHidden: !row().querySelector(".features__pending") || row().querySelector(".features__pending").hidden,
};
mode = "general_pending";
await refresh();
await until(() => row() && row().querySelector(".features__finish"), "pending via settings/general fallback");
const fallbackShown = text(row().querySelector(".features__pending"));
mode = "general_done";
await refresh();
await until(() => row() && !row().querySelector(".features__finish"), "cleared via settings/general installed");
const fallbackHidden = !row().querySelector(".features__finish");
return { shown, hiddenState, fallbackShown, fallbackHidden };
""")
    assert T("features_reader_not_installed") in page["shown"]["text"], page
    assert T("features_reader_finish") in page["shown"]["text"], page
    assert page["shown"]["href"] == "#settings/label-reader", page
    assert page["hiddenState"] == {"hasPendingLink": False, "pendingHidden": True}, page
    assert T("features_reader_not_installed") in page["fallbackShown"], page
    assert page["fallbackHidden"] is True, page


def test_af4_apply_watcher_redirects_when_on_features_sets_left_early_when_away_and_ignores_failure(hub, tmp_path):
    """Turning local_intake ON and saving watches the apply job: exit=0 on #features sets __swReaderArrived, toasts,
    and navigates to #settings/label-reader; leaving #features sets __swReaderLeftEarly without hijacking; exit!=0
    clears pending and does not redirect."""
    page = _drive(hub, tmp_path, PRE + UNLOCK + """
const realFetch = window.fetch.bind(window);
let readerOn = false, jobState = { seen: true, name: "apply", running: false, exit: null, lost: false, text: "", next: 0 };
window.fetch = async (u, init) => {
  const s = String(u), m = (init && init.method) || "GET";
  if (s.endsWith("/setup/api/state")) {
    const r = await realFetch(u, init);
    const data = await r.json();
    data.modules = data.modules.map((x) => (x.name === "local_intake" ? { ...x, on: readerOn } : x));
    data.reader_install_pending = readerOn;
    data.apply = { ...data.apply, running: !!jobState.running };
    return Response.json(data);
  }
  if (s.includes("/setup/api/modules/preview?name=local_intake")) {
    return Response.json({ name: "local_intake", on: true, notes: [], after: ["server", "local_intake"], needs: [], refused: null });
  }
  if (s.endsWith("/setup/api/modules") && m === "POST") {
    readerOn = true;
    jobState = { seen: true, name: "apply", running: true, exit: null, lost: false, text: "", next: 0 };
    return Response.json({ ok: true, changed: true, notes: [] });
  }
  if (/\\/setup\\/api\\/job(\\?|$)/.test(s)) {
    const r = await realFetch(u, init);
    const base = await r.json();
    return Response.json({ ...base, ...jobState });
  }
  return realFetch(u, init);
};
async function turnOnLocalIntake() {
  location.hash = "#features";
  await until(() => $('[data-key="local_intake"] button'), "local_intake button");
  $('[data-key="local_intake"] button').click();
  const dlg = await until(() => $("dialog[open].dialog--drawer"), "drawer");
  await until(() => !dlg.querySelector('[data-action="confirm"]').hasAttribute("aria-disabled"), "apply enabled");
  dlg.querySelector('[data-action="confirm"]').click();
  await until(() => !$("dialog[open].dialog--drawer"), "drawer closed after apply");
}

// 1) Stay on #features until exit === 0 -> toast + __swReaderArrived + #settings/label-reader
readerOn = false;
await refresh();
await turnOnLocalIntake();
jobState = { seen: true, name: "apply", running: false, exit: 0, lost: false, text: "", next: 0 };
await until(() => location.hash === "#settings/label-reader", "redirected to label-reader");
const stayed = {
  arrived: window.__swReaderArrived,
  leftEarly: !!window.__swReaderLeftEarly,
  hash: location.hash,
  toasts: $$(".toast").map((t) => text(t)),
};

// 2) Leave #features while apply runs -> __swReaderLeftEarly = true, no navigation hijack
window.__swReaderArrived = false;
window.__swReaderLeftEarly = false;
readerOn = false;
await refresh();
await turnOnLocalIntake();
location.hash = "#overview";
await until(() => tabNow() === "overview", "left to overview");
const readerBanner = () => [...document.querySelectorAll(".banner")].find((e) => text(e).includes("The Label Reader is switched on but not installed."));
await until(() => $(".tile a, a.tile"), "the Overview tiles");
const bannerWhileRunning = !!readerBanner();
jobState = { seen: true, name: "apply", running: false, exit: 0, lost: false, text: "", next: 0 };
await until(() => window.__swReaderLeftEarly === true, "leftEarly set");
const banner = await until(readerBanner, "the leave-early reader banner");
const leftEarly = {
  leftEarly: window.__swReaderLeftEarly,
  arrived: !!window.__swReaderArrived,
  hash: location.hash,
  bannerWhileRunning,
  finish: banner.querySelector("a").getAttribute("href"),
};
readerOn = false;
await refresh();
await until(() => !readerBanner(), "the banner cleared when no longer pending");

// 3) Failed job (exit !== 0) -> no redirect, clears pending flags
window.__swReaderArrived = false;
window.__swReaderLeftEarly = false;
readerOn = false;
await refresh();
await turnOnLocalIntake();
jobState = { seen: true, name: "apply", running: false, exit: 1, lost: false, text: "", next: 0 };
await until(() => hubJobs().strip.dataset.state === "failed", "failed job strip");
await sleep(300);
const failed = {
  arrived: !!window.__swReaderArrived,
  leftEarly: !!window.__swReaderLeftEarly,
  hash: location.hash,
};
return { stayed, leftEarly, failed };
""")
    assert page["stayed"]["arrived"] is True and page["stayed"]["leftEarly"] is False, page
    assert page["stayed"]["hash"] == "#settings/label-reader", page
    assert any(T("features_reader_switched_on") in t for t in page["stayed"]["toasts"]), page
    assert page["leftEarly"] == {"leftEarly": True, "arrived": False, "hash": "#overview", "bannerWhileRunning": False,
                                 "finish": "#settings/label-reader"}, page
    assert page["failed"] == {"arrived": False, "leftEarly": False, "hash": "#features"}, page
