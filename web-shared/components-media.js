// Spoolwarden shared components, the media addition (the design, components 1.5, 7.8): loaded after components.js, only by the
// pages that show photos or a pairing code. It adds window.SW_MEDIA.
(() => {
"use strict";
// the QR block's image (7.8): the Hub's SVG through a Blob URL
const qrImage = (svgText, alt) => {
  const url = URL.createObjectURL(new Blob([svgText], { type: "image/svg+xml" }));
  const img = window.SW.h("img", { class: "qr__image", alt, width: 224, height: 224, src: url });
  const revoke = () => URL.revokeObjectURL(url);
  img.addEventListener("load", revoke, { once: true });
  img.addEventListener("error", revoke, { once: true });
  return img;
};

window.SW_MEDIA = Object.freeze({ qrImage });
})();
