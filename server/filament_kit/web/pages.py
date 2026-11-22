"""The web app's two standalone pages (design 04 section 11): the refusal page served outside the home network (or at
an address the Hub refuses) and the head shared with the sign-in page. Both use the web app's stylesheet and the
appearance script only, carry no navigation and no Hub data, and take their words from the string catalogue.

The files these pages link (PUBLIC_SHARED, PUBLIC_OWN) hold no data: they are served to any address, so a refused phone still
gets a styled page."""
from __future__ import annotations

import html
import json
import re

from .. import lan, shared_files


# <prefix>/<file> as the pages link them; the shared ones live under <prefix>/shared/
PUBLIC_SHARED = ("theme-init.js", "tokens.css", "components.css")
PUBLIC_OWN = ("app.css",)


def catalogue(files: dict[str, bytes]) -> dict[str, str]:
    """The string catalogue from the served strings.js (generated from strings/en.json); {} when it is missing."""
    text = files.get("strings.js", b"").decode("utf-8")
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        return {}
    body = re.sub(r",\s*}$", "}", text[start:end + 1])
    try:
        return json.loads(body)
    except ValueError:
        return {}


def text(cat: dict[str, str], key: str, **values: str) -> str:
    """The catalogue text of `key` with {product}, {command} and the given values filled in (plain text)."""
    values = {"product": cat.get("app_name", ""), "command": cat.get("command_name", ""), **values}
    return re.sub(r"\{(\w+)\}", lambda m: values.get(m.group(1), m.group(0)), cat.get(key, key))


def head(prefix: str, title: str) -> str:
    """<head> of a standalone page: the appearance script first, then tokens, components and the web app's own sheet.
    `prefix` is the web app's address as the browser sees it (absolute, or relative to the page)."""
    p = html.escape(prefix, quote=True)
    return ("<!doctype html><html lang=en><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1,viewport-fit=cover'>"
            "<meta name=color-scheme content='light dark'>"
            f"<title>{html.escape(title)}</title><script src='{p}/shared/theme-init.js'></script>"
            f"<link rel=stylesheet href='{p}/shared/tokens.css'><link rel=stylesheet href='{p}/shared/components.css'>"
            f"<link rel=stylesheet href='{p}/app.css'>")


def brand(files: dict[str, bytes], cat: dict[str, str]) -> str:
    """The sprite (hidden) and the mark with the product name, as the top of a standalone page's body."""
    return (f"{shared_files.sprite_inline(files)}<p class=standalone__brand>"
            "<svg class='icon icon--l' aria-hidden=true focusable=false><use href='#i-spoolwarden'/></svg>"
            f"<span>{html.escape(cat.get('app_name', ''))}</span></p>")


def strings_scripts(prefix: str, strings_src: str | None) -> str:
    """The catalogue's scripts: English always first (it is inside the product), then the language in use, whose texts replace it
    when they load. A language script that does not load (the Hub down, the phone offline) leaves the page in English, never
    with bare keys (R85 3.5; 04 10.2)."""
    p = html.escape(prefix, quote=True)
    return f"<script src='{p}/shared/strings.js'></script>" + (
        f"<script src='{html.escape(strings_src, quote=True)}'></script>" if strings_src else "")


def labels_body(prefix: str, ids: str, size: str, strings_src: str | None, files: dict[str, bytes], *, address: str = "",
                demo: bool = False) -> str:
    """The print page's body (R85 5.3): its own script (`labels.js`) draws the size choice and the labels from the Hub's
    `spools/<id>/label.svg`; the print rules in app.css hide everything but the labels, and `labels/page.css` states the paper.
    `address` is the Hub's home address the codes hold; `demo` shows the demo banner. `ids` and `size` are checked by the
    caller."""
    p = html.escape(prefix, quote=True)
    return (f"<body><link rel=stylesheet class=labels__paper href='{p}/labels/page.css?size={html.escape(size, quote=True)}'>"
            f"<main class=labels data-ids='{html.escape(ids, quote=True)}' data-size='{html.escape(size, quote=True)}'"
            + (f" data-address='{html.escape(address, quote=True)}'" if address else "") + (" data-demo='1'" if demo else "") + ">"
            "<header class=labels__chrome><h1 class=labels__title></h1>"
            "<div class=labels__sizes></div><div class=labels__missing></div></header>"
            "<div class=labels__sheet></div>"
            "<footer class=labels__chrome><p class=labels__note></p><p class=labels__error role=alert hidden></p>"
            "<button class='btn btn--primary labels__print' type=button></button></footer></main>"
            + strings_scripts(prefix, strings_src) + f"<script src='{p}/shared/components.js'></script>"
            f"<script src='{p}/labels.js'></script></body>")


# the paper of each label size (R85 00:50 point 5; 02 7.13): a roll prints each label on a page of its own size, a sheet on its paper
LABEL_PAPER = {"62x29": "62mm 29mm", "50x30": "50mm 30mm", "a4-63x34": "A4", "a4-70x37": "A4", "letter-66x25": "letter"}


def labels_page_css(size: str) -> str:
    """The print page's paper, stated for the browser's print dialog, margin 0 (the sheet's own margins place the labels)."""
    return f"@page {{ size: {LABEL_PAPER[size]}; margin: 0; }}\n"


def refusal(files: dict[str, bytes], prefix: str, client: str, *, reason: str | None = None) -> str:
    """The refusal page (04 11.1). Without `reason`: outside the home network, with the IPv6 hint when the request came
    over IPv6. With `reason` (the Hub's refusal of the address the page was opened at): "Not at this address"."""
    cat = catalogue(files)
    if reason is None:
        heading, paras = text(cat, "shell_refusal_title"), [text(cat, "shell_refusal_text")]
        if lan.over_ipv6(client):
            paras.append(lan.IPV6_HINT)
    else:
        heading, paras = text(cat, "shell_refusal_title_address"), [reason]
    body = "".join(f"<p>{html.escape(t)}</p>" for t in paras)
    return (head(prefix, cat.get("app_name", "")) + "<body><main class=standalone>" + brand(files, cat)
            + f"<h1>{html.escape(heading)}</h1>{body}"
            f"<p><a class='btn btn--primary' href=''>{html.escape(text(cat, 'common_try_again'))}</a></p>"
            "</main></body></html>")
