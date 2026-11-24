"""Plan mounted label kit routes (/api/spools/{id}/label.svg, /s/{id}), Hub page labels_tab routes,
and `spool label --out` CLI command."""
from __future__ import annotations

import json
import xml.etree.ElementTree as ET

import pytest
from starlette.testclient import TestClient

from filament_kit import cli, config, inventory, modules, output, pairing
from filament_kit.api import create_app
from filament_kit.spoolman import Spoolman
from test_inventory import RULES, item


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir(exist_ok=True)
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    pairing.make_cert("fkhost", [], tmp_path / "etc")
    config.save({"modules": {n: True for n in modules.MODES["server"]},
                 "server_name": "Workshop", "spoolman_url": "http://127.0.0.1:7912",
                 "placement": {"box_count": 2}})
    return tmp_path


@pytest.fixture
def sm(spoolman_url):
    s = Spoolman(spoolman_url)
    s.ensure_fields()
    return s


def _seed_spool(sm: Spoolman) -> int:
    res = inventory.add_spools(sm, [item()], RULES, dry=False)
    return int(res.spool_ids[0])


def _auth(name="Phone", role=None) -> dict:
    return {"Authorization": "Bearer " + pairing.redeem_code(pairing.issue(name, role=role).code)}


def test_kit_label_svg_and_open_short_address_redirect_are_mounted(sm):
    sid = _seed_spool(sm)
    app = create_app(sm=sm)
    c = TestClient(app, client=("127.0.0.1", 1234), base_url="https://127.0.0.1:8710", raise_server_exceptions=False)
    phone = _auth("Phone")
    viewer = _auth("HA", role=pairing.VIEWER_ROLE)
    web = _auth("web", role=pairing.WEB_ROLE)
    station = _auth("station", role="station")

    # Unpaired caller is refused on /api/spools/{id}/label.svg; station & viewer tokens are 403
    assert c.get(f"/api/spools/{sid}/label.svg").status_code == 401
    assert c.get(f"/api/spools/{sid}/label.svg", headers=station).status_code == 403
    assert c.get(f"/api/spools/{sid}/label.svg", headers=viewer).status_code == 403

    # Paired phone and web token succeed
    for hdrs in (phone, web):
        r = c.get(f"/api/spools/{sid}/label.svg?size=50x30", headers=hdrs)
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
        assert r.headers.get("cache-control") == "no-store"
        assert ET.fromstring(r.text).get("width") == "50mm"

    # Kit error shapes
    bad = c.get(f"/api/spools/{sid}/label.svg?size=A4", headers=phone)
    assert bad.status_code == 400 and bad.json()["error"] == "bad_size"
    missing = c.get("/api/spools/999999/label.svg", headers=phone)
    assert missing.status_code == 404 and missing.json()["error"] == "not_found"

    # Unpaired /s/{id} returns only a 302 redirect (no spool data leaked)
    r_open = c.get(f"/s/{sid}", follow_redirects=False)
    assert r_open.status_code == 302 and r_open.headers["location"] == f"/#spools/{sid}"
    assert r_open.headers.get("cache-control") == "no-store"
    assert "Acme" not in r_open.text and "PLA" not in r_open.text and r_open.text == ""

    # When web module is on, /s/{id} redirects to /intake#spools/{id}
    cfg = config.load()
    cfg["modules"]["web"] = True
    config.save(cfg)
    r_web = c.get(f"/s/{sid}", follow_redirects=False)
    assert r_web.status_code == 302 and r_web.headers["location"] == f"/intake#spools/{sid}"

    assert c.get("/s/999999", follow_redirects=False).status_code == 404


def test_setup_console_labels_tab_routes_are_mounted(sm, monkeypatch):
    sid = _seed_spool(sm)
    from filament_kit.setup_console import labels_tab
    monkeypatch.setattr(labels_tab, "_spoolman", lambda: sm)
    app = create_app(sm=sm)
    c = TestClient(app, client=("127.0.0.1", 1234), base_url="https://127.0.0.1:8710", raise_server_exceptions=False)

    r_svg = c.get(f"/setup/api/spools/{sid}/label.svg?size=50x30")
    assert r_svg.status_code == 200 and r_svg.headers["content-type"].startswith("image/svg+xml")
    assert ET.fromstring(r_svg.text).get("width") == "50mm"

    r_page = c.get(f"/setup/api/labels?ids={sid}&size=letter-66x25")
    assert r_page.status_code == 200 and r_page.headers["content-type"].startswith("text/html")
    assert "<svg" in r_page.text and "script-src 'self'" in r_page.headers["content-security-policy"]

    assert c.get("/setup/api/labels/print.css?size=50x30").status_code == 200
    assert c.get("/setup/api/labels/print.js").status_code == 200
    assert c.get("/setup/api/spools/999999/label.svg").status_code == 404


def test_cli_spool_label_writes_svg_and_prints_note_and_json(sm, tmp_path, monkeypatch, capsys):
    sid = _seed_spool(sm)
    app = create_app(sm=sm)
    tc = TestClient(app)
    hdrs = _auth("cli")

    class _Adapter:
        def get(self, path):
            r = tc.get(path, headers=hdrs)
            if r.status_code >= 400:
                from filament_kit.web.slots import SlotError
                raise SlotError(r.status_code, r.json().get("message", ""), body=r.json())
            return r.json()

    monkeypatch.setattr(cli, "_server_api", lambda: _Adapter())

    out1 = tmp_path / "one.svg"
    assert cli.main(["spool", "label", str(sid), "--out", str(out1)]) == 0
    out, _ = capsys.readouterr()
    assert "Wrote 1 label (62 x 29 mm)" in out and f"/s/{sid}" in out
    assert ET.fromstring(out1.read_text(encoding="utf-8")).get("width") == "62mm"

    out2 = tmp_path / "sheet.svg"
    assert cli.main(["spool", "label", str(sid), str(sid), "--size", "a4-63x34", "--out", str(out2), "--json"]) == 0
    doc = json.loads(capsys.readouterr()[0])
    assert doc == {"ok": True, "file": str(out2), "size": "a4-63x34", "labels": 2}

    assert cli.main(["spool", "label", str(sid)]) == output.USAGE
    assert cli.main(["spool", "label", str(sid), "--size", "A4", "--out", str(out1)]) == output.USAGE
    monkeypatch.setattr(cli, "_server_api", lambda: None)
    assert cli.main(["spool", "label", str(sid), "--out", str(out1)]) == output.HUB_DOWN

    assert cli.main(["help", "--markdown"]) == 0
    assert "`spool label <number>..." in capsys.readouterr()[0]
