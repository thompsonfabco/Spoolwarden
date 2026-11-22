"use strict";
// The station's own page. Relative addresses only (the page lives at the station's own address); every text from the
// station is put on the page as text, never as markup.
(function () {
  var T = JSON.parse(document.getElementById("texts").textContent);
  var $ = function (id) { return document.getElementById(id); };
  var timer = null;

  function say(el, text) { el.textContent = text; }
  function send(path, body) {
    return fetch(path, {method: "POST", credentials: "same-origin", headers: {"Content-Type": "application/json"},
                        body: JSON.stringify(body)});
  }
  function openFromHub(text) {
    clearInterval(timer);
    $("status").textContent = "";           // the data goes off the page, not just out of sight
    $("settings").textContent = "";
    $("photo").removeAttribute("src");
    $("photo").hidden = true;
    $("status-section").hidden = true;
    $("settings-section").hidden = true;
    say($("note"), text);
  }

  function row(dl, name, value) {
    var dt = document.createElement("dt"), dd = document.createElement("dd");
    say(dt, name);
    say(dd, value === null || value === undefined || value === "" ? T["station_page_none"] : String(value));
    dl.appendChild(dt);
    dl.appendChild(dd);
  }

  // a catalogue text of the page's block with its {values} filled in; null when the block does not hold the key (then the
  // row shows the plain value, never an English fragment of this script's own)
  function tx(key, vars) {
    var v = T[key];
    if (typeof v !== "string") { return null; }
    return v.replace(/\{(\w+)\}/g, function (m, n) { return vars && vars[n] !== undefined && vars[n] !== null ? String(vars[n]) : m; });
  }
  // a word of the catalogue for a station id (a step, a scale state, a page-access mode), else the id itself
  function word(prefix, id) { return tx(prefix + String(id).replace(/[^a-z0-9]+/gi, "_").toLowerCase()) || id; }
  function aged(value, s) {
    return s && s.age_seconds !== null && s.age_seconds !== undefined ? tx("station_page_age", {value: value, seconds: s.age_seconds}) || value : value;
  }
  function grams(g) { return tx("station_page_grams", {grams: g}) || String(g); }
  function scaleState(st) { return /^unit /.test(st || "") ? tx("station_scale_unit") || st : word("station_scale_", st); }

  function showStatus(s) {
    var dl = $("status");
    dl.textContent = "";
    row(dl, T["station_page_tag"], s.tag && s.tag.nfc_id ? aged(s.tag.nfc_id, s.tag) : null);
    row(dl, T["station_page_scale"], s.scale ? tx("station_page_scale_value", {grams: s.scale.grams, status: scaleState(s.scale.status)}) || grams(s.scale.grams) : null);
    row(dl, T["station_page_last_weight"], s.last_weight ? aged(grams(s.last_weight.grams), s.last_weight) : null);
    row(dl, T["station_page_panel"], s.panel ? (s.panel_version ? tx("station_page_panel_firmware", {panel: s.panel, version: s.panel_version}) || s.panel : s.panel) : null);
    row(dl, T["station_page_air"], s.air ? tx("station_page_air_value", {temp: s.air.temp_c, rh: s.air.rh}) : null);
    row(dl, T["station_page_step"], s.flow ? (s.flow.message ? tx("station_page_step_message", {step: word("station_step_", s.flow.step), message: s.flow.message}) : null) || word("station_step_", s.flow.step) : null);
    row(dl, T["station_page_version"], s.version);
    var img = $("photo");
    if (s.photo) { img.src = "photo?page=1&t=" + encodeURIComponent(s.photo.taken_at || ""); img.hidden = false; } else { img.hidden = true; }
    say($("own-values"), T["station_page_own_values"]);
    $("own-values").hidden = !s.settings_changed;
  }

  function control(s) {
    var label = document.createElement("label"), name = document.createElement("span"), input;
    say(name, s.label);
    if (s.kind === "switch") {
      input = document.createElement("input");
      input.type = "checkbox";
      input.checked = s.value === true;
    } else if (s.kind === "choice") {
      input = document.createElement("select");
      (s.choices || []).forEach(function (c) {
        var o = document.createElement("option");
        o.value = c;
        say(o, (s.choice_labels || {})[c] || c);
        o.selected = c === s.value;
        input.appendChild(o);
      });
    } else {
      input = document.createElement("input");
      input.type = "number";
      input.min = s.min;
      input.max = s.max;
      input.step = 1;
      input.value = s.value;
    }
    input.dataset.id = s.id;
    input.dataset.kind = s.kind;
    if (s.readonly) { input.disabled = true; }       // the Hub's value is in force: the line says where to change it
    label.appendChild(name);
    label.appendChild(input);
    if (s.readonly && s.note) {
      var note = document.createElement("small");
      say(note, s.note);
      label.appendChild(note);
    }
    return label;
  }

  function showSettings(list) {
    var form = $("settings");
    form.textContent = "";
    list.forEach(function (s) { form.appendChild(control(s)); });
  }

  function values() {
    var out = {};
    $("settings").querySelectorAll("[data-id]").forEach(function (el) {
      if (el.disabled) { return; }
      out[el.dataset.id] = el.dataset.kind === "switch" ? el.checked : el.dataset.kind === "number" ? Number(el.value) : el.value;
    });
    return out;
  }

  function load(first) {
    return fetch("state?page=1", {credentials: "same-origin"}).then(function (r) {
      if (r.status === 401 || r.status === 403) {
        openFromHub(first ? T["station_page_open_from_hub"] : T["station_page_session_over"]);
        return null;
      }
      return r.json();
    }).then(function (s) {
      if (!s) { return null; }
      say($("note"), "");
      $("status-section").hidden = false;
      showStatus(s);
      if (!first) { return null; }
      return fetch("settings?page=1", {credentials: "same-origin"}).then(function (r) { return r.json(); }).then(function (d) {
        $("settings-section").hidden = false;
        const policy = $("page-from");
        const mode = d.page_from;
        policy.hidden = !["home", "subnets", "allowed"].includes(mode);
        if (!policy.hidden) {
          const label = T["station_page_page_from"];
          const note = mode === "subnets" ? " " + T["station_page_page_from_subnets_note"] : "";
          policy.textContent = label.replace("{mode}", word("station_page_mode_", mode)) + note;
        }
        showSettings(d.settings || []);
        timer = setInterval(function () { load(false); }, 5000);
      });
    }).catch(function () { say($("note"), T["station_page_unreachable"]); });
  }

  function save(body, done) {
    send("settings", body).then(function (r) { return r.json().then(function (d) { return [r.ok, d]; }); }).then(function (x) {
      if (x[0]) { showSettings(x[1].settings || []); say($("result"), done); load(false); }
      else { say($("result"), T["station_page_not_saved"].replace("{reason}", x[1].message || "")); }
    }).catch(function () { say($("result"), T["station_page_unreachable"]); });
  }

  function start() {
    say($("title"), T["station_page_title"]);
    say($("status-heading"), T["station_page_status"]);
    say($("settings-heading"), T["station_page_settings"]);
    say($("save"), T["station_page_save"]);
    say($("reset"), T["station_page_reset"]);
    $("save").addEventListener("click", function () { save(values(), T["station_page_saved"]); });
    // Enter in a field (Speaker volume) saves as Save does, and never submits the form to the page's own address (a reload)
    $("settings").addEventListener("submit", function (e) { e.preventDefault(); save(values(), T["station_page_saved"]); });
    $("reset").addEventListener("click", function () { save({reset: true}, T["station_page_reset_done"]); });
    var m = /(?:^#|&)ticket=([A-Za-z0-9_-]+)/.exec(location.hash);
    if (!m) { load(true); return; }      // no ticket: a browser with no session is refused by the station, and the page says so
    history.replaceState(null, "", location.pathname + location.search);       // the ticket leaves the address bar at once
    send("session", {ticket: m[1]}).then(function (r) {
      if (r.ok) { load(true); } else { openFromHub(T["station_page_open_from_hub"]); }
    }).catch(function () { say($("note"), T["station_page_unreachable"]); });
  }
  start();
}());
