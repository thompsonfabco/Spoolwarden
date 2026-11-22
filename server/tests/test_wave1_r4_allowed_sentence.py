"""R597: the Devices page shows "This Weigh Station answers only the addresses on its allow-list." under a station's page link when the
station's `page_from` is `allowed` (R83 2026-11-18 01:14 point 1); the Hub already passes `page_from` through (device_tab.py)."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "server" / "filament_kit" / "setup_static" / "setup_devices.js"
SENTENCE = "This Weigh Station answers only the addresses on its allow-list."


def test_the_catalogue_holds_the_sentence_and_the_page_reads_page_from():
    keys = json.loads((ROOT / "strings" / "en.json").read_text(encoding="utf-8"))
    assert keys["devices_station_allow_list_only"] == SENTENCE
    js = SCRIPT.read_text(encoding="utf-8")
    assert "devOwnPage(row, r.own_page, r.page_from)" in js
    assert 'pageFrom === "allowed"' in js and 'devT("devices_station_allow_list_only")' in js


def test_the_sentence_is_drawn_only_for_the_allow_list(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.fail("this test needs Node")
    js = SCRIPT.read_text(encoding="utf-8")
    snippet = js[js.index("function devOwnPage("):js.index("function devDetail(")]
    harness = r"""
const h = (tag, props, ...kids) => ({ tag, ...props, kids: kids.flat() });
const SW = { btn: () => ({}), result() {}, t: (k) => k };
const devT = (k) => k, coded = async () => {}, api = async () => ({}), theCode = () => "", say = () => {};
%s
const text = (el) => JSON.stringify(el);
console.log(JSON.stringify({ allowed: text(devOwnPage({ id: "a" }, "https://s:8720", "allowed")).includes("devices_station_allow_list_only"),
  home: text(devOwnPage({ id: "a" }, "https://s:8720", "home")).includes("devices_station_allow_list_only"),
  none: text(devOwnPage({ id: "a" }, "https://s:8720")).includes("devices_station_allow_list_only") }));
""" % snippet
    f = tmp_path / "own_page.js"
    f.write_text(harness, encoding="utf-8")
    run = subprocess.run([node, str(f)], capture_output=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout) == {"allowed": True, "home": False, "none": False}
