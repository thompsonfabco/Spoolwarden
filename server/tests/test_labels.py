"""Spool labels and the short address (RC8, R85 section 5 and contracts 6.1 and 6.2): what a label says, how it is drawn,
that its QR code reads back as the spool's address at every size, the print page, and the routes' answers."""
import re
import xml.etree.ElementTree as ET

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient
from test_qr import decode  # the reader of a QR symbol, written from ISO 18004 and not from the encoder

from filament_kit import config, labels, pairing, qr
from filament_kit.setup_console import labels_tab
from filament_kit.spoolman import SpoolmanError

SIZES5 = ["62x29", "50x30", "a4-63x34", "a4-70x37", "letter-66x25"]
NS = "{http://www.w3.org/2000/svg}"
HOME = "https://spoolwarden.local:8710"
ADDRESS = HOME + "/s/214"


def spool(number=214, **over):
    s = {"id": number, "registered": "2026-11-12T14:05:03Z", "lot_nr": "F57",
         "filament": {"name": "PolyTerra PLA Charcoal Black", "material": "PLA", "color_hex": "2b2b2e", "weight": 1000,
                      "settings_extruder_temp": 210, "settings_bed_temp": 60, "vendor": {"name": "Polymaker"}}}
    s["filament"].update(over.pop("filament", {}))
    s.update(over)
    return s


class FakeSm:
    def __init__(self, *spools, down=False):
        self.spools, self.down = {s["id"]: s for s in spools}, down

    def get(self, path):
        if self.down:
            raise SpoolmanError("spoolman_unreachable", None)
        number = int(path.rsplit("/", 1)[1])
        if number not in self.spools:
            raise SpoolmanError(f"spoolman GET {path} -> 404", 404)
        return self.spools[number]


@pytest.fixture(autouse=True)
def hub(monkeypatch):
    monkeypatch.setattr(pairing, "short_hostname", lambda: "spoolwarden")
    monkeypatch.setattr(config, "load", lambda: {"port": 8710, "spoolman_url": "http://127.0.0.1:7912"})


def parse(doc: str):
    return ET.fromstring(doc)


def code_of(root):
    """The QR code of a drawn label as rows of booleans (no quiet zone): the modules of its path, read back."""
    path = next(p for p in root.iter(NS + "path") if p.get("class") == "label__qr")
    scale = float(re.search(r"scale\(([\d.]+)\)", path.get("transform")).group(1))
    runs = re.findall(r"M(\d+) (\d+)h(\d+)v1h-\d+z", path.get("d"))
    top = min(int(y) for _, y, _ in runs)
    side = max(int(x) + int(n) for x, _, n in runs) + qr.QUIET          # the code is symmetric: its quiet zone ends it
    n = side - 2 * qr.QUIET
    grid = [[False] * n for _ in range(n)]
    assert top == qr.QUIET
    for x, y, length in runs:
        for k in range(int(length)):
            grid[int(y) - qr.QUIET][int(x) - qr.QUIET + k] = True
    return grid, scale


# ---- what a label says

def test_label_data_reads_the_spool():
    d = labels.label_data(spool())
    assert d == {"number": 214, "vendor": "Polymaker", "name": "PolyTerra PLA Charcoal Black", "material": "PLA",
                 "color_hex": "#2B2B2E", "lot": "F57", "added": "2026-11-12", "net_g": "1000", "nozzle_c": "210", "bed_c": "60"}


def test_label_data_leaves_out_what_the_filament_does_not_have():
    d = labels.label_data(spool(registered=None, lot_nr=None, filament={"color_hex": "nonsense", "weight": None,
                                                                          "settings_extruder_temp": None, "settings_bed_temp": 0}))
    assert (d["color_hex"], d["lot"], d["added"], d["net_g"], d["nozzle_c"], d["bed_c"]) == (None, "", "", "", "", "")
    assert labels.label_data(spool(filament={"weight": 750.5}))["net_g"] == "750.5"
    assert labels.label_data(spool(filament={"color_hex": "#ff8800ff"}))["color_hex"] == "#FF8800"


def test_sizes_and_ids_are_checked():
    assert [labels.parse_size(x) for x in (None, "") + tuple(SIZES5)] == ["62x29"] * 2 + SIZES5
    assert list(labels.SIZES) == SIZES5 and labels.DEFAULT_SIZE == "62x29"
    for bad in ("A4", "62X29", ["62x29"], "sheet24"):
        with pytest.raises(labels.LabelError) as e:
            labels.parse_size(bad)
        assert (e.value.code, e.value.status, e.value.message) == ("bad_size", 400, "The size must be 62x29, 50x30, a4-63x34, a4-70x37 or letter-66x25.")
    assert labels.parse_ids("214, 213,7") == [214, 213, 7]
    for bad in (None, "", " , ", "0", "-3", "1,x", "1.5", "1,,2x"):
        with pytest.raises(labels.LabelError) as e:
            labels.parse_ids(bad)
        assert e.value.code == "bad_ids"
    with pytest.raises(labels.LabelError) as e:
        labels.parse_ids(",".join(["1"] * (labels.MAX_LABELS + 1)))
    assert e.value.code == "too_many"


# ---- the short address

def test_the_short_address_names_the_hub_and_leaves_out_port_443():
    assert labels.short_address(214, {"port": 8710}) == "https://spoolwarden.local:8710/s/214"
    assert labels.short_address(7, {"port": 443}) == "https://spoolwarden.local/s/7"
    assert labels.home_address({"port": 443}) == "https://spoolwarden.local"


def test_the_redirect_goes_to_the_web_app_when_it_is_on_else_to_the_hub_page():
    assert labels.redirect_target(214, True) == "/intake#spools/214"
    assert labels.redirect_target(214, False) == "/#spools/214"
    assert labels.web_app_on({"modules": {"web": True}}) and not labels.web_app_on({"modules": {"web": False}})


# ---- the drawing

@pytest.mark.parametrize("size", [s for s in SIZES5 if s != "letter-66x25"])
def test_a_label_is_drawn_to_its_real_size_with_everything_on_it(size):
    w, h, _ = labels.SIZES[size]
    root = parse(labels.one_svg(labels.label_data(spool(filament={"name": "PolyTerra PLA Black"})), size, ADDRESS))
    assert root.tag == NS + "svg" and root.get("viewBox") == f"0 0 {w:g} {h:g}"
    assert (root.get("width"), root.get("height")) == (f"{w:g}mm", f"{h:g}mm")
    assert root.get("role") == "img"
    assert root.get("aria-label") == "Label of spool 214: Polymaker PolyTerra PLA Black, PLA, 1000 g"
    said = [t.text for t in root.iter(NS + "text")]
    assert said[0] == "214"
    for want in ("PLA", "Polymaker", "PolyTerra PLA Black", "#2B2B2E", "Lot F57", "Added 2026-11-12", "1000 g",
                 "Nozzle 210 / Bed 60 °C"):
        assert want in said, (size, want, said)
    # black on white: the colour box is the only colour
    fills = {e.get("fill") for e in root.iter() if e.get("fill")}
    assert fills == {"#fff", "#000", "#2B2B2E"}


def test_the_designs_example_name_fits_whole_on_the_roll_label():
    said = [t.text for t in parse(labels.one_svg(labels.label_data(spool()), "62x29", ADDRESS)).iter(NS + "text")]
    assert "PolyTerra PLA Charcoal Black" in said and not any(x.endswith("…") for x in said)


def test_temperatures_are_left_out_with_no_empty_caption():
    said = [t.text for t in parse(labels.one_svg(labels.label_data(spool(filament={"settings_extruder_temp": None,
                                                                                    "settings_bed_temp": None})),
                                                 "62x29", ADDRESS)).iter(NS + "text")]
    assert not any("Nozzle" in x or "Bed" in x for x in said)
    only_bed = [t.text for t in parse(labels.one_svg(labels.label_data(spool(filament={"settings_extruder_temp": None})),
                                                     "62x29", ADDRESS)).iter(NS + "text")]
    assert "Bed 60 °C" in only_bed


def test_a_spool_with_little_known_still_draws_without_empty_words():
    d = labels.label_data({"id": 5, "filament": {}})
    root = parse(labels.one_svg(d, "50x30", labels.short_address(5, {"port": 8710})))
    said = [t.text for t in root.iter(NS + "text")]
    assert said == ["5"] and "None" not in labels.one_svg(d, "50x30", ADDRESS)
    assert root.get("aria-label") == "Label of spool 5: no name, no material, weight not known"


def text_box(t):
    """Where a drawn text lies, in mm: left, right, top, bottom (width estimated, cap height 0.75 and descent 0.25 of the size)."""
    x, y, fs, bold = float(t.get("x")), float(t.get("y")), float(t.get("font-size")), t.get("font-weight") == "700"
    width = labels._width(t.text, fs, bold)
    left, right = (x - width, x) if t.get("text-anchor") == "end" else (x, x + width)
    return left, right, y - 0.75 * fs, y + 0.25 * fs


@pytest.mark.parametrize("size", SIZES5)
def test_nothing_of_a_label_runs_over_the_code_or_outside_its_inset(size):
    w, h, box = labels.SIZES[size]
    inset = labels.INSETS[size]
    long = "Extra Long Brand Name " * 3
    d = labels.label_data(spool(lot_nr="LOT-WITH-A-LONG-NUMBER-0001", filament={"vendor": {"name": long}, "name": long * 2,
                                                                                  "material": "PLA-CF-HIGH-SPEED"}))
    root = parse(labels.one_svg(d, size, ADDRESS))
    texts = list(root.iter(NS + "text"))
    assert any(t.text.endswith("…") for t in texts)                            # a name too long for its line ends in "…"
    for t in texts:
        left, right, top, bottom = text_box(t)
        assert inset + box <= left and right <= w - inset, (size, t.text, left, right)
        assert inset <= top and bottom <= h - inset + 1e-9, (size, t.text, top, bottom)
    for r in root.iter(NS + "rect"):                                            # the colour box
        if r.get("x"):
            x, y, side = float(r.get("x")), float(r.get("y")), float(r.get("width"))
            assert inset + box <= x and x + side <= w - inset and inset <= y and y + side <= h - inset + 1e-9, size


@pytest.mark.parametrize("size", SIZES5)
def test_the_code_with_its_quiet_zone_lies_inside_the_inset(size):
    w, h, box = labels.SIZES[size]
    inset = labels.INSETS[size]
    path = next(p for p in parse(labels.one_svg(labels.label_data(spool()), size, ADDRESS)).iter(NS + "path"))
    x, y = (float(v) for v in re.search(r"translate\(([\d.]+) ([\d.]+)\)", path.get("transform")).groups())
    assert x >= inset - 1e-9 and y >= inset - 1e-9 and x + box <= w - inset + 1e-9 and y + box <= h - inset + 1e-9


def texts_of(size, **over):
    return [t.text for t in parse(labels.one_svg(labels.label_data(spool(**over)), size, ADDRESS)).iter(NS + "text")]


def test_the_short_label_leaves_out_temperatures_then_lot_then_date_and_keeps_what_identifies_the_spool():
    said = texts_of("letter-66x25")
    assert said[0] == "214"
    for keep in ("Polymaker", "PolyTerra PLA Charcoal Black", "PLA", "#2B2B2E", "1000 g"):
        assert keep in said, said
    assert not any(x.startswith(("Nozzle", "Bed", "Lot", "Added")) for x in said)
    # a label with room for everything shows the lot, the date and the temperatures
    assert {"Lot F57", "Added 2026-11-12", "Nozzle 210 / Bed 60 °C"} <= set(texts_of("a4-70x37"))
    # the order: of the four things, the temperatures are on the lowest line, then the lot and the date, then the weight
    ys = {t.text: float(t.get("y")) for t in parse(labels.one_svg(labels.label_data(spool()), "62x29", ADDRESS)).iter(NS + "text")}
    assert ys["Nozzle 210 / Bed 60 °C"] > ys["Lot F57"] and ys["Nozzle 210 / Bed 60 °C"] > ys["Added 2026-11-12"]
    assert ys["Lot F57"] > ys["1000 g"] and ys["Added 2026-11-12"] > ys["1000 g"]


def test_a_label_text_loses_every_character_xml_does_not_allow():
    bad = "A\x00\x08\ufffe\uffff\ud800Z\U0001f600"
    d = labels.label_data(spool(lot_nr="L" + bad, filament={"name": "N" + bad, "vendor": {"name": "V" + bad}}))
    assert d["name"] == "NA Z\U0001f600" and d["vendor"] == "VA Z\U0001f600" and d["lot"] == "LA Z\U0001f600"
    doc = labels.one_svg(d, "62x29", ADDRESS)
    assert parse(doc).get("aria-label").startswith("Label of spool 214: VA Z")
    for gone in ("\x00", "\x08", "\ufffe", "\uffff", "\ud800"):
        assert gone not in doc
    # data that did not come through label_data is cleaned where it is drawn
    raw = dict(d, name="x\ufffey\ud800z", vendor="v\uffff")
    drawn = [t.text for t in parse(labels.one_svg(raw, "62x29", ADDRESS)).iter(NS + "text")]
    assert "xyz" in drawn and "v" in drawn


def test_text_is_escaped_and_control_characters_are_dropped():
    d = labels.label_data(spool(filament={"name": 'A<b>&"C\x00\x1fD', "vendor": {"name": "X & Y"}}))
    doc = labels.one_svg(d, "62x29", ADDRESS)
    said = [t.text for t in parse(doc).iter(NS + "text")]
    assert 'A<b>&"C D' in said and "X & Y" in said


@pytest.mark.parametrize("size", list(labels.SIZES))
def test_the_code_reads_back_as_the_spools_address_at_every_size(size):
    """R85 assumption 6, the part a test can hold: the drawn label's own path decodes (with a reader written from the
    standard) to the spool's address, the code sits inside the label with its quiet zone, and one dark square is not
    smaller than MIN_MODULE_MM. That a phone camera reads it is inferred from the square's size, not tested."""
    w, h, box = labels.SIZES[size]
    root = parse(labels.one_svg(labels.label_data(spool()), size, ADDRESS))
    grid, scale = code_of(root)
    assert decode(grid) == ADDRESS
    assert scale == pytest.approx(labels.module_mm(size, ADDRESS), rel=1e-3) and scale >= labels.MIN_MODULE_MM
    side = (len(grid) + 2 * qr.QUIET) * scale                                   # the code with its quiet zone, in mm
    inset = labels.INSETS[size]
    assert side == pytest.approx(box, rel=1e-3) and inset + side <= w - inset + 1e-9 and side + 2 * inset <= h + 1e-9


@pytest.mark.parametrize("size", list(labels.SIZES))
def test_the_longest_address_still_has_squares_a_camera_reads(size):
    worst = "https://" + "h" * 63 + ".local:65535/s/9999999"                 # a 63 character name, the largest port and number
    assert labels.module_mm(size, worst) >= labels.MIN_MODULE_MM
    grid, _ = code_of(parse(labels.one_svg(labels.label_data(spool(9999999)), size, worst)))
    assert decode(grid) == worst


def test_the_reader_used_here_is_not_a_rubber_stamp():
    grid, _ = code_of(parse(labels.one_svg(labels.label_data(spool()), "62x29", ADDRESS)))
    assert decode(grid) != ADDRESS + "x" and decode(grid) != HOME + "/s/215"


def test_modules_are_the_code_without_a_quiet_zone():
    rows = qr.modules(ADDRESS)
    assert len(rows) == len(rows[0]) == 17 + 4 * ((len(rows) - 17) // 4) and decode(rows) == ADDRESS
    assert qr.modules("x" * 5000) is None


# ---- one file with several labels

def test_one_file_holds_a_label_for_each_number_in_the_order_given():
    items = [labels.label_data(spool(n)) for n in (214, 7, 99)]
    root = parse(labels.file_svg(items, "62x29", HOME))
    assert (root.get("width"), root.get("height")) == ("62mm", "87mm")
    inner = [e for e in root if e.tag == NS + "svg"]
    assert [next(t for t in e.iter(NS + "text")).text for e in inner] == ["214", "7", "99"]
    assert [float(e.get("y")) for e in inner] == [0, 29, 58]
    for e, n in zip(inner, (214, 7, 99)):
        assert decode(code_of(e)[0]) == f"{HOME}/s/{n}"                    # each code holds its own spool's address


STOCK = {"a4-63x34": ((210.0, 297.0), 3, 8, 7.25, 12.9, 66.0, 33.9, (63.5, 33.9)),
         "a4-70x37": ((210.0, 297.0), 3, 8, 0.0, 0.5, 70.0, 37.0, (70.0, 37.0)),
         "letter-66x25": ((215.9, 279.4), 3, 10, 4.7625, 12.7, 69.85, 25.4, (66.675, 25.4))}


@pytest.mark.parametrize("size", list(STOCK))
def test_a_sheet_is_one_page_with_the_labels_where_the_stock_has_them(size):
    (pw, ph), cols, rows, left, top, px, py, (lw, lh) = STOCK[size]
    items = [labels.label_data(spool(n)) for n in range(1, cols * rows + 1)]
    root = parse(labels.file_svg(items, size, HOME))
    assert (float(root.get("width").removesuffix("mm")), float(root.get("height").removesuffix("mm"))) == (pw, ph)
    cells = [e for e in root if e.tag == NS + "svg"]
    assert len(cells) == cols * rows
    assert (float(cells[0].get("x")), float(cells[0].get("y"))) == pytest.approx((left, top))          # first label's corner
    last = cells[-1]
    assert (float(last.get("x")), float(last.get("y"))) == pytest.approx((left + (cols - 1) * px, top + (rows - 1) * py))
    assert [float(c.get("x")) for c in cells[:cols + 1]] == pytest.approx([left + i * px for i in range(cols)] + [left])  # row by row
    assert float(cells[cols].get("y")) == pytest.approx(top + py)
    assert (cells[0].get("width"), cells[0].get("height")) == (f"{lw:g}mm", f"{lh:g}mm")
    assert float(last.get("x")) + lw <= pw and float(last.get("y")) + lh <= ph                         # all on the paper
    assert [next(t for t in c.iter(NS + "text")).text for c in cells] == [str(n) for n in range(1, cols * rows + 1)]
    with pytest.raises(labels.LabelError) as e:
        labels.file_svg(items + items[:1], size, HOME)
    assert e.value.code == "too_many"
    assert e.value.message == f"A sheet of this size holds {cols * rows} labels. Ask for fewer, or choose another size."


def test_a_sheet_of_a_partly_filled_page_starts_at_the_top_left():
    root = parse(labels.file_svg([labels.label_data(spool(n)) for n in (5, 6)], "letter-66x25", HOME))
    assert [(c.get("x"), c.get("y")) for c in root if c.tag == NS + "svg"] == [("4.7625", "12.7"), ("74.6125", "12.7")]


# ---- the print page

def page(items, size="62x29", ids=(214,), error=None):
    return labels.print_page(items, size, list(ids), HOME, error)


def test_the_print_page_has_the_size_choice_the_labels_the_address_line_and_print():
    doc = page([labels.label_data(spool(214)), labels.label_data(spool(213))], ids=(214, 213))
    assert doc.startswith("<!doctype html>") and "<title>Print labels</title>" in doc
    links = re.findall(r'<a href="([^"]+)"( aria-current="true")?>([^<]+)</a>', doc)
    assert [(text, href) for href, _, text in links] == [
        ("62 x 29 mm", "labels?ids=214,213&amp;size=62x29"), ("50 x 30 mm", "labels?ids=214,213&amp;size=50x30"),
        ("A4 sheet of 24, 63.5 x 33.9 mm", "labels?ids=214,213&amp;size=a4-63x34"),
        ("A4 sheet of 24, 70 x 37 mm", "labels?ids=214,213&amp;size=a4-70x37"),
        ("Letter sheet of 30, 2-5/8 x 1 in", "labels?ids=214,213&amp;size=letter-66x25")]
    assert [text for _, current, text in links if current] == ["62 x 29 mm"]
    assert doc.count("<svg") == 2 and doc.count('role="img"') == 2
    assert ("The code holds this Hub&#x27;s address, https://spoolwarden.local:8710. If the Hub is renamed or moves to another "
            "port, old labels still open in the Spoolwarden app&#x27;s scanner but not from a phone&#x27;s camera.") in doc
    assert '<button id="print" type="button">Print</button>' in doc
    assert "<title>Print a label</title>" in page([labels.label_data(spool())])


def test_the_print_page_holds_no_inline_script_or_style():
    doc = page([labels.label_data(spool())])
    assert not re.search(r"<script(?![^>]*\ssrc=)", doc) and "<style" not in doc and " style=" not in doc
    assert 'href="labels/print.css?size=62x29"' in doc and 'src="labels/print.js"' in doc
    assert "script-src 'self'" in labels.PAGE_CSP and "unsafe-inline" not in labels.PAGE_CSP


def test_each_label_on_the_page_has_its_own_spools_address():
    doc = page([labels.label_data(spool(214)), labels.label_data(spool(213))], ids=(214, 213))
    grids = [decode(code_of(ET.fromstring(f"<r xmlns='{NS[1:-1]}'>{s}</r>"))[0]) for s in re.findall(r"<svg.*?</svg>", doc)]
    assert grids == [HOME + "/s/214", HOME + "/s/213"]


def test_a_number_the_hub_does_not_know_is_named_in_its_place():
    doc = page([labels.label_data(spool(214)), 999], ids=(214, 999))
    assert doc.count("<svg") == 1 and '<p class="missing">Spool 999 was not found.</p>' in doc


def test_the_error_state_replaces_the_labels():
    doc = page([], error="Spoolman is not reachable.")
    assert 'role="alert"' in doc and "Could not draw the labels. Spoolman is not reachable." in doc and "Try again" in doc
    assert "<svg" not in doc


def test_a_sheet_page_groups_the_labels_to_the_stocks_page():
    doc = page([labels.label_data(spool(n)) for n in range(1, 27)], "a4-63x34", range(1, 27))
    assert doc.count('class="sheet"') == 2 and doc.count("<svg") == 26
    doc = page([labels.label_data(spool(n)) for n in range(1, 32)], "letter-66x25", range(1, 32))
    assert doc.count('class="sheet"') == 2 and doc.count("<svg") == 31
    assert page([labels.label_data(spool(n)) for n in range(1, 31)], "letter-66x25", range(1, 31)).count('class="sheet"') == 1


def test_the_page_says_to_print_at_actual_size_for_a_sheet_only():
    line = '<p class="actual-size">Print at actual size (100%), not fitted to the page.</p>'
    for size in ("a4-63x34", "a4-70x37", "letter-66x25"):
        assert line in page([labels.label_data(spool())], size)
    for size in ("62x29", "50x30"):
        assert "actual size" not in page([labels.label_data(spool())], size)
    assert ".controls, .address, .missing { display: none; }" in labels.print_css("a4-63x34")    # the line hides when printing


def test_the_print_rules_hide_everything_but_the_labels_and_set_the_paper():
    roll = labels.print_css("62x29")
    assert "@page { size: 62mm 29mm; margin: 0; }" in roll and "break-after: page" in roll
    assert ".controls, .address, .missing { display: none; }" in roll
    assert "@page { size: 50mm 30mm; margin: 0; }" in labels.print_css("50x30")
    assert "window.print()" in labels.PRINT_JS


@pytest.mark.parametrize("size", list(STOCK))
def test_a_sheet_prints_on_its_stated_paper_with_the_stocks_margins_and_pitch(size):
    css = labels.print_css(size)
    (pw, ph), cols, rows, left, top, px, py, (lw, lh) = STOCK[size]
    paper = "A4" if pw == 210.0 else "letter"
    assert f"@page {{ size: {paper}; margin: 0; }}" in css and "size: auto" not in css
    assert f"padding: {top:g}mm 0 0 {left:g}mm" in css
    assert f"grid-template-columns: repeat({cols}, {lw:g}mm)" in css
    assert f"column-gap: {round(px - lw, 4):g}mm" in css and f"row-gap: {round(py - lh, 4):g}mm" in css
    assert "padding: 6mm 0 0" not in css                                                              # the fixed top padding is gone


def test_print_page_for_names_unknown_numbers_and_reports_spoolman_down():
    doc, status = labels.print_page_for(FakeSm(spool(214)), [214, 999], "50x30", {"port": 8710})
    assert status == 200 and doc.count("<svg") == 1 and "Spool 999 was not found." in doc and "size=50x30" in doc
    doc, status = labels.print_page_for(FakeSm(down=True), [214], "62x29", {"port": 8710})
    assert status == 502 and "Could not draw the labels. Spoolman is not reachable." in doc and "<svg" not in doc


# ---- the kit routes (6.1)

def kit_client(sm, web_on=False):
    cfg = {"port": 8710, "modules": {"web": web_on}}
    table = labels.kit_routes(lambda: sm, lambda: cfg)
    return TestClient(Starlette(routes=[Route(r.path, r.handler, methods=[r.method]) for r in table]),
                      follow_redirects=False), table


def test_the_kit_routes_are_the_two_of_the_contract_with_their_auth():
    _, table = kit_client(FakeSm())
    assert [(r.method, r.path, r.auth) for r in table] == [("GET", "/api/spools/{id:int}/label.svg", "token"),
                                                           ("GET", "/s/{id:int}", "open")]


def test_label_svg_answers_the_label_at_the_size_asked():
    c, _ = kit_client(FakeSm(spool(214)))
    r = c.get("/api/spools/214/label.svg")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert parse(r.text).get("width") == "62mm"
    for size, width in (("50x30", "50mm"), ("a4-63x34", "63.5mm"), ("a4-70x37", "70mm"), ("letter-66x25", "66.675mm")):
        assert parse(c.get(f"/api/spools/214/label.svg?size={size}").text).get("width") == width
    assert decode(code_of(parse(r.text))[0]) == ADDRESS


def test_label_svg_refusals_have_the_kits_shape():
    c, _ = kit_client(FakeSm(spool(214)))
    r = c.get("/api/spools/214/label.svg?size=A4")
    assert r.status_code == 400 and r.json() == {"error": "bad_size", "message": "The size must be 62x29, 50x30, a4-63x34, a4-70x37 or letter-66x25."}
    r = c.get("/api/spools/999/label.svg")
    assert r.status_code == 404 and r.json() == {"error": "not_found", "message": "Spool 999 was not found."}
    r = kit_client(FakeSm(down=True))[0].get("/api/spools/214/label.svg")
    assert r.status_code == 502 and r.json()["error"] == "spoolman_unreachable" and set(r.json()) == {"error", "message"}


def test_the_short_address_redirects_to_the_spool():
    r = kit_client(FakeSm(spool(214)))[0].get("/s/214")
    assert r.status_code == 302 and r.headers["location"] == "/#spools/214"
    r = kit_client(FakeSm(spool(214)), web_on=True)[0].get("/s/214")
    assert r.status_code == 302 and r.headers["location"] == "/intake#spools/214"


def test_the_short_address_of_an_unknown_number_is_a_404_and_needs_no_token():
    c, table = kit_client(FakeSm(spool(214)))
    r = c.get("/s/999")
    assert r.status_code == 404 and r.json() == {"error": "not_found", "message": "Spool 999 was not found."}
    assert next(t for t in table if t.path == "/s/{id:int}").auth == "open"
    assert kit_client(FakeSm(down=True))[0].get("/s/214").status_code == 502


# ---- the Hub page's routes (6.2)

class StubPage:
    """The page's `guard`, as far as these routes need it: the handler gets (request, body); a Refused is the page's shape."""

    def guard(self, fn, write=False, raw=False):
        from filament_kit.setup_page import Refused

        async def route(request):
            try:
                return await fn(request, {})
            except Refused as e:
                return JSONResponse({"error": e.message, **e.extra}, e.status)
        return route


def page_client(monkeypatch, sm):
    monkeypatch.setattr(labels_tab, "_spoolman", lambda: sm)
    table = labels_tab.routes(StubPage())
    return TestClient(Starlette(routes=[Route(p, h, methods=[m]) for p, h, m in table])), table


def test_the_page_routes_are_the_two_of_the_contract_and_the_two_files_of_the_print_page(monkeypatch):
    _, table = page_client(monkeypatch, FakeSm())
    assert [(p, m) for p, _, m in table] == [("/spools/{id:int}/label.svg", "GET"), ("/labels", "GET"),
                                             ("/labels/print.css", "GET"), ("/labels/print.js", "GET")]


def test_the_page_serves_the_label_the_print_page_and_its_files(monkeypatch):
    c, _ = page_client(monkeypatch, FakeSm(spool(214), spool(213)))
    r = c.get("/spools/214/label.svg?size=50x30")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and parse(r.text).get("width") == "50mm"
    r = c.get("/labels?ids=214,213,999&size=letter-66x25")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.text.count("<svg") == 2 and "Spool 999 was not found." in r.text
    assert "script-src 'self'" in r.headers["content-security-policy"]
    css = c.get("/labels/print.css?size=50x30")
    assert css.headers["content-type"].startswith("text/css") and "50mm 30mm" in css.text
    js = c.get("/labels/print.js")
    assert js.headers["content-type"].startswith("text/javascript") and "window.print" in js.text


def test_the_page_refuses_in_its_own_shape(monkeypatch):
    c, _ = page_client(monkeypatch, FakeSm(spool(214)))
    r = c.get("/spools/999/label.svg")
    assert r.status_code == 404 and r.json() == {"error": "Spool 999 was not found.", "code": "not_found"}
    r = c.get("/labels?ids=abc")
    assert r.status_code == 400 and r.json()["code"] == "bad_ids"
    r = c.get("/labels?ids=214&size=huge")
    assert r.status_code == 400 and r.json()["error"] == "The size must be 62x29, 50x30, a4-63x34, a4-70x37 or letter-66x25."
    assert c.get("/labels").status_code == 400
