// The Settings section's Alerts group: eight switches, the last-spool-only switch, Running low under, Storage warning
// at (one Save, the Hub's alerts.xml) and the part "On this device" (the browser switch kept in this browser, Send a test alert,
// Recent alerts) that applies at once. Loaded after setup_settings.js and setup_alertbar.js (its texts, axt). It is the pattern of
// setup_storage.js: loadAlerts() is the group's read, alertsGroup(data) its builder, ALERTS_GROUP its row for SET_GROUPS.
// Everything from the Hub goes in through textContent.
"use strict";

// ----- the data: the Hub's own routes -----
// GET alerts answers {types: [{key, title, on}] (the eight fixed keys), last_spool_only, running_low_g, storage_warn_percent,
// storage_bad_percent}; POST settings/alerts (code) takes the same fields; POST alerts/test (no code) takes {client}.
const ALERT_TYPES = ["runout", "paused", "failed", "printer_error", "finished", "storage", "running_low", "humidity", "device_down"];
const loadAlerts = () => api("alerts");
// the group's row of SET_GROUPS: id, catalogue key of the name, icon, builder, the read it needs
const ALERTS_GROUP = ["alerts", "settings_group_alerts", "warning", (d) => alertsGroup(d), "alerts"];
const alertsOnCount = (al) => (al.types || []).filter((x) => x.on).length;
// the phone list's one-line state and the title-row badge
const alertsState = (al) => axt("settings_alerts_summary", { count: alertsOnCount(al) });

const alertsRange = (min, max) => (v) => (/^\d+$/.test(v.trim()) && +v >= min && +v <= max ? null : axt("alerts_error_whole", { low: min, high: max }));

// ----- On this device: applies at once, needs no code, kept in this browser -----
function alertsDevice() {
  const N = () => SW_ALERTS.notifications;
  const res = sbox(), note = h("div", { class: "alerts__state", role: "status" });
  const notify = SW.toggle({ label: axt("settings_alerts_browser"), hint: axt("settings_alerts_browser_hint"), checked: N().on() });
  let painting = false;
  // the switch and the words under it from what the browser says now: Blocked, or cannot show notifications
  const paint = () => {
    const say1 = N().say(), on = N().on();
    painting = true;
    notify._control.checked = on;
    notify._control.dispatchEvent(new Event("change"));      // the toggle writes its On / Off word
    painting = false;
    notify.dataset.state = say1 ? "refused" : on ? "on" : "off";
    note.replaceChildren(...(say1 ? [h("b", { textContent: say1.text }), " ", say1.hint] : []));
  };
  notify._control.addEventListener("change", async () => {
    if (painting) return;
    const want = notify._control.checked;
    SW.result(res, null);
    notify._control.setAttribute("aria-busy", "true");
    try {
      await N().set(want);
    } finally {
      notify._control.removeAttribute("aria-busy");
    }
    paint();
    if (!N().say()) say(res, axt(N().on() ? "alerts_notify_on" : "alerts_notify_off"), true);
  });
  const test = SW.btn(axt("settings_alerts_test"), { action: "alerts-test", run: async () => {
    SW.busy(test, SW.t("common_working"));
    try {
      await api("alerts/test", { client: SW_ALERTS.clientId() });
      say(res, axt("settings_alerts_sent"), true);
      SW_ALERTS.poll();                                       // it arrives in the next events answer: ask now, not in 10 s
    } catch (e) {
      if (e.status === 404 || e.status === 405) test.hidden = true;       // an older Hub: the button is hidden
      else say(res, hubText(e.message), false);
    } finally { SW.idle(test); }
  } });
  const recent = SW.btn(axt("settings_alerts_recent"), { cls: "btn--ghost", action: "alerts-recent", run: (e) => SW_ALERTS.openList(e.currentTarget) });
  paint();
  return h("section", { class: "alerts__device", "aria-labelledby": "alerts-device-title" },
    h("h3", { class: "settings__heading", id: "alerts-device-title", textContent: axt("settings_alerts_this_device") }),
    notify, note, h("div", { class: "actions" }, test, recent), res);
}

// ----- the group -----
function alertsGroup({ al }) {
  const sw = (key, label, hint, on) => {
    const t1 = SW.toggle({ label, hint, checked: !!on });
    t1._control.name = key;                                   // the form group reads the named controls
    return t1;
  };
  const types = Array.isArray(al.types) && al.types.length
    ? al.types
    : ALERT_TYPES.map((key) => ({ key, title: axt("alerts_type_" + key), on: false }));
  const switches = [];
  for (const item of types) {
    const key = item.key;
    const lbl = SW.t("alerts_type_" + key) !== "alerts_type_" + key ? axt("alerts_type_" + key) : (item.title || key);
    switches.push(sw(key, lbl, key === "device_down" ? axt("alerts_type_device_down_hint") : undefined, item.on));
    if (key === "running_low") {
      const sub = sw("last_spool_only", axt("settings_alerts_last_spool"), undefined, al.last_spool_only);
      sub.classList.add("alerts__sub");
      switches.push(sub);
    }
  }
  const bad = al.storage_bad_percent ?? 95;
  const hum = al.humidity_high_pct ?? 45;
  let el;
  const fields = [
    requiredField({ name: "running_low_g", type: "number", mono: true, label: axt("settings_alerts_running_low"), unit: axt("settings_alerts_running_low_unit"), hint: axt("settings_alerts_running_low_hint"),
      value: String(al.running_low_g), check: alertsRange(10, 2000) }),
    requiredField({ name: "storage_warn_percent", type: "number", mono: true, label: axt("settings_alerts_storage_warn"), unit: axt("settings_alerts_storage_unit"),
      hint: axt("settings_alerts_storage_hint", { percent: bad }), value: String(al.storage_warn_percent),
      check: (v) => {
        const b = Number(el?.elements?.storage_bad_percent?.value ?? bad);
        const max = Number.isFinite(b) && b > 1 ? b - 1 : 98;
        return alertsRange(1, max)(v);
      } }),
    requiredField({ name: "storage_bad_percent", type: "number", mono: true, label: axt("alerts_storage_bad_pct"), unit: axt("settings_alerts_storage_unit"),
      value: String(bad), check: alertsRange(2, 99) }),
    requiredField({ name: "humidity_high_pct", type: "number", mono: true, label: axt("alerts_humidity_high_pct"), unit: axt("alerts_humidity_unit"),
      value: String(hum), check: alertsRange(1, 99) }),
  ];
  el = setGroup("alerts", { desc: axt("settings_alerts_desc"), save: axt("settings_alerts_save"),
    badges: [SW.status("note", { small: true, cls: "alerts__count", text: alertsState(al) })],
    body: [h("div", { class: "alerts__types" }, ...switches), ...fields] });
  el._after = alertsDevice();

  const syncDependents = () => {
    const form = el.elements;
    const setOff = (ctrl, off) => {
      if (!ctrl) return;
      const wrap = ctrl.closest(".toggle, .field");
      if (wrap) {
        if (off && wrap.dataset.state !== "error") wrap.dataset.state = "off";
        else if (!off && wrap.dataset.state === "off") wrap.removeAttribute("data-state");
      }
      ctrl.disabled = !!off;
    };
    setOff(form.last_spool_only, !form.running_low?.checked);
    setOff(form.running_low_g, !form.running_low?.checked);
    setOff(form.storage_warn_percent, !form.storage?.checked);
    setOff(form.storage_bad_percent, !form.storage?.checked);
    setOff(form.humidity_high_pct, !form.humidity?.checked);
  };
  el.addEventListener("change", syncDependents);
  el.addEventListener("sw:dirty", syncDependents);
  syncDependents();

  // one request for the whole group: the first changed key sends all the fields, the others have nothing more to send
  let plan = null;
  const o = { send: null };
  const typeKeys = types.map((x) => x.key);
  o.send = async (key) => {
    if (!plan) {
      const form = el.elements, body = {
        types: typeKeys.map((k) => ({ key: k, on: form[k].checked })),
        last_spool_only: form.last_spool_only ? form.last_spool_only.checked : al.last_spool_only,
        running_low_g: Number(form.running_low_g.value),
        storage_warn_percent: Number(form.storage_warn_percent.value),
        storage_bad_percent: Number(form.storage_bad_percent.value),
        humidity_high_pct: Number(form.humidity_high_pct.value),
      };
      plan = { left: o.group.changed(), reply: null };
      try { plan.reply = await api("settings/alerts", { ...body, code: theCode() }); } catch (e) { plan = null; throw setRefusal(el, e); }
    }
    plan.left = plan.left.filter((k) => k !== key);
    return {};
  };
  o.send.reset = () => { plan = null; };
  wireGroup(el, "alerts", o);
  return el;
}
