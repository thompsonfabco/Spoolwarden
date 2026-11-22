"""Spool labels on the Hub page (R85 section 5 and 6.2): one spool's label as SVG, and the print page. Nothing here
changes anything, so no code is asked for. The drawing and the page are `labels.py`; this file gives the Hub page's routes.

    GET  spools/{id}/label.svg {size}      image/svg+xml: the label (size 62x29 | 50x30 | a4-63x34 | a4-70x37 |
                                       letter-66x25, default 62x29). 404 for an
                                       unknown number. No code
    GET  labels {ids=<n,n,...>, size}      the print page: HTML with the labels as inline SVG, the size choice, the line about
                                       the printed address and Print. A number the Hub does not know is named in its place.
                                       No code
    GET  labels/print.css {size}           the page's styles (its CSP allows only its own files)
    GET  labels/print.js                   the page's one line of script (Print opens the browser's print dialog)

A refusal is the page's own shape (`{error: <sentence>}`, as every route of this page); the kit routes of labels.py answer
`{error: <code>, message: <sentence>}`. The registry step mounts `routes(page)` beside the other tabs.
"""
from __future__ import annotations

import anyio
from starlette.responses import Response

from .. import config, labels
from ..spoolman import Spoolman


def _spoolman() -> Spoolman:
    return Spoolman(config.load()["spoolman_url"])


def routes(page) -> list[tuple]:
    from ..setup_page import Refused

    def refuse(e: labels.LabelError) -> Refused:
        return Refused(e.status, e.message, code=e.code)

    async def get_label(request, body):
        try:
            size = labels.parse_size(request.query_params.get("size"))
            doc = await anyio.to_thread.run_sync(labels.label_svg_for, _spoolman(), request.path_params["id"], size,
                                                 config.load())
        except labels.LabelError as e:
            raise refuse(e) from None
        return Response(doc, media_type="image/svg+xml")

    async def get_page(request, body):
        try:
            size = labels.parse_size(request.query_params.get("size"))
            ids = labels.parse_ids(request.query_params.get("ids"))
        except labels.LabelError as e:
            raise refuse(e) from None
        doc, status = await anyio.to_thread.run_sync(labels.print_page_for, _spoolman(), ids, size, config.load())
        return Response(doc, status, media_type="text/html", headers=labels.PAGE_HEADERS)

    async def get_css(request, body):
        try:
            size = labels.parse_size(request.query_params.get("size"))
        except labels.LabelError as e:
            raise refuse(e) from None
        return Response(labels.print_css(size), media_type="text/css", headers=labels.PAGE_HEADERS)

    async def get_js(request, body):
        return Response(labels.PRINT_JS, media_type="text/javascript", headers=labels.PAGE_HEADERS)

    return [("/spools/{id:int}/label.svg", page.guard(get_label), "GET"),
            ("/labels", page.guard(get_page), "GET"),
            ("/labels/print.css", page.guard(get_css), "GET"),
            ("/labels/print.js", page.guard(get_js), "GET")]
