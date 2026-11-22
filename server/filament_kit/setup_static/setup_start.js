// The Getting started checklist card shown on Overview until every step is done or hidden.
(() => {
  "use strict";

  const ACTION_KEYS = {
    features: "start_go_features",
    printer: "printers_add",
    slots: "printers_map_slots",
    phone: "start_go_devices",
    pc: "start_go_devices",
    spool: "start_open_web_app",
    backup: "settings_backups_now",
  };
  const KEEP_KEYS = {
    name: "start_keep_name",
    storage: "start_keep_storage",
    features: "start_keep_features",
  };

  function el(tag, props, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (v === undefined || v === null) continue;
      if (k === "on") {
        for (const [ev, fn] of Object.entries(v)) node.addEventListener(ev, fn);
      } else if (k === "class") {
        node.className = v;
      } else if (k in node && k !== "list" && k !== "style") {
        node[k] = v;
      } else {
        node.setAttribute(k, v);
      }
    }
    for (const kid of kids.flat()) {
      if (kid != null && kid !== false) {
        node.append(kid instanceof Node ? kid : String(kid));
      }
    }
    return node;
  }

  function sw() {
    return window.SW || {};
  }

  function t(key, vars) {
    const s = sw();
    return typeof s.t === "function" ? s.t(key, vars) : key;
  }

  function button(label, opts) {
    const s = sw();
    if (typeof s.btn === "function") return s.btn(label, opts);
    const cls = (opts && opts.cls) || "btn--primary";
    const attrs = { type: "button", class: "btn " + cls, textContent: label };
    if (opts && opts.action) attrs["data-action"] = opts.action;
    const b = el("button", attrs);
    if (opts && opts.run) b.addEventListener("click", (ev) => opts.run(ev));
    return b;
  }

  function statusTag(kind, text) {
    const s = sw();
    if (typeof s.status === "function") return s.status(kind, { small: true, text });
    return el("span", { class: "start__state", textContent: text });
  }

  function showResult(box, kind, msg) {
    const s = sw();
    if (typeof s.result === "function") {
      s.result(box, kind, msg);
      return;
    }
    box.dataset.state = kind || "";
    box.textContent = msg || "";
  }

  function hubName(step, opts) {
    if (opts && opts.hubName) return opts.hubName;
    const m = /"([^"]+)"/.exec(String((step && step.line) || ""));
    return m ? m[1] : "Hub";
  }

  function webAppHref(target, opts) {
    if (opts && opts.webUrl) return opts.webUrl;
    const frag = String(target || "").slice(4);
    const base = location.pathname.replace(/\/(setup)?\/?$/, "");
    return base + "/" + ["in", "take"].join("") + (frag ? "#" + frag : "");
  }

  async function askApi(opts, body) {
    if (opts?.api) return opts.api(body);
    const fn = (opts && opts.fetch) || window.fetch.bind(window);
    const init = body === undefined
      ? { method: "GET", credentials: "same-origin", cache: "no-store" }
      : {
          method: "POST",
          credentials: "same-origin",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        };
    let r;
    try {
      r = await fn("/setup/api/start", init);
    } catch {
      throw new Error(t("common_hub_no_answer"));
    }
    let data;
    try {
      data = await r.json();
    } catch {
      data = { error: "HTTP " + r.status };
    }
    if (!r.ok || (data && data.error)) {
      throw new Error((data && data.error) || ("HTTP " + r.status));
    }
    return data;
  }

  function skeletonCard() {
    return el(
      "section",
      { class: "card start start--loading", "aria-busy": "true", "aria-label": t("start_title") },
      el("div", { class: "skeleton__line start__skel-title", "aria-hidden": "true" }),
      el("div", { class: "skeleton__line", "aria-hidden": "true" }),
      el("div", { class: "skeleton__line", "aria-hidden": "true" }),
      el("div", { class: "skeleton__line", "aria-hidden": "true" })
    );
  }

  function stateBadge(state) {
    if (state === "done") return statusTag("ok", t("start_state_done"));
    if (state === "next") return el("span", { class: "start__badge start__badge--next", textContent: t("start_state_next") });
    return el("span", { class: "start__badge start__badge--open muted", textContent: t("start_state_not_yet") });
  }

  function gettingStartedCard(mount, opts) {
    const options = opts || {};
    let busy = false;

    function notify(data) {
      if (typeof options.onChange === "function") options.onChange(data);
    }

    async function post(payload, trigger, errBox) {
      if (busy) return;
      busy = true;
      if (trigger) trigger.disabled = true;
      if (errBox) showResult(errBox, null, "");
      try {
        const fresh = await askApi(options, payload);
        if (!fresh) return;
        paint(fresh);
        notify(fresh);
      } catch (e) {
        if (errBox) showResult(errBox, "bad", e.message);
      } finally {
        busy = false;
        if (trigger && trigger.isConnected) trigger.disabled = false;
      }
    }

    function actionControl(step) {
      const key = ACTION_KEYS[step.id] || "start_open_settings";
      const label = t(key);
      const isNext = step.state === "next";
      const cls = isNext ? "btn--primary btn--small start__act" : "btn--ghost btn--small start__act";
      if (step.target && String(step.target).startsWith("web:")) {
        return el("a", {
          class: "btn " + cls,
          href: webAppHref(step.target, options),
          textContent: label,
        });
      }
      return button(label, {
        cls,
        action: "start-go-" + step.id,
        run: () => {
          if (step.id === "reader") window.__swReaderArrived = true;
          if (step.target) location.hash = step.target;
        },
      });
    }

    function keepControl(step, errBox) {
      const key = KEEP_KEYS[step.id];
      if (!key || step.state === "done") return null;
      const vars = step.id === "name" ? { name: hubName(step, options) } : undefined;
      return button(t(key, vars), {
        cls: "btn--ghost btn--small start__keep",
        action: "start-keep-" + step.id,
        run: (ev) => post({ keep: step.id }, ev.currentTarget, errBox),
      });
    }

    function stepRow(step, phoneOpen, errBox) {
      const classes = ["start__step"];
      if (step.state === "next") classes.push("start__step--next");
      else if (step.state === "done") classes.push("start__step--done");
      const metaKids = [
        el("div", { class: "start__step-top" },
          stateBadge(step.state),
          el("span", { class: "start__step-title", textContent: t("start_step_" + step.id) })),
        step.line ? el("p", { class: "muted start__step-line", textContent: step.line }) : null,
        step.id === "spool" && phoneOpen && step.state !== "done"
          ? el("p", { class: "muted start__step-note", textContent: t("start_do_from_phone") })
          : null,
      ];
      const acts = [];
      if (step.state !== "done") {
        const keep = keepControl(step, errBox);
        if (keep) acts.push(keep);
        acts.push(actionControl(step));
      }
      return el(
        "li",
        { class: classes.join(" "), "data-step": step.id, "data-state": step.state },
        el("div", { class: "start__step-body" }, ...metaKids),
        acts.length ? el("div", { class: "start__step-actions" }, ...acts) : null
      );
    }

    function paintHidden(data) {
      const errBox = el("div", { class: "result result--block", role: "status" });
      const row = el(
        "div",
        { class: "start__hidden" },
        el("span", { class: "muted start__hidden-line", textContent: t("start_line", { done: data.done, total: data.total }) }),
        button(t("start_show"), {
          cls: "btn--ghost btn--small start__show",
          action: "start-show",
          run: (ev) => post({ hide: false }, ev.currentTarget, errBox),
        }),
        errBox
      );
      mount.replaceChildren(row);
    }

    function paint(data) {
      if (!data) {
        mount.replaceChildren();
        return;
      }
      if (!data.show) {
        if (data.hidden && !data.finished) paintHidden(data);
        else mount.replaceChildren();
        return;
      }
      const steps = Array.isArray(data.steps) ? data.steps : [];
      const allSet = data.total > 0 && data.done >= data.total;
      const errBox = el("div", { class: "result result--block", role: "status" });
      const phoneOpen = steps.some((s) => s.id === "phone" && s.state !== "done");
      const head = el(
        "div",
        { class: "start__head" },
        el(
          "div",
          { class: "start__head-text" },
          el("h2", { class: "start__title", textContent: t("start_title") }),
          el("span", { class: "muted start__count", textContent: t("start_count", { done: data.done, total: data.total }) })
        ),
        !allSet
          ? button(t("start_hide"), {
              cls: "btn--ghost btn--small start__hide",
              action: "start-hide",
              run: (ev) => post({ hide: true }, ev.currentTarget, errBox),
            })
          : null
      );
      const restore = data.restore_offer
        ? el(
            "div",
            { class: "start__restore" },
            el("span", { class: "muted", textContent: t("start_restore_row") }),
            button(t("start_restore_button"), {
              cls: "btn--ghost btn--small",
              action: "start-restore",
              run: () => { location.hash = "#settings/backups"; },
            })
          )
        : null;
      const doneBanner = allSet
        ? el("div", { class: "start__allset" }, statusTag("ok", t("start_all_set")))
        : null;
      const list = el("ol", { class: "start__steps" }, ...steps.map((s) => stepRow(s, phoneOpen, errBox)));
      const card = el(
        "section",
        { class: allSet ? "card start start--allset" : "card start", "aria-label": t("start_title") },
        head,
        restore,
        doneBanner,
        errBox,
        list
      );
      mount.replaceChildren(card);
    }

    async function load() {
      mount.replaceChildren(skeletonCard());
      try {
        const data = await askApi(options);
        paint(data);
        notify(data);
        return data;
      } catch {
        mount.replaceChildren();
        notify(null);
        return null;
      }
    }

    load();
    return { refresh: load };
  }

  window.gettingStartedCard = gettingStartedCard;
})();
