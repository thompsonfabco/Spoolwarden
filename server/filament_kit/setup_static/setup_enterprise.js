// The Enterprise section: multi-user sign-in, role permissions, SSO (LDAP/LDAPS & SAML), certificates and user activity.
// Loaded after setup.js; everything from the server goes in through textContent, never innerHTML.
"use strict";

const ENT_TOGGLES = [
  "multiuser_login",
  "setup_code_fallback",
  "rbac",
  "local_accounts",
  "ldap_sso",
  "saml_sso",
  "activity_audit",
  "cert_management",
];

const ENT_PERM_LEVELS = ["NA", "RO", "RW"];

const ENT_DEFAULT_SECTIONS = [
  { id: "overview", labelKey: "nav_overview", functions: ["view"] },
  { id: "printers", labelKey: "nav_printers", functions: ["view", "manage", "test"] },
  { id: "spools", labelKey: "nav_spools", functions: ["view", "edit", "load"] },
  { id: "analytics", labelKey: "nav_analytics", functions: ["view", "export"] },
  { id: "recent", labelKey: "nav_recently_added", functions: ["view", "edit"] },
  { id: "devices", labelKey: "nav_devices", functions: ["view", "pair", "revoke"] },
  { id: "pre" + "sets", labelKey: "nav_slicer_presets", functions: ["view", "build"] },
  { id: "features", labelKey: "nav_features", functions: ["view", "toggle"] },
  { id: "settings", labelKey: "nav_settings", functions: ["view", "edit"] },
  { id: "flash", labelKey: "nav_flash_device", functions: ["flash"] },
  { id: "updates", labelKey: "nav_updates", functions: ["view", "apply"] },
  { id: "logs", labelKey: "nav_logs", functions: ["view", "export"] },
  { id: "enterprise", labelKey: "nav_enterprise", functions: ["view", "manage"] },
];

let entSubTab = "overview";
let entActFilters = { user: "", category: "" };

const entSubTabFromHash = () => {
  const part = location.hash.slice(1).split("/")[1] || "";
  return ["overview", "users", "sso", "certs", "activity"].includes(part) ? part : entSubTab;
};

// ----- Sign-in screen (rendered when S.enterprise.login_required is true) -----
function enterpriseSignInCard() {
  const box = sbox();
  const userField = SW.field({ label: SW.t("ent_signin_username"), name: "username", mono: true,
    check: (v) => (v.trim() ? null : SW.t("ent_user_error_username")) });
  const passField = SW.field({ label: SW.t("ent_signin_password"), name: "password", type: "secret" });
  const submitBtn = SW.btn(SW.t("ent_signin_submit"), { cls: "btn--primary", type: "submit" });
  const samlOn = !!(S && S.enterprise && S.enterprise.saml_sso);
  const samlBtn = samlOn
    ? SW.btn(SW.t("ent_signin_sso_saml"), { cls: "btn--ghost", run: () => { location.assign("/auth/saml/login"); } })
    : null;

  const form = h("form", { class: "ent__form", on: { submit: async (e) => {
    e.preventDefault();
    const u = userField._control.value.trim();
    const p = passField._control.value;
    if (!u || !p) { say(box, SW.t("ent_signin_error_empty"), false); return; }
    SW.busy(submitBtn, SW.t("common_working"));
    try {
      const r = await fetch("/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username: u, password: p }),
      });
      let data = {};
      try { data = await r.json(); } catch {}
      if (!r.ok || data.error) throw new Error(data.error || `HTTP ${r.status}`);
      passField._control.value = "";
      await refresh();
    } catch (err) {
      say(box, err.message || SW.t("ent_signin_failed"), false);
    } finally {
      SW.idle(submitBtn);
    }
  } } },
    userField,
    passField,
    h("div", { class: "ent__actions" }, submitBtn, samlBtn),
    box);

  return h("section", { class: "card ent__signin" },
    h("h2", {}, SW.t("ent_signin_title")),
    h("p", { class: "muted" }, SW.t("ent_signin_subtitle", { hub: (S && S.server_name) || "" })),
    form);
}

// ----- Overview & Toggles -----
function entOverviewPanel(data, out) {
  const box = boxFor("ent:toggles");
  const toggles = data.toggles || {};
  const policy = data.policy || {};
  const switches = {};
  const toggleEls = ENT_TOGGLES.map((key) => {
    const t = SW.toggle({
      label: SW.t("ent_toggle_" + key),
      hint: SW.t("ent_toggle_" + key + "_hint"),
      checked: !!toggles[key],
    });
    t._control.name = key;
    switches[key] = t;
    return t;
  });

  const saveBtn = SW.btn(SW.t("common_save"), { cls: "btn--primary", run: () => {
    const nextToggles = {};
    for (const key of ENT_TOGGLES) nextToggles[key] = !!switches[key]._control.checked;
    change(box, "enterprise/toggles", { toggles: nextToggles, policy }, () => say(box, SW.t("ent_toggles_saved"), true));
  } });

  out.replaceChildren(
    h("section", { class: "card" },
      h("h2", {}, SW.t("ent_overview_title")),
      h("p", { class: "muted" }, SW.t("ent_overview_desc"))),
    h("section", { class: "card" },
      h("h2", {}, SW.t("ent_toggles_title")),
      h("div", { class: "ent__toggles" }, ...toggleEls),
      h("div", { class: "ent__actions" }, saveBtn),
      box));
}

// ----- Users & Roles -----
function entUserDialog(roles, existing, box) {
  const isEdit = !!existing;
  const roleOpts = (roles || []).map((r) => ({ value: r.id || r.name, label: r.name || r.id }));
  if (!roleOpts.length) {
    roleOpts.push(
      { value: "administrator", label: "administrator" },
      { value: "operator", label: "operator" },
      { value: "viewer", label: "viewer" },
    );
  }

  const username = SW.field({ label: SW.t("ent_user_field_username"), name: "username", mono: true,
    value: existing ? existing.username : "", disabled: isEdit,
    check: (v) => (v.trim() ? null : SW.t("ent_user_error_username")) });
  const display = SW.field({ label: SW.t("ent_user_field_display"), name: "display_name", optional: true,
    value: existing ? (existing.display_name || "") : "" });
  const role = SW.field({ label: SW.t("ent_user_field_role"), name: "role", type: "select",
    value: existing ? (existing.role || roleOpts[0].value) : roleOpts[0].value, options: roleOpts });
  const password = SW.field({ label: SW.t("ent_user_field_password"), name: "password", type: "secret",
    optional: isEdit, hint: isEdit ? SW.t("ent_user_field_password_hint") : undefined });
  const active = SW.check({ label: SW.t("ent_user_field_active"), name: "active",
    checked: existing ? existing.active !== false : true });

  const uid = existing ? (existing.id || existing.username) : "";
  const path = isEdit ? `enterprise/users/${encodeURIComponent(uid)}` : "enterprise/users";
  asking(box, path, {
    title: SW.t(isEdit ? "ent_users_edit" : "ent_users_add"),
    fields: [username, display, role, password],
    body: [active],
    confirm: SW.t("common_save"),
  }, () => {
    const body = {
      username: username._control.value.trim(),
      display_name: display._control.value.trim(),
      role: role._control.value,
      active: !!active._control.checked,
      enabled: !!active._control.checked,
    };
    if (!isEdit && password._control.value) body.password = password._control.value;
    return body;
  }, async () => {
    if (isEdit && password._control.value) {
      try {
        await api(`enterprise/users/${encodeURIComponent(uid)}/password`, {
          password: password._control.value,
          code: theCode(),
        });
      } catch (err) {
        say(box, err.message, false);
        return;
      }
    }
    say(box, SW.t("ent_user_saved"), true);
  });
}

function entPermSegment(name, value, labelText, onChange) {
  const seg = SW.segmented({
    legend: labelText,
    legendSr: true,
    name,
    value: ENT_PERM_LEVELS.includes(value) ? value : "NA",
    options: ENT_PERM_LEVELS.map((lvl) => ({ value: lvl, label: SW.t("ent_perm_" + lvl.toLowerCase()) })),
  });
  seg.addEventListener("change", (e) => {
    if (e.target && e.target.name === name) onChange(e.target.value);
  });
  return seg;
}

function entRoleCard(role, sections, box) {
  const roleId = role.id || role.name;
  const secLevels = { ...(role.sections || {}) };
  const funcLevels = { ...(role.functions || role.overrides || {}) };
  const legacyPerms = role.permissions || {};

  const sectionRows = (sections || ENT_DEFAULT_SECTIONS).map((sec) => {
    const secLabel = sec.labelKey ? SW.t(sec.labelKey) : (sec.label || sec.id);
    const funcs = Array.isArray(sec.functions) && sec.functions.length ? sec.functions : ["view"];
    const secLegacy = legacyPerms[sec.id] && typeof legacyPerms[sec.id] === "object" ? legacyPerms[sec.id] : {};
    const currentVals = funcs.map((fn) => funcLevels[`${sec.id}.${fn}`] || funcLevels[fn] || secLegacy[fn] || secLevels[sec.id] || "NA");
    const masterVal = secLevels[sec.id] || (currentVals.every((v) => v === currentVals[0]) ? currentVals[0] : "NA");
    secLevels[sec.id] = ENT_PERM_LEVELS.includes(masterVal) ? masterVal : "NA";
    const funcSegs = {};

    const masterSeg = entPermSegment(`master-${roleId}-${sec.id}`, secLevels[sec.id], `${secLabel} ${SW.t("ent_perm_master")}`, (lvl) => {
      secLevels[sec.id] = lvl;
      for (const fn of funcs) {
        funcLevels[`${sec.id}.${fn}`] = lvl;
        const fSeg = funcSegs[fn];
        if (fSeg) {
          for (const inp of fSeg.querySelectorAll("input[type=radio]")) inp.checked = inp.value === lvl;
        }
      }
    });

    const funcEls = funcs.map((fn, idx) => {
      const initVal = currentVals[idx];
      funcLevels[`${sec.id}.${fn}`] = ENT_PERM_LEVELS.includes(initVal) ? initVal : "NA";
      const fSeg = entPermSegment(`fn-${roleId}-${sec.id}-${fn}`, funcLevels[`${sec.id}.${fn}`], `${secLabel} ${fn}`, (lvl) => {
        funcLevels[`${sec.id}.${fn}`] = lvl;
        const allSame = funcs.every((f) => funcLevels[`${sec.id}.${f}`] === lvl);
        if (allSame) secLevels[sec.id] = lvl;
      });
      funcSegs[fn] = fSeg;
      return h("div", { class: "ent__func-row" },
        h("span", { class: "ent__func-name", textContent: fn }),
        fSeg);
    });

    return h("div", { class: "ent__section" },
      h("div", { class: "ent__section-head" },
        h("h4", { class: "ent__section-title", textContent: secLabel }),
        masterSeg),
      h("div", { class: "ent__funcs" }, ...funcEls));
  });

  const saveBtn = SW.btn(SW.t("ent_role_save"), { cls: "btn--primary", run: () => {
    change(box, "enterprise/roles", {
      id: roleId,
      name: role.name,
      sections: secLevels,
      functions: funcLevels,
      overrides: funcLevels,
    }, () => say(box, SW.t("ent_role_saved"), true));
  } });
  const delBtn = role.builtin ? null : SW.btn(SW.t("ent_role_delete"), { cls: "danger", run: () => {
    asking(box, `enterprise/roles/${encodeURIComponent(roleId)}/delete`,
      destroy("ent_role_delete_title", "ent_role_delete_text", "ent_role_delete", { role: role.name }),
      () => ({}), () => say(box, SW.t("ent_role_deleted"), true));
  } });

  return h("div", { class: "ent__role-card" },
    h("div", { class: "ent__role-head" },
      h("h3", { class: "ent__role-name", textContent: role.name }),
      h("div", { class: "ent__actions" }, saveBtn, delBtn)),
    ...sectionRows);
}

function entUsersPanel(data, out) {
  const userBox = boxFor("ent:users");
  const roleBox = boxFor("ent:roles");
  const users = Array.isArray(data.users) ? data.users : [];
  const customRoles = Array.isArray(data.roles) ? data.roles : [];
  const builtinRoles = Object.entries(data.builtin_roles || {}).map(([id, r]) => ({
    id,
    name: (r && r.name) || id,
    builtin: true,
    sections: (r && r.sections) || {},
    functions: (r && (r.functions || r.overrides)) || {},
    permissions: (r && r.permissions) || {},
  }));
  const seenRoleIds = new Set(customRoles.map((r) => r.id || r.name));
  const roles = [...builtinRoles.filter((r) => !seenRoleIds.has(r.id)), ...customRoles];
  const rawSec = data.sections;
  const sections = Array.isArray(rawSec) && rawSec.length
    ? rawSec
    : (rawSec && typeof rawSec === "object" && Object.keys(rawSec).length
      ? Object.entries(rawSec).map(([id, v]) => ({ id, label: (v && v.label) || id, functions: (v && v.functions) || ["view"] }))
      : ENT_DEFAULT_SECTIONS);

  const addUserBtn = SW.btn(SW.t("ent_users_add"), { cls: "btn--primary", icon: "plus",
    run: () => entUserDialog(roles, null, userBox) });

  const usersBody = users.length
    ? h("div", { class: "ent__table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {},
          h("th", {}, SW.t("ent_user_col_name")),
          h("th", {}, SW.t("ent_user_col_display")),
          h("th", {}, SW.t("ent_user_col_role")),
          h("th", {}, SW.t("ent_user_col_source")),
          h("th", {}, SW.t("ent_user_col_status")),
          h("th", {}, SW.t("ent_user_col_actions")))),
        h("tbody", {}, users.map((u) => h("tr", {},
          h("td", { class: "mono", textContent: u.username }),
          h("td", { textContent: u.display_name || "—" }),
          h("td", { textContent: u.role || "—" }),
          h("td", { textContent: u.source || "local" }),
          h("td", {}, SW.status(u.active === false ? "off" : "ok", { small: true, text: SW.t(u.active === false ? "ent_user_disabled" : "ent_user_active") })),
          h("td", {}, h("div", { class: "ent__actions" },
            SW.btn(SW.t("ent_user_edit_btn"), { cls: "btn--ghost", run: () => entUserDialog(roles, u, userBox) }),
            SW.btn(SW.t("ent_user_delete_btn"), { cls: "danger", run: () => {
              asking(userBox, `enterprise/users/${encodeURIComponent(u.id || u.username)}/delete`,
                destroy("ent_user_delete_title", "ent_user_delete_text", "ent_user_delete_btn", { user: u.username }),
                () => ({}), () => say(userBox, SW.t("ent_user_removed"), true));
            } }))))))))
    : SW.empty({ icon: "devices", title: SW.t("ent_users_empty_title"), text: SW.t("ent_users_empty_text") });

  const addRoleBtn = SW.btn(SW.t("ent_roles_add"), { cls: "btn--primary", icon: "plus", run: () => {
    const nameField = entryField({ label: SW.t("ent_role_name"), name: "name", mono: true }, "ent_role_error_name");
    asking(roleBox, "enterprise/roles", {
      title: SW.t("ent_roles_add"),
      fields: [nameField],
      confirm: SW.t("ent_role_save"),
    }, () => ({ name: nameField._control.value.trim(), sections: {}, functions: {} }), () => say(roleBox, SW.t("ent_role_saved"), true));
  } });

  const rolesBody = roles.length
    ? h("div", { class: "ent__roles" }, roles.map((r) => entRoleCard(r, sections, roleBox)))
    : SW.empty({ icon: "shield", title: SW.t("ent_roles_empty_title"), text: SW.t("ent_roles_empty_text") });

  out.replaceChildren(
    h("section", { class: "card" },
      h("div", { class: "ent__role-head" }, h("h2", {}, SW.t("ent_users_title")), addUserBtn),
      usersBody,
      userBox),
    h("section", { class: "card" },
      h("div", { class: "ent__role-head" }, h("h2", {}, SW.t("ent_roles_title")), addRoleBtn),
      rolesBody,
      roleBox));
}

// ----- SSO (LDAP / LDAPS & SAML) -----
function entSsoPanel(data, out) {
  const ldapBox = boxFor("ent:ldap");
  const samlBox = boxFor("ent:saml");
  const ldap = data.ldap || {};
  const saml = data.saml || {};

  const url = SW.field({ label: SW.t("ent_ldap_host"), name: "url", mono: true, value: ldap.url || "" });
  const starttls = SW.toggle({ label: SW.t("ent_ldap_tls"), checked: !!ldap.starttls });
  const bindDn = SW.field({ label: SW.t("ent_ldap_bind_dn"), name: "bind_dn", mono: true, optional: true, value: ldap.bind_dn || "" });
  const bindPw = SW.field({ label: SW.t("ent_ldap_bind_pw"), name: "bind_password", type: "secret", optional: true });
  const baseDn = SW.field({ label: SW.t("ent_ldap_base_dn"), name: "base_dn", mono: true, value: ldap.base_dn || "" });
  const filter = SW.field({ label: SW.t("ent_ldap_filter"), name: "user_filter", mono: true, optional: true, value: ldap.user_filter || "(uid={username})" });
  const attrGroup = SW.field({ label: SW.t("ent_ldap_attr_group"), name: "group_attr", mono: true, optional: true, value: ldap.group_attr || "memberOf" });
  const defaultRole = SW.field({ label: SW.t("ent_ldap_default_role"), name: "default_role", optional: true, value: ldap.default_role || "viewer" });

  const ldapPayload = () => {
    const body = {
      url: url._control.value.trim(),
      starttls: !!starttls._control.checked,
      bind_dn: bindDn._control.value.trim(),
      base_dn: baseDn._control.value.trim(),
      user_filter: filter._control.value.trim(),
      group_attr: attrGroup._control.value.trim(),
      default_role: defaultRole._control.value.trim(),
      verify_tls: ldap.verify_tls !== false,
    };
    if (bindPw._control.value) body.bind_password = bindPw._control.value;
    return body;
  };

  const ldapSave = SW.btn(SW.t("ent_ldap_save"), { cls: "btn--primary", run: () => {
    change(ldapBox, "enterprise/ldap", ldapPayload(), () => say(ldapBox, SW.t("ent_ldap_saved"), true), [bindPw._control]);
  } });
  const ldapTest = SW.btn(SW.t("ent_ldap_test"), { cls: "btn--ghost", run: () => coded(ldapBox, async () => {
    working(ldapBox, SW.t("common_checking"));
    const r = await api("enterprise/ldap/test", { ...ldapPayload(), code: theCode() });
    say(ldapBox, r.reachable ? SW.t("ent_ldap_test_ok") : SW.t("ent_ldap_test_fail", { reason: r.details || r.error || "" }), !!r.reachable);
  }) });

  const idpEntity = SW.field({ label: SW.t("ent_saml_idp_entity"), name: "idp_entity_id", mono: true, value: saml.idp_entity_id || "" });
  const ssoUrl = SW.field({ label: SW.t("ent_saml_sso_url"), name: "idp_sso_url", mono: true, value: saml.idp_sso_url || "" });
  const certPem = SW.field({ label: SW.t("ent_saml_cert"), name: "idp_cert_pem", type: "textarea", mono: true, optional: true, value: saml.idp_cert_pem || "" });
  const metaXml = SW.field({ label: SW.t("ent_saml_metadata_xml"), name: "metadata_xml", type: "textarea", mono: true, optional: true });

  const samlSave = SW.btn(SW.t("ent_saml_save"), { cls: "btn--primary", run: () => {
    const pem = certPem._control.value.trim();
    change(samlBox, "enterprise/saml", {
      idp_entity_id: idpEntity._control.value.trim(),
      idp_sso_url: ssoUrl._control.value.trim(),
      idp_cert_pem: pem,
      idp_cert: pem,
    }, () => say(samlBox, SW.t("ent_saml_saved"), true));
  } });
  const samlImport = SW.btn(SW.t("ent_saml_import"), { cls: "btn--ghost", run: () => {
    change(samlBox, "enterprise/saml/idp-metadata", { metadata_xml: metaXml._control.value }, () => {
      metaXml._control.value = "";
      say(samlBox, SW.t("ent_saml_imported"), true);
    });
  } });
  const spDownload = h("a", { class: "btn btn--ghost", href: "/auth/saml/metadata.xml", download: "sp-metadata.xml",
    textContent: SW.t("ent_saml_sp_download") });

  out.replaceChildren(h("div", { class: "ent__grid" },
    h("section", { class: "card" },
      h("h2", {}, SW.t("ent_ldap_title")),
      h("div", { class: "ent__form" }, url, starttls, bindDn, bindPw, baseDn, filter, attrGroup, defaultRole),
      h("div", { class: "ent__actions" }, ldapSave, ldapTest),
      ldapBox),
    h("section", { class: "card" },
      h("h2", {}, SW.t("ent_saml_title")),
      h("div", { class: "ent__form" }, idpEntity, ssoUrl, certPem, metaXml),
      h("div", { class: "ent__actions" }, samlSave, samlImport, spDownload),
      samlBox)));
}

// ----- Certificates -----
function entCertsPanel(data, out) {
  const certBox = boxFor("ent:certs");
  const caBox = boxFor("ent:ca");
  const c = data.cert || {};

  const kvRow = (key, val) => h("div", { class: "kv__row" },
    h("dt", { class: "kv__key", textContent: SW.t(key) }),
    h("dd", { class: "kv__value mono", textContent: val || "—" }));

  const csrOut = h("div");
  const renewBtn = SW.btn(SW.t("ent_cert_renew"), { cls: "btn--primary", run: () => {
    asking(certBox, "enterprise/certs/renew", {
      title: SW.t("ent_cert_renew_title"),
      text: SW.t("ent_cert_renew_text"),
      confirm: SW.t("ent_cert_renew"),
    }, () => ({}), () => say(certBox, SW.t("ent_cert_renewed"), true));
  } });

  const csrBtn = SW.btn(SW.t("ent_cert_csr_btn"), { cls: "btn--ghost", run: () => {
    const cn = SW.field({ label: SW.t("ent_cert_csr_cn"), name: "cn", mono: true, value: (S && S.server_name) || "" });
    const org = SW.field({ label: SW.t("ent_cert_csr_org"), name: "org", optional: true });
    const sans = SW.field({ label: SW.t("ent_cert_csr_sans"), name: "sans", mono: true, optional: true });
    asking(certBox, "enterprise/certs/csr", {
      title: SW.t("ent_cert_csr_btn"),
      fields: [cn, org, sans],
      confirm: SW.t("ent_cert_csr_btn"),
    }, () => ({
      cn: cn._control.value.trim(),
      org: org._control.value.trim(),
      sans: sans._control.value.split(",").map((s) => s.trim()).filter(Boolean),
    }), (r) => {
      if (r && r.csr) {
        csrOut.replaceChildren(
          h("h3", { textContent: SW.t("ent_cert_csr_out") }),
          h("pre", { class: "code__text", tabindex: "0", textContent: r.csr }));
      }
    });
  } });

  const upCert = SW.field({ label: SW.t("ent_cert_upload_pem"), name: "cert", type: "textarea", mono: true });
  const upKey = SW.field({ label: SW.t("ent_cert_upload_key"), name: "key", type: "textarea", mono: true, optional: true });
  const upBtn = SW.btn(SW.t("ent_cert_upload_btn"), { cls: "btn--primary", run: () => {
    change(certBox, "enterprise/certs/upload", {
      cert: upCert._control.value,
      key: upKey._control.value,
      chain: "",
    }, () => say(certBox, SW.t("ent_cert_uploaded"), true), [upCert._control, upKey._control]);
  } });

  const caStatus = data.ca_installed ? pill(true, SW.t("ent_user_active"), SW.t("ent_user_disabled")) : null;
  const caPem = SW.field({ label: SW.t("ent_cert_ca_pem"), name: "ca_pem", type: "textarea", mono: true, value: "" });
  const caBtn = SW.btn(SW.t("ent_cert_ca_save"), { cls: "btn--primary", run: () => {
    change(caBox, "enterprise/certs/ca", { ca_pem: caPem._control.value }, () => say(caBox, SW.t("ent_cert_ca_saved"), true));
  } });

  out.replaceChildren(
    h("section", { class: "card" },
      h("h2", {}, SW.t("ent_certs_title")),
      h("dl", { class: "kv" },
        kvRow("ent_cert_subject", c.subject),
        kvRow("ent_cert_issuer", c.issuer),
        kvRow("ent_cert_valid_from", c.valid_from),
        kvRow("ent_cert_valid_to", c.valid_to),
        kvRow("ent_cert_fingerprint", c.fingerprint),
        kvRow("ent_cert_sans", Array.isArray(c.sans) ? c.sans.join(", ") : c.sans)),
      h("div", { class: "ent__actions" }, renewBtn, csrBtn),
      csrOut,
      certBox),
    h("div", { class: "ent__grid" },
      h("section", { class: "card" },
        h("h2", {}, SW.t("ent_cert_upload_title")),
        h("div", { class: "ent__form" }, upCert, upKey),
        h("div", { class: "ent__actions" }, upBtn)),
      h("section", { class: "card" },
        h("div", { class: "ent__role-head" }, h("h2", {}, SW.t("ent_cert_ca_title")), caStatus),
        h("div", { class: "ent__form" }, caPem),
        h("div", { class: "ent__actions" }, caBtn),
        caBox)));
}

// ----- User Activity -----
function entActivityPanel(data, out, reload) {
  const box = boxFor("ent:activity");
  const entries = Array.isArray(data.events) ? data.events : [];
  const actorName = (row) => (row.actor && (row.actor.username || row.actor.id || row.actor)) || row.user || "";
  const users = Array.isArray(data.users)
    ? data.users
    : [...new Set(entries.map(actorName).filter(Boolean))];
  const categories = Array.isArray(data.categories)
    ? data.categories
    : [...new Set(entries.map((r) => (r.kind || r.action || "").split(".")[0]).filter(Boolean))];

  const userSel = SW.field({
    label: SW.t("ent_act_filter_user"),
    name: "filter_user",
    type: "select",
    value: entActFilters.user,
    options: [{ value: "", label: SW.t("ent_act_filter_all") }, ...users.map((u) => ({ value: u, label: u }))],
  });
  const catSel = SW.field({
    label: SW.t("ent_act_filter_action"),
    name: "filter_category",
    type: "select",
    value: entActFilters.category,
    options: [{ value: "", label: SW.t("ent_act_filter_all") }, ...categories.map((c) => ({ value: c, label: c }))],
  });
  userSel._control.addEventListener("change", () => { entActFilters.user = userSel._control.value; reload(); });
  catSel._control.addEventListener("change", () => { entActFilters.category = catSel._control.value; reload(); });

  const qs = new URLSearchParams();
  if (entActFilters.category) qs.set("category", entActFilters.category);
  if (entActFilters.user) qs.set("user", entActFilters.user);
  const csvUrl = "/setup/api/export/enterprise_activity.csv" + (qs.toString() ? "?" + qs.toString() : "");
  const csvBtn = h("a", { class: "btn btn--ghost", href: csvUrl, download: "enterprise-activity.csv", textContent: SW.t("ent_act_csv") });

  const table = entries.length
    ? h("div", { class: "ent__table-wrap" }, h("table", { class: "table" },
        h("thead", {}, h("tr", {},
          h("th", {}, SW.t("ent_act_col_when")),
          h("th", {}, SW.t("ent_act_col_user")),
          h("th", {}, SW.t("ent_act_col_action")),
          h("th", {}, SW.t("ent_act_col_detail")),
          h("th", {}, SW.t("ent_act_col_ip")))),
        h("tbody", {}, entries.map((row) => h("tr", {},
          h("td", { class: "mono", textContent: row.ts ? fmtDate(row.ts) : (row.when || "—") }),
          h("td", { class: "mono", textContent: actorName(row) || "—" }),
          h("td", { textContent: row.kind || row.action || "—" }),
          h("td", { textContent: row.summary || row.detail || "—" }),
          h("td", { class: "mono", textContent: row.ip || "—" }))))))
    : SW.empty({ icon: "logs", title: SW.t("ent_act_empty_title"), text: SW.t("ent_act_empty_text") });

  out.replaceChildren(h("section", { class: "card" },
    h("div", { class: "ent__role-head" }, h("h2", {}, SW.t("ent_act_title")), csvBtn),
    h("div", { class: "ent__filters" }, userSel, catSel),
    table,
    box));
}

// ----- Main Enterprise tab -----
function enterpriseTab() {
  entSubTab = entSubTabFromHash();
  const nav = SW.segmented({
    legend: SW.t("ent_subtab_legend"),
    legendSr: true,
    cls: "ent__nav",
    name: "ent-subtab",
    value: entSubTab,
    options: [
      { value: "overview", label: SW.t("ent_tab_overview") },
      { value: "users", label: SW.t("ent_tab_users") },
      { value: "sso", label: SW.t("ent_tab_sso") },
      { value: "certs", label: SW.t("ent_tab_certs") },
      { value: "activity", label: SW.t("ent_tab_activity") },
    ],
  });
  const body = h("div", { class: "ent__panel" });

  const loadSubTab = () => {
    if (entSubTab === "overview") {
      return later(readRegion(body, "enterprise", () => api("enterprise/overview"), (d) => entOverviewPanel(d, body)));
    }
    if (entSubTab === "users") {
      return later(readRegion(body, "enterprise", async () => {
        const [u, r] = await Promise.all([api("enterprise/users"), api("enterprise/roles")]);
        return {
          users: u.users || [],
          roles: r.roles || [],
          builtin_roles: r.builtin_roles || {},
          sections: r.sections || {},
        };
      }, (d) => entUsersPanel(d, body)));
    }
    if (entSubTab === "sso") {
      return later(readRegion(body, "enterprise", () => api("enterprise/sso"), (d) => entSsoPanel(d, body)));
    }
    if (entSubTab === "certs") {
      return later(readRegion(body, "enterprise", () => api("enterprise/certs"), (d) => entCertsPanel(d, body)));
    }
    const qs = new URLSearchParams();
    if (entActFilters.category) qs.set("category", entActFilters.category);
    if (entActFilters.user) qs.set("user", entActFilters.user);
    const path = "enterprise/activity" + (qs.toString() ? "?" + qs.toString() : "");
    return later(readRegion(body, "enterprise", () => api(path), (d) => entActivityPanel(d, body, loadSubTab)));
  };

  nav.addEventListener("change", (e) => {
    if (e.target && e.target.name === "ent-subtab") {
      entSubTab = e.target.value;
      history.replaceState(null, "", "#enterprise/" + entSubTab);
      loadSubTab();
    }
  });

  loadSubTab();
  return h("div", { class: "ent" }, nav, body);
}
