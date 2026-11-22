"""Spool labels and the short address (contracts 6.1 and 6.2; the design, components and Hub page).

The Hub draws a label as SVG: the QR code of the spool's short address (`https://<name>.local[:port]/s/<number>`), the
spool's number large, brand, name, material, a box in the filament's colour with its hex value, the net weight, the lot,
the date it was added, and the nozzle and bed temperatures when the filament has them. Black on white; the colour box is
the only colour. Sizes (R85, 2026-11-22 00:50): 62x29 mm and 50x30 mm (rolls), and three sheet stocks, each laid out as its
stock (margins, pitch): a4-63x34 (24), a4-70x37 (24) and letter-66x25 (30). Every size is drawn to its real size in
millimetres. Everything of a label, its code's quiet zone included, lies inside the size's content inset. When the label
is too short for it all, the temperatures go first, then the lot, then the date, then the net weight.

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
SIZES = {"62x29": (62.0, 29.0, 26.0), "50x30": (50.0, 30.0, 21.0), "a4-63x34": (63.5, 33.9, 26.0),
         "a4-70x37": (70.0, 37.0, 26.0), "letter-66x25": (66.675, 25.4, 21.0)}
# size -> the band inside the label that stays empty, in mm (a printer's shift, or the edge it cannot print, costs nothing)
INSETS = {"62x29": 1.0, "50x30": 1.0, "a4-63x34": 2.0, "a4-70x37": 5.0, "letter-66x25": 2.0}


class Sheet(NamedTuple):
    paper: str           # the CSS name of the paper
    page: tuple          # width, height in mm
    columns: int
    rows: int
    left: float          # margin to the first label, mm
    top: float
    pitch_x: float       # from one label's left edge to the next, mm
    pitch_y: float

    @property
    def count(self) -> int:
        return self.columns * self.rows


A4 = (210.0, 297.0)
LETTER = (215.9, 279.4)
SHEETS = {"a4-63x34": Sheet("A4", A4, 3, 8, 7.25, 12.9, 66.0, 33.9), "a4-70x37": Sheet("A4", A4, 3, 8, 0.0, 0.5, 70.0, 37.0),
          "letter-66x25": Sheet("letter", LETTER, 3, 10, 4.7625, 12.7, 69.85, 25.4)}
MAX_LABELS = 100            # one request
MIN_MODULE_MM = 0.4         # the smallest dark square the drawing may have (labels_test holds it)
PAGE_CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'")
PAGE_HEADERS = {"Content-Security-Policy": PAGE_CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
                "Cache-Control": "no-store"}

# ---- texts (plain constants for now: RC9 moves them to the catalogue under these names)
TEXT_LABELS_TITLE_ONE = "Print a label"
TEXT_LABELS_TITLE_MANY = "Print labels"
TEXT_LABELS_SIZE = "Size"
TEXT_LABELS_SIZE_NAMES = {"62x29": "62 x 29 mm", "50x30": "50 x 30 mm", "a4-63x34": "A4 sheet of 24, 63.5 x 33.9 mm",
                          "a4-70x37": "A4 sheet of 24, 70 x 37 mm", "letter-66x25": "Letter sheet of 30, 2-5/8 x 1 in"}
TEXT_LABELS_ACTUAL_SIZE = "Print at actual size (100%), not fitted to the page."
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
TEXT_BAD_SIZE = "The size must be 62x29, 50x30, a4-63x34, a4-70x37 or letter-66x25."
TEXT_BAD_IDS = "Give the spool numbers as ids=<n,n,...>, whole numbers of 1 or more."
TEXT_TOO_MANY = "Ask for at most {limit} labels at a time."
TEXT_SHEET_FULL = "A sheet of this size holds {count} labels. Ask for fewer, or choose another size."
TEXT_NO_CODE = "The Hub's address is too long for a QR code."
TEXT_SPOOLMAN_DOWN = "Spoolman is not reachable."
# the command line's lines, for the registry step
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
    """The size asked for (None or empty: the default); LabelError bad_size for anything but the five."""
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


# every character XML 1.0 does not allow: controls but tab, line feed and carriage return; lone surrogates; U+FFFE, U+FFFF
_XML_INVALID = re.compile("[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")


def _xml_safe(text: str) -> str:
    return _XML_INVALID.sub("", text)


def _clean(value) -> str:
    """Text for a label: one line, no control characters and no other character an XML document may not hold."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", _xml_safe(re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", str(value)))).strip()


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
    return f"{x:.4f}".rstrip("0").rstrip(".")


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
    return (f'<text x="{_n(x)}" y="{_n(y)}" font-size="{_n(fs)}"{weight}{anchor}>'
            f'{html.escape(_xml_safe(content), quote=False)}</text>')


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
    """The inside of one label, in mm, from (0, 0) to the label's width and height. Everything lies inside the size's inset.
    The lines go down from the top of the content area; a label taller than the 62x29 one spreads them and grows the type, a
    shorter one keeps the type and leaves out the lines that do not fit: the temperatures, then the lot and the date."""
    w, h, box = SIZES[size]
    inset = INSETS[size]
    room_h = h - 2 * inset
    k = max(1.0, room_h / 27.0)

    def y(local: float) -> float:
        return inset + local * k

    x0 = inset + box + 0.5
    right = w - inset - 0.5
    room = right - x0
    out = [f'<rect width="{_n(w)}" height="{_n(h)}" fill="#fff"/>',
           _qr_path(code_modules(address), inset, inset + (room_h - box) / 2, box)]
    number = str(data["number"])
    number, nfs = _fit(number, room * 0.6, 7.4 * k, 4.0 * k, bold=True)
    out.append(_text(x0, y(8.0), nfs, number, bold=True))
    material, mfs = _fit(data["material"], room - _width(number, nfs, True) - 1.5, 3.2 * k, 2.2 * k, bold=True)
    out.append(_text(right, y(8.0), mfs, material, bold=True, end=True))
    for text, line in ((data["vendor"], 12.2), (data["name"], 15.6)):
        shown, fs = _fit(text, room, 2.8 * k, 1.8 * k)
        out.append(_text(x0, y(line), fs, shown))
    side = 3.4 * k
    taken = 0.0
    if data["color_hex"]:
        out.append(f'<rect x="{_n(x0)}" y="{_n(y(20.0) - side + 0.4 * k)}" width="{_n(side)}" height="{_n(side)}" '
                   f'fill="{data["color_hex"]}" stroke="#000" stroke-width="0.15"/>')
        out.append(_text(x0 + side + 1.0, y(20.0), 2.6 * k, data["color_hex"]))
        taken = side + 1.0 + _width("#000000", 2.6 * k) + 1.0
    net, nfs2 = _fit(TEXT_LABEL_NET.format(g=data["net_g"]) if data["net_g"] else "", room - taken, 2.6 * k, 2.0 * k)
    out.append(_text(right, y(20.0), nfs2, net, end=True))
    if (23.2 + 0.6) * k <= room_h + 1e-9:                       # the line of the date and the lot fits
        lot, lfs = _fit(TEXT_LABEL_LOT.format(lot=data["lot"]) if data["lot"] else "", room * 0.3, 2.4 * k, 1.8 * k)
        out.append(_text(right, y(23.2), lfs, lot, end=True))
        added, afs = _fit(TEXT_LABEL_ADDED.format(date=data["added"]) if data["added"] else "",
                          room - (_width(lot, lfs) + 1.5 if lot else 0), 2.4 * k, 1.6 * k)
        out.append(_text(x0, y(23.2), afs, added))
    if (26.3 + 0.6) * k <= room_h + 1e-9:                       # the line of the temperatures fits
        if data["nozzle_c"] and data["bed_c"]:
            temps = TEXT_LABEL_TEMPS.format(nozzle=data["nozzle_c"], bed=data["bed_c"])
        else:
            temps = (TEXT_LABEL_NOZZLE.format(c=data["nozzle_c"]) if data["nozzle_c"] else
                     TEXT_LABEL_BED.format(c=data["bed_c"]) if data["bed_c"] else "")
        shown, tfs = _fit(temps, room, 2.4 * k, 1.8 * k)
        out.append(_text(x0, y(26.3), tfs, shown))
    return "".join(out)


def _svg(attrs: str, inner: str, standalone: bool) -> str:
    ns = ' xmlns="http://www.w3.org/2000/svg"' if standalone else ""
    return f'<svg{ns}{attrs} font-family="Arial, Helvetica, sans-serif" fill="#000">{inner}</svg>'


def _label_attrs(size: str, data: dict | None, x=None, y=None) -> str:
    w, h, _ = SIZES[size]
    place = f' x="{_n(x)}" y="{_n(y)}"' if x is not None else ""
    named = f' role="img" aria-label="{html.escape(_xml_safe(title_of(data)))}"' if data else ""
    return f'{place} class="label" viewBox="0 0 {_n(w)} {_n(h)}" width="{_n(w)}mm" height="{_n(h)}mm"{named}'


def one_svg(data: dict, size: str, address: str, standalone: bool = True) -> str:
    """One label as an SVG document (`standalone`) or as the markup the print page inlines."""
    doc = _svg(_label_attrs(size, data), _body(data, size, address), standalone)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + doc + "\n" if standalone else doc


def file_svg(items: list[dict], size: str, home: str) -> str:
    """One SVG document holding a label for each item, in the order given (`spoolwarden spool label`); `home` is the Hub's
    home address, each code adds its own spool's number. A roll size stacks them top to bottom; a sheet size is one page of
    the paper, the labels placed where the stock has them (margins and pitch), filled from the top left, at most one sheet."""
    w, h, _ = SIZES[size]
    cells = []
    sheet = SHEETS.get(size)
    if sheet:
        if len(items) > sheet.count:
            raise LabelError("too_many", TEXT_SHEET_FULL.format(count=sheet.count))
        for i, d in enumerate(items):
            cells.append(_svg(_label_attrs(size, d, sheet.left + (i % sheet.columns) * sheet.pitch_x,
                                           sheet.top + (i // sheet.columns) * sheet.pitch_y),
                              _body(d, size, f"{home}/s/{d['number']}"), False))
        total_w, total_h = sheet.page
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
    label's size on a roll, the stock's paper for a sheet size, and its labels where the stock has them)."""
    w, h, _ = SIZES[size]
    sheet = SHEETS.get(size)
    page = f"@page {{ size: {sheet.paper}; margin: 0; }}" if sheet else f"@page {{ size: {_n(w)}mm {_n(h)}mm; margin: 0; }}"
    cut = "" if sheet else "break-after: page; "
    grid = ""
    if sheet:
        grid = (f".sheet {{ box-sizing: border-box; width: {_n(sheet.page[0])}mm; margin: 1rem auto; background: #fff; "
                f"outline: 1px solid #999; padding: {_n(sheet.top)}mm 0 0 {_n(sheet.left)}mm; display: grid; "
                f"grid-template-columns: repeat({sheet.columns}, {_n(w)}mm); grid-auto-rows: {_n(h)}mm; "
                f"column-gap: {_n(sheet.pitch_x - w)}mm; row-gap: {_n(sheet.pitch_y - h)}mm; justify-content: start; "
                f"align-content: start; }}\n")
    return f"""body {{ margin: 0; background: #fff; color: #000; font: 16px/1.5 system-ui, sans-serif; }}
.controls {{ max-width: 60rem; margin: 0 auto; padding: 1rem; }}
h1 {{ font-size: 1.4rem; margin: 0 0 1rem; }}
.sizes {{ display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: center; margin-bottom: 1rem; }}
.sizes a {{ border: 1px solid #555; border-radius: 0.5rem; padding: 0.4rem 0.9rem; color: #000; text-decoration: none; }}
.sizes a[aria-current="true"] {{ background: #000; color: #fff; }}
.labels {{ padding: 1rem; display: flex; flex-wrap: wrap; gap: 4mm; justify-content: center; }}
{grid}.label {{ background: #fff; outline: 1px solid #999; display: block; }}
.actual-size {{ margin: 0 0 1rem; font-weight: 600; }}
.missing {{ border: 1px dashed #555; padding: 0.5rem 1rem; }}
.address {{ max-width: 60rem; margin: 0 auto; padding: 0 1rem; }}
button {{ font: inherit; padding: 0.5rem 1.5rem; border: 0; border-radius: 0.5rem; background: #000; color: #fff; }}
{page}
@media print {{
  .controls, .address, .missing {{ display: none; }}
  .labels {{ display: block; padding: 0; }}
  .label {{ outline: 0; {cut}}}
  .sheet {{ margin: 0; outline: 0; }}
  .sheet + .sheet {{ break-before: page; }}
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
    actual = f'<p class="actual-size">{_e(TEXT_LABELS_ACTUAL_SIZE)}</p>' if size in SHEETS else ""
    if error:
        body = (f'<p class="error" role="alert">{_e(TEXT_LABELS_ERROR.format(reason=error))} '
                f'<a href="labels?ids={ask}&amp;size={size}">{_e(TEXT_LABELS_TRY_AGAIN)}</a></p>')
    else:
        drawn = [one_svg(i, size, f"{address}/s/{i['number']}", standalone=False) if isinstance(i, dict) else
                 f'<p class="missing">{_e(TEXT_LABELS_NOT_FOUND.format(number=i))}</p>' for i in items]
        if size in SHEETS:
            count = SHEETS[size].count
            body = "".join(f'<div class="sheet">{"".join(drawn[n:n + count])}</div>' for n in range(0, len(drawn), count))
        else:
            body = f'<div class="labels">{"".join(drawn)}</div>'
    return (f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>{_e(title)}</title>'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<link rel="stylesheet" href="labels/print.css?size={size}"></head><body>'
            f'<main><div class="controls"><h1>{_e(title)}</h1>'
            f'<div class="sizes" role="group" aria-label="{_e(TEXT_LABELS_SIZE)}"><span>{_e(TEXT_LABELS_SIZE)}</span>{sizes}</div>{actual}</div>'
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
