// The web app's print page (R85 5.3; 04 5.4, 03 16.5): the size choice, the labels the Hub draws, the one line about the printed
// address and Print, which opens the browser's own print dialog. The page draws no label of its own: each one is the Hub's
// `spools/<id>/label.svg`, shown as an image (it runs nothing) and printed as it is.
(() => {
"use strict";
const root = document.querySelector(".labels");
if (!root || !window.SW) return;
const base = location.pathname.replace(/\/labels\/?$/, "");
const ids = root.dataset.ids.split(",").map(Number);
let size = root.dataset.size;
const $ = (s) => root.querySelector(s);

// Texts of this page that the string catalogue does not hold yet (the keys file lists them); the catalogue's own text wins.
const STUB = {
  labels_title_one: "Print a label", labels_title_many: "Print labels", labels_size: "Size",
  labels_size_62x29: "62 x 29 mm", labels_size_50x30: "50 x 30 mm", labels_size_sheet24: "Sheet of 24",
  labels_address_note: "The code holds this Hub's address, {address}. If the Hub is renamed or moves to another port, old labels still open in the Spoolwarden app's scanner but not from a phone's camera.",
  labels_print: "Print", labels_error: "Could not draw the labels. {text}", labels_missing: "Spool {id} was not found.",
  labels_try_again: "Try again", labels_loading: "Drawing the labels…",
};
function tx(key, vars) {
  const v = SW.t(key, vars);
  if (v !== key) return v;
  const f = STUB[key];
  return f == null ? key : vars ? f.replace(/\{([a-z][a-z0-9_]*)\}/g, (m, n) => (vars[n] != null ? String(vars[n]) : m)) : f;
}

const title = tx(ids.length === 1 ? "labels_title_one" : "labels_title_many");
$(".labels__title").textContent = title;
document.title = title;
$(".labels__note").textContent = tx("labels_address_note", { address: location.origin });
const print = $(".labels__print");
print.textContent = tx("labels_print");
print.addEventListener("click", () => window.print());

let drawn = 0, urls = [];
async function one(id, mine) {
  const r = await fetch(`${base}/api/spools/${id}/label.svg?size=${encodeURIComponent(size)}`, { credentials: "same-origin" });
  if (!r.ok) {
    let text = "";
    try { const j = await r.json(); text = j.message || j.error || ""; } catch (e) { /* not JSON */ }
    return { id, status: r.status, text };
  }
  return { id, url: URL.createObjectURL(await r.blob()) };
}
async function draw() {
  const mine = ++drawn, sheet = $(".labels__sheet"), err = $(".labels__error");
  err.hidden = true;
  for (const u of urls) URL.revokeObjectURL(u);
  urls = [];
  sheet.setAttribute("aria-busy", "true");
  sheet.replaceChildren(Object.assign(document.createElement("p"), { className: "muted", textContent: tx("labels_loading") }));
  let got;
  try { got = await Promise.all(ids.map((id) => one(id))); }
  catch (e) { got = ids.map((id) => ({ id, status: 0, text: "" })); }
  if (mine !== drawn) return;
  sheet.removeAttribute("aria-busy");
  const bad = got.find((g) => g.url == null && g.status !== 404);
  if (bad) {
    sheet.replaceChildren();
    err.replaceChildren(document.createTextNode(tx("labels_error", { text: bad.text || "" }) + " "),
      Object.assign(document.createElement("button"), { className: "btn", type: "button", textContent: tx("labels_try_again"), onclick: draw }));
    err.hidden = false;
    return;
  }
  sheet.replaceChildren(...got.map((g) => {
    if (g.url == null) return Object.assign(document.createElement("p"), { className: "labels__missing", textContent: tx("labels_missing", { id: g.id }) });
    urls.push(g.url);
    return Object.assign(document.createElement("img"), { className: `labels__img labels__img--${size}`, src: g.url, alt: `${g.id}` });
  }));
}
$(".labels__sizes").replaceWith(SW.segmented({
  legend: tx("labels_size"), value: size, cls: "labels__sizes",
  options: ["62x29", "50x30", "sheet24"].map((value) => ({ value, label: tx("labels_size_" + value) })),
  apply: async (value) => {
    size = value;
    history.replaceState(null, "", `?ids=${encodeURIComponent(root.dataset.ids)}&size=${encodeURIComponent(value)}`);
    await draw();
  },
}));
draw();
})();
