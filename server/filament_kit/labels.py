"""Spool labels and the short address (R85 section 5; contracts 6.1 and 6.2; design 02 section 7.13, 03 section 16.5).

The Hub draws a label as SVG: the QR code of the spool's short address (`https://<name>.local[:port]/s/<number>`), the
spool's number large, brand, name, material, a box in the filament's colour with its hex value, the lot, the date it was
added, the net weight, and the nozzle and bed temperatures when the filament has them. Black on white; the colour box is
the only colour. Sizes: 62x29 mm (a 62 mm roll), 50x30 mm, and a sheet of 24 (cells of 66 x 33 mm, three across and eight
down, which fit A4 and Letter alike; the one-file form is an A4 page). Every size is drawn to its real size in millimetres.

This module holds the data functions (what a label says, where an address goes), the drawing, the print page and the
route handlers. It mounts nothing: `kit_routes()` and `labels_tab.routes()` give the routes as lists, and the registry
step (RC9) mounts them. Texts are plain constants named like the keys they will have in the catalogue (RC9 moves them).

    label_data(spool)                    the facts a label shows, from a Spoolman spool
    short_address(number, cfg)           the address in the QR code
    redirect_target(number, web_on)      where `GET /s/<number>` goes
    one_svg(data, size, address)         one label (`GET .../label.svg`)
    file_svg(items, size, address)       one SVG file with every label (`spoolwarden spool label`)
    print_page(...)                      the print page's HTML (labels as inline SVG)
"""
from __future__ import annotations

import html
import re
from typing import NamedTuple

import anyio
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import config, hubport, modules, pairing, qr
from .spoolman import Spoolman, SpoolmanError

DEFAULT_SIZE = "62x29"
# size -> (width mm, height mm, side of the square the code is drawn in, quiet zone included)
SIZES = {"62x29": (62.0, 29.0, 26.0), "50x30": (50.0, 30.0, 21.0), "sheet24": (66.0, 33.0, 28.0)}
SHEET_COLUMNS, SHEET_ROWS = 3, 8
SHEET_COUNT = SHEET_COLUMNS * SHEET_ROWS
A4 = (210.0, 297.0)
MAX_LABELS = 100            # one request
MIN_MODULE_MM = 0.4         # the smallest dark square the drawing may have (R85 assumption 6; labels_test holds it)
PAD = 1.0                   # mm round the label
PAGE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'")
PAGE_HEADERS = {"Content-Security-Policy": PAGE_CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store"}

# ---- texts (plain constants for now: RC9 moves them to the catalogue under these names)
TEXT_LABELS_TITLE_ONE = "Print a label"
TEXT_LABELS_TITLE_MANY = "Print labels"
TEXT_LABELS_SIZE = "Size"
TEXT_LABELS_SIZE_NAMES = {"62x29": "62 x 29 mm", "50x30": "50 x 30 mm", "sheet24": "Sheet of 24"}
TEXT_LABELS_PRINT = "Print"
TEXT_LABELS_ADDRESS = ("The code holds this Hub's address, {address}. If the Hub is renamed or moves to another port, old "
                       "labels still open in the Spoolwarden app's scanner but not from a phone's camera.")
TEXT_LABELS_NOT_FOUND = "Spool {number} was not found."
TEXT_LABELS_ERROR = "Could not draw the labels. {reason}"
TEXT_LABELS_TRY_AGAIN = "Try again"
TEXT_LABELS_ARIA = "Label of spool {number}: {name}, {material}, {net}"
TEXT_LABEL_ADDED = "Added {date}"
TEXT_LABEL_LOT = "Lot {lot}"
TEXT_LABEL_NOZZLE = "Nozzle {c} °C"
TEXT_LABEL_BED = "Bed {c} °C"
TEXT_LABEL_TEMPS = "Nozzle {nozzle} / Bed {bed} °C"      # both, on one line: the narrow label has no room for two captions
TEXT_LABEL_NET = "{g} g"
TEXT_BAD_SIZE = "The size must be 62x29, 50x30 or sheet24."
TEXT_BAD_IDS = "Give the spool numbers as ids=<n,n,...>, whole numbers of 1 or more."
TEXT_TOO_MANY = "Ask for at most {limit} labels at a time."
TEXT_SHEET_FULL = "A sheet holds 24 labels. Ask for fewer, or choose another size."
TEXT_NO_CODE = "The Hub's address is too long for a QR code."
TEXT_SPOOLMAN_DOWN = "Spoolman is not reachable."
# the command line's lines (06 section 9.21), for the registry step
TEXT_CLI_WROTE = "Wrote {count} {noun} ({size}) to {file}."
TEXT_CLI_NOTE = ("Note: the codes hold this Hub's address ({example}). If the Hub is renamed or moves to another port, old "
                 "labels stop opening from a phone's camera; the Spoolwarden app's scanner still reads them.")
TEXT_CLI_NEED_OUT = "Say where to write the file."
TEXT_CLI_NEED_OUT_FIX = "Add --out <file.svg>."
TEXT_CLI_NOT_FOUND_FIX = "spoolwarden spools lists the numbers."


class LabelError(Exception):
    """A refusal, in the kit's shape: `code` and `message` (`{"error": code, "message": message}`), with the HTTP status."""

    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


# ---------------------------------------------------------------- what a label says

def parse_size(raw) -> str:
    """The size asked for (None or empty: the default); LabelError bad_size for anything but the three."""
    if raw is None or raw == "":
        return DEFAULT_SIZE
    if not isinstance(raw, str) or raw not in SIZES:
        raise LabelError("bad_size", TEXT_BAD_SIZE)
    return raw


def parse_ids(raw) -> list[int]:
    """`214,213` -> [214, 213]: whole numbers of 1 or more, in the order given, at most MAX_LABELS."""
    parts = [p.strip() for p in str(raw or "").split(",") if p.strip()]
    if not parts or not all(re.fullmatch(r"[0-9]{1,9}", p) and int(p) >= 1 for p in parts):
        raise LabelError("bad_ids", TEXT_BAD_IDS)
    if len(parts) > MAX_LABELS:
        raise LabelError("too_many", TEXT_TOO_MANY.format(limit=MAX_LABELS))
    return [int(p) for p in parts]


def _clean(value) -> str:
    """Text for a label: one line, no control characters (an XML document may not hold them)."""
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value))).strip() if value is not None else ""


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and value == value else None


def _grams(value) -> str:
    n = _num(value)
    if n is None or n < 0:
        return ""
    return str(int(n)) if float(n).is_integer() else f"{n:.1f}"


def _hex(value) -> str | None:
    m = re.fullmatch(r"#?([0-9A-Fa-f]{6})(?:[0-9A-Fa-f]{2})?", str(value or "").strip())
    return "#" + m.group(1).upper() if m else None


def label_data(spool: dict) -> dict:
    """What a label shows, from a Spoolman spool: number, vendor, name, material, color_hex (`#RRGGBB` or None), lot, added
    (`YYYY-MM-DD`, a day and nothing more, R83), net_g (the filament's net weight text, empty when unknown), nozzle_c and
    bed_c (text, empty when the filament has none)."""
    f = spool.get("filament") or {}
    added = re.match(r"\d{4}-\d{2}-\d{2}", str(spool.get("registered") or ""))
    return {"number": int(spool["id"]), "vendor": _clean((f.get("vendor") or {}).get("name")), "name": _clean(f.get("name")),
            "material": _clean(f.get("material")), "color_hex": _hex(f.get("color_hex")), "lot": _clean(spool.get("lot_nr")),
            "added": added.group(0) if added else "", "net_g": _grams(f.get("weight")),
            "nozzle_c": _grams(f.get("settings_extruder_temp")) if (_num(f.get("settings_extruder_temp")) or 0) > 0 else "",
            "bed_c": _grams(f.get("settings_bed_temp")) if (_num(f.get("settings_bed_temp")) or 0) > 0 else ""}


def title_of(data: dict) -> str:
    """The words that say what is on a label (its accessible name): brand and name, material, net weight."""
    name = " ".join(x for x in (data["vendor"], data["name"]) if x) or "no name"
    return TEXT_LABELS_ARIA.format(number=data["number"], name=name, material=data["material"] or "no material",
                                   net=TEXT_LABEL_NET.format(g=data["net_g"]) if data["net_g"] else "weight not known")


# ---------------------------------------------------------------- the short address

def home_address(cfg: dict | None = None) -> str:
    """The Hub's home address, `https://<name>.local[:port]` (no port for 443)."""
    cfg = config.load() if cfg is None else cfg
    return f"https://{pairing.short_hostname()}.local{hubport.url_port(cfg.get('port', hubport.FALLBACK_PORT))}"


def short_address(number: int, cfg: dict | None = None) -> str:
    return f"{home_address(cfg)}/s/{int(number)}"


def redirect_target(number: int, web_on: bool) -> str:
    """Where the short address goes: the web app's spool sheet while the web app is on, else the Hub page's Spools."""
    return f"/intake#spools/{int(number)}" if web_on else f"/#spools/{int(number)}"


def web_app_on(cfg: dict | None = None) -> bool:
    return "web" in modules.enabled(config.load() if cfg is None else cfg)


# ---------------------------------------------------------------- the drawing

def _n(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _width(text: str, fs: float, bold: bool = False) -> float:
    """What a text takes in mm: an estimate (the average width of a sans-serif glyph), kept on the safe side."""
    return len(text) * fs * (0.62 if bold else 0.56)


def _fit(text: str, width: float, fs: float, floor: float, bold: bool = False) -> tuple[str, float]:
    """The text and the font size that make it fit `width`: the size goes down to `floor`, then the text ends in "…"."""
    if width <= 0 or not text:
        return "", fs
    while fs - floor > 1e-9 and _width(text, fs, bold) > width:
        fs = max(round(fs - 0.1, 2), floor)
    if _width(text, fs, bold) <= width:
        return text, fs
    keep = max(int(width / (fs * (0.62 if bold else 0.56))) - 1, 1)
    return text[:keep].rstrip() + "…", fs


def _text(x: float, y: float, fs: float, content: str, bold: bool = False, end: bool = False) -> str:
    if not content:
        return ""
    weight = ' font-weight="700"' if bold else ""
    anchor = ' text-anchor="end"' if end else ""
    return f'<text x="{_n(x)}" y="{_n(y)}" font-size="{_n(fs)}"{weight}{anchor}>{html.escape(content, quote=False)}</text>'


def code_modules(address: str) -> list[list[bool]]:
    rows = qr.modules(address)
    if rows is None:
        raise LabelError("no_code", TEXT_NO_CODE, 500)
    return rows


def module_mm(size: str, address: str) -> float:
    """The side of one dark square of the code, in mm, at this size (the proof of readability reads it)."""
    return SIZES[size][2] / (len(code_modules(address)) + 2 * qr.QUIET)


def _qr_path(rows: list[list[bool]], x: float, y: float, box: float) -> str:
    side = len(rows) + 2 * qr.QUIET
    scale = box / side
    runs = []
    for r, row in enumerate(rows):
        c = 0
        while c < len(row):
            if not row[c]:
                c += 1
                continue
            start = c
            while c < len(row) and row[c]:
                c += 1
            runs.append(f"M{start + qr.QUIET} {r + qr.QUIET}h{c - start}v1h-{c - start}z")
    return (f'<path class="label__qr" transform="translate({_n(x)} {_n(y)}) scale({scale:.5f})" d="{"".join(runs)}" '
            f'fill="#000" shape-rendering="crispEdges"/>')


def _body(data: dict, size: str, address: str) -> str:
    """The inside of one label, in mm, from (0, 0) to the label's width and height."""
    w, h, box = SIZES[size]
    k = h / 29.0
    x0 = PAD + box + 0.5
    right = w - 1.5
    room = right - x0
    out = [f'<rect width="{_n(w)}" height="{_n(h)}" fill="#fff"/>',
           _qr_path(code_modules(address), PAD, (h - box) / 2, box)]
    number = str(data["number"])
    number, nfs = _fit(number, room * 0.6, 7.4 * k, 4.0 * k, bold=True)
    out.append(_text(x0, 9.0 * k, nfs, number, bold=True))
    material, mfs = _fit(data["material"], room - _width(number, nfs, True) - 1.5, 3.2 * k, 2.2 * k, bold=True)
    out.append(_text(right, 9.0 * k, mfs, material, bold=True, end=True))
    for text, y in ((data["vendor"], 13.2 * k), (data["name"], 16.6 * k)):
        shown, fs = _fit(text, room, 2.8 * k, 1.8 * k)
        out.append(_text(x0, y, fs, shown))
    y = 21.0 * k
    side = 3.4 * k
    if data["color_hex"]:
        out.append(f'<rect x="{_n(x0)}" y="{_n(y - side + 0.4 * k)}" width="{_n(side)}" height="{_n(side)}" '
                   f'fill="{data["color_hex"]}" stroke="#000" stroke-width="0.15"/>')
        out.append(_text(x0 + side + 1.0, y, 2.6 * k, data["color_hex"]))
    lot, lfs = _fit(TEXT_LABEL_LOT.format(lot=data["lot"]) if data["lot"] else "",
                    room - (side + 1.0 + _width("#000000", 2.6 * k) + 1.0 if data["color_hex"] else 0), 2.6 * k, 2.0 * k)
    out.append(_text(right, y, lfs, lot, end=True))
    y = 24.5 * k
    net = TEXT_LABEL_NET.format(g=data["net_g"]) if data["net_g"] else ""
    net, nfs2 = _fit(net, room / 2, 2.6 * k, 2.0 * k)
    out.append(_text(right, y, nfs2, net, end=True))
    added, afs = _fit(TEXT_LABEL_ADDED.format(date=data["added"]) if data["added"] else "",
                      room - _width(net, nfs2) - 1.5, 2.4 * k, 1.6 * k)
    out.append(_text(x0, y, afs, added))
    if data["nozzle_c"] and data["bed_c"]:
        temps = TEXT_LABEL_TEMPS.format(nozzle=data["nozzle_c"], bed=data["bed_c"])
    else:
        temps = (TEXT_LABEL_NOZZLE.format(c=data["nozzle_c"]) if data["nozzle_c"] else
                 TEXT_LABEL_BED.format(c=data["bed_c"]) if data["bed_c"] else "")
    shown, tfs = _fit(temps, room, 2.4 * k, 1.8 * k)
    out.append(_text(x0, 27.6 * k, tfs, shown))
    return "".join(out)


def _svg(attrs: str, inner: str, standalone: bool) -> str:
    ns = ' xmlns="http://www.w3.org/2000/svg"' if standalone else ""
    return f'<svg{ns}{attrs} font-family="Arial, Helvetica, sans-serif" fill="#000">{inner}</svg>'


def _label_attrs(size: str, data: dict | None, x=None, y=None) -> str:
    w, h, _ = SIZES[size]
    place = f' x="{_n(x)}" y="{_n(y)}"' if x is not None else ""
    named = f' role="img" aria-label="{html.escape(title_of(data))}"' if data else ""
    return f'{place} class="label" viewBox="0 0 {_n(w)} {_n(h)}" width="{_n(w)}mm" height="{_n(h)}mm"{named}'


def one_svg(data: dict, size: str, address: str, standalone: bool = True) -> str:
    """One label as an SVG document (`standalone`) or as the markup the print page inlines."""
    doc = _svg(_label_attrs(size, data), _body(data, size, address), standalone)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + doc + "\n" if standalone else doc


def file_svg(items: list[dict], size: str, home: str) -> str:
    """One SVG document holding a label for each item, in the order given (`spoolwarden spool label`); `home` is the Hub's
    home address, each code adds its own spool's number. A roll size stacks them top to bottom; a sheet is an A4 page with
    the cells filled from the top left (at most 24)."""
    w, h, _ = SIZES[size]
    cells = []
    if size == "sheet24":
        if len(items) > SHEET_COUNT:
            raise LabelError("too_many", TEXT_SHEET_FULL)
        left = (A4[0] - SHEET_COLUMNS * w) / 2
        top = (A4[1] - SHEET_ROWS * h) / 2
        for i, d in enumerate(items):
            cells.append(_svg(_label_attrs(size, d, left + (i % SHEET_COLUMNS) * w, top + (i // SHEET_COLUMNS) * h),
                              _body(d, size, f"{home}/s/{d['number']}"), False))
        total_w, total_h = A4
    else:
        for i, d in enumerate(items):
            cells.append(_svg(_label_attrs(size, d, 0, i * h), _body(d, size, f"{home}/s/{d['number']}"), False))
        total_w, total_h = w, h * len(items)
    root = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {_n(total_w)} {_n(total_h)}" '
            f'width="{_n(total_w)}mm" height="{_n(total_h)}mm" fill="#fff">'
            f'<rect width="{_n(total_w)}" height="{_n(total_h)}" fill="#fff"/>{"".join(cells)}</svg>')
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + root + "\n"


# ---------------------------------------------------------------- the print page

PRINT_JS = ("document.getElementById('print').addEventListener('click', function () { window.print(); });\n")


def print_css(size: str) -> str:
    """The page's styles: the preview on screen, and the print rules (everything but the labels is hidden; the paper is the
    label's size on a roll, the browser's own paper for a sheet)."""
    w, h, _ = SIZES[size]
    sheet = size == "sheet24"
    page = "@page { size: auto; margin: 0; }" if sheet else f"@page {{ size: {_n(w)}mm {_n(h)}mm; margin: 0; }}"
    cut = "" if sheet else "break-after: page; "
    return f"""body {{ margin: 0; background: #fff; color: #000; font: 16px/1.5 system-ui, sans-serif; }}
.controls {{ max-width: 60rem; margin: 0 auto; padding: 1rem; }}
h1 {{ font-size: 1.4rem; margin: 0 0 1rem; }}
.sizes {{ display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; margin-bottom: 1rem; }}
.sizes a {{ border: 1px solid #555; border-radius: 0.5rem; padding: 0.4rem 0.9rem; color: #000; text-decoration: none; }}
.sizes a[aria-current="true"] {{ background: #000; color: #fff; }}
.labels, .sheet {{ padding: 1rem; display: flex; flex-wrap: wrap; gap: 4mm; justify-content: center; }}
.sheet {{ display: grid; grid-template-columns: repeat({SHEET_COLUMNS}, {_n(w)}mm); grid-auto-rows: {_n(h)}mm; gap: 0;
  justify-content: center; align-content: start; }}
.label {{ background: #fff; outline: 1px solid #999; display: block; }}
.missing {{ border: 1px dashed #555; padding: 0.5rem 1rem; }}
.address {{ max-width: 60rem; margin: 0 auto; padding: 0 1rem; }}
button {{ font: inherit; padding: 0.5rem 1.5rem; border: 0; border-radius: 0.5rem; background: #000; color: #fff; }}
{page}
@media print {{
  .controls, .address, .missing {{ display: none; }}
  .labels {{ display: block; padding: 0; }}
  .label {{ outline: 0; {cut}}}
  .sheet {{ padding: 6mm 0 0; break-after: page; }}
}}
"""


def _e(text: str) -> str:
    return html.escape(text, quote=True)


def print_page(items: list, size: str, ids: list[int], address: str, error: str | None = None) -> str:
    """The print page as HTML; `address` is the Hub's home address (each code adds its own spool's number). `items` holds, in the order asked, either a label's data or the number of a spool that was
    not found; `error` (the Hub's text) replaces the labels when they could not be drawn. Nothing here is inline script
    or style: the page's CSP allows only its own, so both come as files (`labels/print.css`, `labels/print.js`)."""
    ask = ",".join(str(i) for i in ids)
    title = TEXT_LABELS_TITLE_ONE if len(ids) == 1 else TEXT_LABELS_TITLE_MANY
    current = ' aria-current="true"'
    sizes = "".join(f'<a href="labels?ids={ask}&amp;size={s}"{current if s == size else ""}>'
                    f'{_e(TEXT_LABELS_SIZE_NAMES[s])}</a>' for s in SIZES)
    if error:
        body = (f'<p class="error" role="alert">{_e(TEXT_LABELS_ERROR.format(reason=error))} '
                f'<a href="labels?ids={ask}&amp;size={size}">{_e(TEXT_LABELS_TRY_AGAIN)}</a></p>')
    else:
        drawn = [one_svg(i, size, f"{address}/s/{i['number']}", standalone=False) if isinstance(i, dict) else
                 f'<p class="missing">{_e(TEXT_LABELS_NOT_FOUND.format(number=i))}</p>' for i in items]
        if size == "sheet24":
            body = "".join(f'<div class="sheet">{"".join(drawn[n:n + SHEET_COUNT])}</div>' for n in range(0, len(drawn), SHEET_COUNT))
        else:
            body = f'<div class="labels">{"".join(drawn)}</div>'
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>{_e(title)}</title>'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<link rel="stylesheet" href="labels/print.css?size={size}"></head><body>'
            f'<main><div class="controls"><h1>{_e(title)}</h1>'
            f'<div class="sizes" role="group" aria-label="{_e(TEXT_LABELS_SIZE)}"><span>{_e(TEXT_LABELS_SIZE)}</span>{sizes}</div></div>'
            f'{body}'
            f'<p class="address">{_e(TEXT_LABELS_ADDRESS.format(address=address))}</p>'
            f'<div class="controls"><button id="print" type="button">{_e(TEXT_LABELS_PRINT)}</button></div></main>'
            f'<script src="labels/print.js"></script></body></html>\n')


# ---------------------------------------------------------------- reading spools, and the route handlers

def fetch(sm: Spoolman, number: int) -> dict | None:
    """One spool from Spoolman, None when there is no such number; Spoolman being down is a LabelError."""
    try:
        return sm.get(f"spool/{int(number)}")
    except SpoolmanError as e:
        if e.status == 404:
            return None
        raise LabelError("spoolman_unreachable", TEXT_SPOOLMAN_DOWN, 502) from None


def not_found(number: int) -> LabelError:
    return LabelError("not_found", TEXT_LABELS_NOT_FOUND.format(number=number), 404)


def label_svg_for(sm: Spoolman, number: int, size: str, cfg: dict | None = None) -> str:
    spool = fetch(sm, number)
    if spool is None:
        raise not_found(number)
    return one_svg(label_data(spool), size, short_address(number, cfg))


def print_page_for(sm: Spoolman, ids: list[int], size: str, cfg: dict | None = None) -> tuple[str, int]:
    """The print page and its status: 200 even when a number is unknown (its place says so), 502 when Spoolman is down."""
    address = home_address(cfg)
    items: list = []
    try:
        for n in ids:
            spool = fetch(sm, n)
            items.append(label_data(spool) if spool is not None else n)
    except LabelError as e:
        return print_page([], size, ids, address, error=e.message), e.status
    return print_page(items, size, ids, address), 200


def error_response(e: LabelError) -> JSONResponse:
    return JSONResponse({"error": e.code, "message": e.message}, e.status)


class KitRoute(NamedTuple):
    method: str
    path: str
    handler: object     # async (Request) -> Response; answers its own refusals in the kit's shape
    auth: str           # "token": the registry wraps it like any kit route (paired device or web token); "open": no token


def kit_routes(sm_factory, cfg_factory=config.load) -> list[KitRoute]:
    """The kit routes of 6.1 that RC8 owns, for the registry step to mount. `sm_factory()` gives the Spoolman client."""

    async def label(request: Request) -> Response:
        try:
            size = parse_size(request.query_params.get("size"))
            doc = await anyio.to_thread.run_sync(label_svg_for, sm_factory(), request.path_params["id"], size, cfg_factory())
        except LabelError as e:
            return error_response(e)
        return Response(doc, media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    async def short(request: Request) -> Response:
        number = request.path_params["id"]
        try:
            spool = await anyio.to_thread.run_sync(fetch, sm_factory(), number)
            if spool is None:
                raise not_found(number)
        except LabelError as e:
            return error_response(e)
        return RedirectResponse(redirect_target(number, web_app_on(cfg_factory())), 302,
                                headers={"Cache-Control": "no-store"})

    return [KitRoute("GET", "/api/spools/{id:int}/label.svg", label, "token"), KitRoute("GET", "/s/{id:int}", short, "open")]
