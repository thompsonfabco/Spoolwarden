// The web app's print page: the size choice, the labels the Hub draws, the one line about
// the printed address and Print, which opens the browser's own print dialog. The page draws no label of its own: each one is the Hub's
// `spools/<id>/label.svg`, shown as an image (it runs nothing) and printed as it is. A sheet size lays the labels on the sheet by its own
// margins and pitches (app.css), 24 or 30 to a page, and states its paper (labels/page.css).
(() => {
"use strict";
const root = document.querySelector(".labels");
if (!root || !window.SW) return;
const base = location.pathname.replace(/\/labels\/?$/, "");
const ids = root.dataset.ids.split(",").map(Number);
let size = root.dataset.size;
const $ = (s) => root.querySelector(s);

// the five sizes: two for a label roll, three for a sheet (`per`: labels to a page)
const SIZES = [{ value: "62x29" }, { value: "50x30" }, { value: "a4-63x34", per: 24 }, { value: "a4-70x37", per: 24 }, { value: "letter-66x25", per: 30 }];
const sizeOf = (v) => SIZES.find((s) => s.value === v) || SIZES[0];

const tx = (key, vars) => SW.t(key, vars);
const el = (tag, props, ...kids) => { const e = Object.assign(document.createElement(tag), props || {}); e.append(...kids.filter((k) => k != null)); return e; };

const title = tx(ids.length === 1 ? "labels_title_one" : "labels_title_many");
$(".labels__title").textContent = title;
document.title = title;
const back = $(".labels__back");
if (back) {
  back.textContent = tx(window.opener ? "common_close" : "common_back");
  back.addEventListener("click", (e) => {
    if (window.opener) { e.preventDefault(); window.close(); }
    else if (history.length > 1) { e.preventDefault(); history.back(); }
  });
}
// the address the codes hold is the Hub's home address, which the page carries (the Hub's own name); this page's origin only when it has none
$(".labels__note").textContent = tx("labels_address_note", { address: root.dataset.address || location.origin });
const print = $(".labels__print");
print.textContent = tx("labels_print");
print.disabled = true;
print.addEventListener("click", () => window.print());

// the demo banner is on every screen, this one too; it is not printed
if (root.dataset.demo === "1") {
  const leave = SW.btn(tx("demo_leave"), { action: "leave-demo", run: async (e) => {
    const done = await SW.ask({ kind: "destructive", title: tx("demo_leave_title"), text: tx("demo_leave_text"), confirm: tx("demo_leave"), opener: e.currentTarget, run: async () => {
      const r = await fetch(`${base}/api/demo/leave`, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json" }, body: "{}" });
      if (r.ok) return true;
      let text = "";
      try {
        const j = await r.json();
        if (r.status === 409 && j.error === "not_in_demo") return true;   // it is off already; any other 409 is the Hub's refusal
        text = j.message || j.error || "";
      } catch (err) { /* not JSON */ }
      throw Object.assign(new Error(text), { text: () => text });
    } });
    if (done) location.reload();
  } });
  $(".labels__chrome").prepend(SW.banner({ kind: "info", text: tx("demo_banner"), actions: [leave] }));
}

let drawn = 0, urls = [];
// what the preview's text says: the number, the filament, its material and net weight, read from the spool
function ariaOf(id, v) {
  const name = v && [v.vendor, v.name].filter(Boolean).join(" ");
  if (!name || !v.material || typeof v.net_g !== "number") return tx("labels_aria_plain", { number: id });
  return tx("labels_aria", { number: id, name, material: v.material, net: tx("labels_net", { grams: Math.round(v.net_g) }) });
}
const FETCH_MS = 15000;
async function timedFetch(url) {
  const c = new AbortController(), t = setTimeout(() => c.abort(), FETCH_MS);
  try { return await fetch(url, { credentials: "same-origin", signal: c.signal }); }
  finally { clearTimeout(t); }
}
const views = new Map();                                    // a spool is read once: a size change asks only for the labels again
function viewOf(id) {
  if (!views.has(id)) views.set(id, (async () => {
    try { const r = await timedFetch(`${base}/api/spools/${id}`); return r.ok ? (await r.json()).view || null : null; }
    catch (e) { return null; }
  })().then((v) => { if (!v) views.delete(id); return v; }));      // a spool not read is asked again at the next draw
  return views.get(id);
}
async function one(id) {
  const [r, view] = await Promise.all([timedFetch(`${base}/api/spools/${id}/label.svg?size=${encodeURIComponent(size)}`), viewOf(id)]);
  if (!r.ok) {
    let text = "";
    try { const j = await r.json(); text = j.message || j.error || ""; } catch (e) { /* not JSON */ }
    return { id, status: r.status, text };
  }
  return { id, url: URL.createObjectURL(await r.blob()), label: ariaOf(id, view) };
}
function image(g, cls) {                                    // an img with role img and the text of what is on it
  const i = el("img", { className: cls, src: g.url, alt: g.label });
  i.setAttribute("role", "img");
  i.setAttribute("aria-label", g.label);
  return i;
}
async function draw() {
  const mine = ++drawn, sheet = $(".labels__sheet"), err = $(".labels__error"), missing = $(".labels__missing");
  print.disabled = true;
  err.hidden = true;
  missing.replaceChildren();
  for (const u of urls) URL.revokeObjectURL(u);
  urls = [];
  sheet.setAttribute("aria-busy", "true");
  sheet.replaceChildren(el("p", { className: "muted", textContent: tx("labels_loading") }));
  let got;
  try { got = await Promise.all(ids.map((id) => one(id))); }
  catch (e) { got = ids.map((id) => ({ id, status: 0, text: "" })); }
  if (mine !== drawn) return;
  sheet.removeAttribute("aria-busy");
  const bad = got.find((g) => g.url == null && g.status !== 404);
  if (bad) {
    sheet.replaceChildren();
    err.replaceChildren(document.createTextNode(tx("labels_error", { text: bad.text || "" }) + " "),
      el("button", { className: "btn", type: "button", textContent: tx("labels_try_again"), onclick: draw }));
    err.hidden = false;
    return;
  }
  // a spool the Hub does not know is said above the labels, never in a cell of the sheet
  missing.append(...got.filter((g) => g.url == null).map((g) => el("p", { className: "labels__missing-line", textContent: tx("labels_missing", { id: g.id }) })));
  const ok = got.filter((g) => g.url != null), s = sizeOf(size);
  urls.push(...ok.map((g) => g.url));
  print.disabled = ok.length === 0;
  if (!s.per) { sheet.replaceChildren(...ok.map((g) => image(g, `labels__img labels__img--${s.value}`))); return; }
  // a sheet: each page holds `per` labels in the sheet's own places; more labels go on further pages
  const pages = [];
  for (let n = 0; n < ok.length; n += s.per) pages.push(el("div", { className: `labels__page labels__page--${s.value}` }, ...ok.slice(n, n + s.per).map((g) => image(g, "labels__img"))));
  sheet.replaceChildren(...pages);
}
const paper = document.querySelector(".labels__paper");
const actual = el("p", { className: "muted labels__actual", textContent: tx("labels_actual_size") });
function follow() {                                         // the paper the browser is told, and the line under the size for a sheet
  actual.hidden = !sizeOf(size).per;
  if (paper) paper.href = `${base}/labels/page.css?size=${encodeURIComponent(size)}`;
}
// the size: a select (five entries are too many for a segmented choice; the design, Hub page)
const pick = SW.field({ type: "select", label: tx("labels_size"), value: size,
  options: SIZES.map((s) => ({ value: s.value, label: tx("labels_size_" + s.value.replace(/-/g, "_")) })) });
pick._control.addEventListener("change", () => {
  size = pick._control.value;
  history.replaceState(null, "", `?ids=${encodeURIComponent(root.dataset.ids)}&size=${encodeURIComponent(size)}`);
  follow();
  draw();
});
$(".labels__sizes").replaceWith(el("div", { className: "labels__sizes" }, pick, actual));
follow();
draw();
})();
