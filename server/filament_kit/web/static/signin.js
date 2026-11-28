// The sign-in page's Show / Hide passphrase button. The page is rendered by the Hub with no other
// script; without this file the field stays a plain password field and the button stays hidden.
"use strict";
(() => {
  const btn = document.querySelector(".field__action"), input = btn && document.getElementById(btn.getAttribute("aria-controls"));
  if (!btn || !input) return;
  const set = (shown) => {
    input.type = shown ? "text" : "password";
    btn.setAttribute("aria-pressed", String(shown));
    btn.textContent = btn.dataset[shown ? "hide" : "show"];
    btn.setAttribute("aria-label", btn.dataset[shown ? "hideName" : "showName"]);
  };
  btn.hidden = false;
  set(false);
  btn.addEventListener("click", () => set(input.type === "password"));
  btn.closest(".field__box").addEventListener("focusout", (e) => {
    if (!btn.closest(".field__box").contains(e.relatedTarget)) set(false);
  });
  const form = btn.closest("form"), submit = form && form.querySelector("button[type=submit]");
  if (form && submit) {
    form.addEventListener("submit", (e) => {
      if (submit.disabled) { e.preventDefault(); return; }
      submit.disabled = true;
      submit.setAttribute("aria-busy", "true");
    });
    window.addEventListener("pageshow", (e) => {
      if (e.persisted) { submit.disabled = false; submit.removeAttribute("aria-busy"); }
    });
  }
})();
