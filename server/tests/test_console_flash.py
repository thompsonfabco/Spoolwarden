"""The setup page's "Flash a device" tab (setup_console/flash_tab.py, setup_static/setup_flash.js): the kit's own images
for the browser, the Printer Station's token for the browser way, and the server way through the root helper's op
reader_usb and the job reader_setup (`reader setup --pending`)."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from test_setup_page import AWAY, BASE, CODE, H, HOME, Helper, client, env, make, post  # noqa: F401

from filament_kit import cli, config, reader_flash as rf, reader_setup as rs, reader_tokens, setup_helper

STATIC = Path(cli.__file__).parent / "setup_static"
ROOT = Path(cli.__file__).resolve().parents[2]
BOARD = rs.Reader("/dev/ttyACM0", "/dev/serial/by-id/usb-Espressif_USB_JTAG_serial_debug_unit_AA:BB:CC:DD:EE:FF-if00",
                  "USB JTAG/serial debug unit")


class Mgr:
    def sync(self):
        pass

    def state(self, serial):
        return None


@pytest.fixture
def app(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(rs, "find_readers", lambda: [BOARD])
    return make(env, Helper(), Mgr())


HUB_IP = "192.0.2.7"


@pytest.fixture
def home(monkeypatch):
    """The Hub's own certificate in the etc folder (as the real maker writes it) and its home name and address."""
    from filament_kit import pairing
    etc = config.paths().etc
    etc.mkdir(parents=True, exist_ok=True)
    pairing.make_cert("fkhub", [HUB_IP], etc)
    monkeypatch.setattr(pairing, "short_hostname", lambda: "fkhub")
    monkeypatch.setattr(pairing, "local_ips", lambda: [HUB_IP, "192.0.2.8"])
    return (etc / "cert.pem").read_text(encoding="ascii")


def _printer(name="Garage"):
    from filament_kit.bridge import registry
    return registry.add_bambu("01P00A000000001", name, "X1C", "192.0.2.50", "12345678").serial


# ---------- the images ----------

def test_the_listing_names_both_images_and_the_server_boards_without_their_serial(app):
    r = client(app).get("/setup/api/firmware")
    assert r.status_code == 200
    d = r.json()
    devs = {x["id"]: x for x in d["devices"]}
    station = rf.bundled_image()
    assert devs["printer-station"]["bundled"] and devs["printer-station"]["version"] == str(station[1])
    assert devs["printer-station"]["settings"] and not devs["intake-panel"]["settings"]
    assert devs["intake-panel"]["version"] == rf.panel_image()[1]
    assert d["server_boards"] == [{"port": "/dev/ttyACM0", "product": "USB JTAG/serial debug unit"}]
    assert "AA:BB" not in r.text
    assert "settings_ok" not in d and "settings_why" not in d        # no public address is asked for any more
    assert d["printers"] == []


def test_the_listing_offers_the_printers_by_name_and_none_with_the_feature_off(app):
    serial = _printer("Garage")
    assert client(app).get("/setup/api/firmware").json()["printers"] == [{"id": serial, "name": "Garage"}]
    cfg = config.load()
    cfg["modules"] = {**cfg["modules"], "printers": False}
    config.save(cfg)
    assert client(app).get("/setup/api/firmware").json()["printers"] == []


def test_an_image_is_served_whole_from_the_kit_and_only_to_the_home_network(app):
    for dev in ("printer-station", "intake-panel"):
        r = client(app).get(f"/setup/api/firmware/{dev}.bin")
        assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream"
        assert r.content == rf.device_image(dev)[0].read_bytes()
    assert client(app).get("/setup/api/firmware/other.bin").status_code == 404
    assert client(app).get("/setup/api/firmware/printer-station").status_code == 404
    assert client(app, AWAY).get("/setup/api/firmware/printer-station.bin").status_code == 403


def test_the_installed_images_win_over_a_checkouts(env, tmp_path):  # noqa: F811
    panel = config.paths().opt / "resources" / "panel"
    panel.mkdir(parents=True)
    (panel / "intake-panel.bin").write_bytes(b"\xe9panel")
    (panel / "VERSION").write_text("99\n")
    assert rf.device_image("intake-panel") == (panel / "intake-panel.bin", "99")
    assert rf.device_image("nothing") is None


# ---------- the browser way: the Printer Station's token ----------

def test_provision_needs_the_code_and_gives_the_token_and_the_address(app, home):
    assert post(client(app), "flash/provision", {}, code=None).status_code in (401, 403)
    assert reader_tokens.TokenStore().list() == []
    r = post(client(app), "flash/provision", {"name": "bench"})
    assert r.status_code == 200, r.text
    d = r.json()
    port = config.load()["port"]
    assert d["base"] == f"https://fkhub.local:{port}/reader" and len(d["token"]) >= 16
    assert d["root_ca"] == home and d["hub_ip"] == HUB_IP and d["printer"] == ""
    assert "kit.example" not in r.text            # no public address anywhere in it
    assert [(t["id"], t["name"]) for t in reader_tokens.TokenStore().list()] == [(d["id"], "bench")]
    # a failed run takes it back with the route the Devices tab has
    assert post(client(app), f"readers/{d['id']}/revoke", {}).status_code == 200
    assert reader_tokens.TokenStore().list() == []


def test_provision_needs_no_public_address_and_carries_the_chosen_printer(app, home):
    assert not (config.load().get("web") or {}).get("public_url")
    serial = _printer()
    r = post(client(app), "flash/provision", {"printer": serial})
    assert r.status_code == 200 and r.json()["printer"] == serial and r.json()["base"].startswith("https://fkhub.local:")


def test_provision_refuses_a_printer_the_hub_does_not_have_and_makes_no_token(app, home):
    r = post(client(app), "flash/provision", {"printer": "NOPE"})
    assert r.status_code == 400 and r.json()["field"] == "printer"
    assert reader_tokens.TokenStore().list() == []


def test_provision_without_the_hubs_certificate_makes_no_token(app, monkeypatch):
    monkeypatch.setattr(rs.pairing, "short_hostname", lambda: "fkhub")
    r = post(client(app), "flash/provision", {})
    assert r.status_code == 409 and "certificate" in r.json()["error"]
    assert reader_tokens.TokenStore().list() == []


# ---------- the server way: op reader_usb, job reader_setup ----------

ANSWERS = {"ssid": "Home", "pass": "wifi-secret-1", "buzzer": "low"}


def test_the_server_way_leaves_the_answers_root_only_and_starts_the_job(app, monkeypatch):
    started = []
    monkeypatch.setattr(setup_helper, "_start_job", lambda name, why="": started.append(name))
    assert post(client(app), "flash/server", ANSWERS, code=None).status_code in (401, 403)
    assert not rf.pending_path().exists()
    r = post(client(app), "flash/server", ANSWERS)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "reader_setup" and started == ["reader_setup"]
    p = rf.pending_path()
    assert json.loads(p.read_text()) == {**ANSWERS, "port": "/dev/ttyACM0", "name": "printer-station", "printer": ""}
    if os.name == "posix":
        assert p.stat().st_mode & 0o777 == 0o600
    assert "wifi-secret-1" not in r.text


def test_the_server_way_carries_the_chosen_printer_and_refuses_a_bad_one(app, monkeypatch):
    monkeypatch.setattr(setup_helper, "_start_job", lambda name, why="": None)
    serial = _printer()
    assert post(client(app), "flash/server", {**ANSWERS, "printer": serial}).status_code == 200
    assert json.loads(rf.pending_path().read_text())["printer"] == serial
    rf.pending_path().unlink()
    r = post(client(app), "flash/server", {**ANSWERS, "printer": "a b/c"})
    assert r.status_code == 400 and r.json()["field"] == "printer" and not rf.pending_path().exists()


def _klipper(name="Bench"):
    from filament_kit.bridge import registry
    return registry.add_klipper(name, "mainsail.local").serial


def _only_klipper_is_on():
    cfg = config.load()
    cfg["modules"] = {**cfg["modules"], "printers": False, "klipper": True}
    config.save(cfg)


def test_the_printer_list_holds_bambu_and_klipper_printers_alike(app):
    bambu, klipper = _printer("Garage"), _klipper("Bench")
    got = client(app).get("/setup/api/firmware").json()["printers"]
    assert {(p["id"], p["name"]) for p in got} == {(bambu, "Garage"), (klipper, "Bench")}


def test_a_klipper_only_hub_offers_its_printers_and_a_hub_with_neither_feature_none(app):
    _printer("Garage")
    klipper = _klipper("Bench")
    _only_klipper_is_on()
    assert {p["id"] for p in client(app).get("/setup/api/firmware").json()["printers"]} == {"01P00A000000001", klipper}
    cfg = config.load()
    cfg["modules"] = {**cfg["modules"], "printers": False, "klipper": False}
    config.save(cfg)
    assert client(app).get("/setup/api/firmware").json()["printers"] == []


def test_all_three_ways_refuse_a_printer_the_hub_does_not_have_with_one_text(app, home, monkeypatch, usb, capsys):
    """The browser way (flash/provision), the Hub's USB (flash/server and the root helper's op) and the command line
    (`reader setup --pending` and `--printer`) all say reader_setup.UNKNOWN_PRINTER for an id the Hub has not."""
    monkeypatch.setattr(setup_helper, "_start_job", lambda name, why="": None)
    text = rs.UNKNOWN_PRINTER
    assert post(client(app), "flash/provision", {"printer": "01P00A000000009"}).json()["error"] == text
    assert reader_tokens.TokenStore().list() == []
    assert post(client(app), "flash/server", {**ANSWERS, "printer": "01P00A000000009"}).json()["error"] == text
    with pytest.raises(setup_helper.Refused) as e:                 # the helper, asked directly (it is a root door of its own)
        setup_helper._reader_usb({**ANSWERS, "printer": "01P00A000000009"})
    assert e.value.message == text and not rf.pending_path().exists()
    # the command line: the pending answers, and the option
    rf.write_pending({**ANSWERS, "port": "/dev/ttyACM1", "printer": "01P00A000000009"})
    capsys.readouterr()
    assert cli._reader_setup_usb(_args(base=None), _no_ask, _no_ask, run=_run) == 1
    assert text in capsys.readouterr().err
    with pytest.raises(rs.SetupError) as e:
        rs.home_settings("x" * 70)                                  # too long for the firmware: the same refusal, not FK-ERR
    assert str(e.value) == text
    assert "settings" not in usb and reader_tokens.TokenStore().list() == []


def test_a_job_that_does_not_start_leaves_no_answers(app, monkeypatch):
    def refuse(name, why=""):
        raise setup_helper.Refused(500, "the job did not start")
    monkeypatch.setattr(setup_helper, "_start_job", refuse)
    assert post(client(app), "flash/server", ANSWERS).status_code == 500
    assert not rf.pending_path().exists()


@pytest.mark.parametrize("body, status, words", [
    ({"ssid": "", "pass": "p"}, 400, "Wi-Fi name"),
    ({"ssid": "Home", "pass": "p" * 64}, 400, "password"),
    ({"ssid": "Home", "pass": "p", "buzzer": "loud"}, 400, "buzzer"),
    ({"ssid": "Home", "pass": "p", "name": "a/b"}, 400, "name"),
    ({"ssid": "Home", "pass": "p", "port": "/dev/ttyACM9"}, 409, "no longer plugged in"),
    ({"pass": "p"}, 400, "Wi-Fi name"),
])
def test_bad_answers_are_refused_and_nothing_is_left(app, monkeypatch, body, status, words):
    monkeypatch.setattr(setup_helper, "_start_job", lambda name, why="": None)
    r = post(client(app), "flash/server", body)
    assert r.status_code == status and words in r.json()["error"]
    assert not rf.pending_path().exists()


def test_no_board_or_several_without_a_pick_are_refused(app, monkeypatch):
    monkeypatch.setattr(setup_helper, "_start_job", lambda name, why="": None)
    monkeypatch.setattr(rs, "find_readers", lambda: [])
    assert "no ESP32 board" in post(client(app), "flash/server", ANSWERS).json()["error"]
    monkeypatch.setattr(rs, "find_readers", lambda: [BOARD, rs.Reader("/dev/ttyACM1", None, "x")])
    assert "several" in post(client(app), "flash/server", ANSWERS).json()["error"]
    assert post(client(app), "flash/server", {**ANSWERS, "port": "/dev/ttyACM1"}).status_code == 200
    assert json.loads(rf.pending_path().read_text())["port"] == "/dev/ttyACM1"


def test_the_job_is_not_started_by_name_alone(app):
    r = post(client(app), "job", {"name": "reader_setup"})
    assert r.status_code == 400 and "Flash a device" in r.json()["error"]


def test_the_job_runs_reader_setup_pending(monkeypatch):
    assert setup_helper.JOBS["reader_setup"] == ("reader", "setup", "--pending")
    assert "reader_setup" in setup_helper.BUSY
    seen = []
    monkeypatch.setattr(cli, "cmd_reader_setup", lambda a: seen.append(a.pending) or 0)
    assert cli._main(["reader", "setup", "--pending"]) == 0 and seen == [True]


# ---------- the job's side: `reader setup --pending` ----------

def _args(**kw):
    base = dict(name=None, ssid=None, source=None, port=None, out=None, ota_pubkey=None, token_file=None,
                base="https://kit.example.net/filament/reader", secrets=None, buzzer=None, reflash=False, pending=True)
    base.update(kw)
    return __import__("argparse").Namespace(**base)


@pytest.fixture
def usb(env, monkeypatch):  # noqa: F811
    monkeypatch.setattr(cli, "_is_root", lambda: True)
    monkeypatch.setattr(rs, "find_readers", lambda: [BOARD, rs.Reader("/dev/ttyACM1", None, "x")])
    got = {}

    def setup(port, settings, reflash=False, run=None, step=None):
        got.update(port=port, settings=settings)
        return got.get("result", rf.Result(True, "Done. The Printer Station joined 'Home' and reached the kit."))
    monkeypatch.setattr(rf, "setup", setup)
    return got


def _no_ask(prompt=""):
    raise EOFError


def _run(cmd, **kw):
    return subprocess.CompletedProcess(cmd, 3, b"", b"")


def test_pending_takes_the_answers_once_asks_nothing_and_prints_no_secret(usb, capsys):
    rf.write_pending({**ANSWERS, "port": "/dev/ttyACM1", "name": "bench"})
    assert cli._reader_setup_usb(_args(), _no_ask, _no_ask, run=_run) == 0
    assert not rf.pending_path().exists()
    s = usb["settings"]
    assert (usb["port"], s["ssid"], s["pass"], s["buzzer_low"]) == ("/dev/ttyACM1", "Home", "wifi-secret-1", True)
    assert [t["name"] for t in reader_tokens.TokenStore().list()] == ["bench"]
    out = capsys.readouterr()
    assert "wifi-secret-1" not in out.out + out.err and s["token"] not in out.out + out.err
    # once: a second run has nothing to take
    assert cli._reader_setup_usb(_args(), _no_ask, _no_ask, run=_run) == 1
    assert "no answers from the setup page" in capsys.readouterr().err


def test_pending_without_a_base_gives_the_reader_the_hubs_home_address(usb, home):
    _printer()
    rf.write_pending({**ANSWERS, "port": "/dev/ttyACM1", "printer": "01P00A000000001"})
    assert cli._reader_setup_usb(_args(base=None), _no_ask, _no_ask, run=_run) == 0
    s = usb["settings"]
    assert s["base"] == f"https://fkhub.local:{config.load()['port']}/reader"
    assert (s["root_ca"], s["hub_ip"], s["printer"]) == (home, HUB_IP, "01P00A000000001")


def test_a_base_given_by_hand_keeps_the_older_settings(usb):
    rf.write_pending({**ANSWERS, "port": "/dev/ttyACM1"})
    assert cli._reader_setup_usb(_args(), _no_ask, _no_ask, run=_run) == 0
    assert not {"root_ca", "hub_ip", "printer"} & set(usb["settings"])


def test_pending_without_a_port_and_several_boards_asks_nothing(usb, capsys):
    rf.write_pending(ANSWERS)
    assert cli._reader_setup_usb(_args(), _no_ask, _no_ask, run=_run) == 1
    assert "pick the Printer Station's port on the setup page" in " ".join(capsys.readouterr().err.split())
    assert reader_tokens.TokenStore().list() == [] and not rf.pending_path().exists()


def test_old_answers_are_dropped(usb, capsys):
    rf.write_pending({**ANSWERS, "port": "/dev/ttyACM0"})
    old = time.time() - rf.PENDING_MAX_AGE - 5
    os.utime(rf.pending_path(), (old, old))
    assert cli._reader_setup_usb(_args(), _no_ask, _no_ask, run=_run) == 1
    assert not rf.pending_path().exists() and "older than 15 minutes" in capsys.readouterr().err


def test_a_failed_job_removes_its_token(usb, capsys):
    usb["result"] = rf.Result(False, "the reader could not join the Wi-Fi network 'Home'")
    rf.write_pending({**ANSWERS, "port": "/dev/ttyACM0"})
    assert cli._reader_setup_usb(_args(), _no_ask, _no_ask, run=_run) == 1
    assert reader_tokens.TokenStore().list() == []


# ---------- the page ----------

def test_the_tab_and_its_assets_are_served(app):
    page = client(app).get("/setup").text
    assert "/setup/setup_flash.js" in page and "esptool.bundle.js" not in page      # loaded only to write a board
    for name in ("setup_flash.js", "esptool.bundle.js"):
        r = client(app).get("/setup/" + name)
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/javascript")
    js = (STATIC / "setup.js").read_text(encoding="utf-8")
    assert 'flash: ["nav_flash_device", "flash", () => [flashTab]]' in js
    # the job strip titles the station setup job from the catalogue (02 8.4)
    assert '"reader_setup"' in (ROOT / "web-shared" / "components-jobs.js").read_text(encoding="utf-8")
    assert '"job_title_reader_setup": "Setting up a Tag Reader"' in (ROOT / "strings" / "en.json").read_text(encoding="utf-8")


def test_the_vendored_esptool_js_is_the_published_bundle():
    """THIRD-PARTY.md names its SHA-256; the file is esptool-js 0.7.0's bundle.js as published, byte for byte."""
    digest = hashlib.sha256((STATIC / "esptool.bundle.js").read_bytes()).hexdigest()
    third = (ROOT / "THIRD-PARTY.md").read_text(encoding="utf-8")
    assert digest == "00ebe6fb0b202976d3e2a3c6b9a6bd438c26a81d2af6739c95ac276a64c1a84e" and digest in third
    lic = (STATIC / "esptool.bundle.LICENSES.txt").read_text(encoding="utf-8")
    assert "Apache License" in lic and "pako 2.1.0" in lic and "atob-lite 2.0.0" in lic


def test_the_page_says_what_to_do_without_web_serial():
    js = (STATIC / "setup_flash.js").read_text(encoding="utf-8")
    assert 'const webSerialOk = () => "serial" in navigator && window.isSecureContext;' in js
    assert '"flash_no_serial"' in js and '"flash_phone_note"' in js
    assert 'import("/setup/esptool.bundle.js")' in js
    assert "innerHTML" not in js


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_the_tab_script_parses_and_reads_the_readers_answer():
    js = (STATIC / "setup_flash.js").read_text(encoding="utf-8")
    subprocess.run([shutil.which("node"), "--check", str(STATIC / "setup_flash.js")], check=True, timeout=30)
    fn = js.split("function parseHello(line) {")[1].split("\n}\n")[0]
    probe = ["FK-READER 1794501335 configured=1 wifi=ok server=unauth", "FK-READER x", "HELLO intake-panel 11"]
    code = f"function parseHello(line) {{{fn}\n}}\nconsole.log(JSON.stringify({json.dumps(probe)}.map(parseHello)));"
    out = subprocess.run([shutil.which("node"), "-e", code], capture_output=True, text=True, timeout=30, check=True).stdout
    assert json.loads(out) == [{"build": 1794501335, "configured": True, "wifi": "ok", "server": "unauth"}, None, None]


# A fake Web Serial board for the tab's own provisioning code: it speaks the firmware's lines (settings.h), restarts
# after FK-OK (the port goes away, then comes back) and then reports its first start.
FAKE_SERIAL = r"""
const vm = require("vm");
const src = require("fs").readFileSync(process.argv[2], "utf8");
const scenario = JSON.parse(process.argv[3]);
const board = { settings: null, gone: false, lines: [], opens: 0 };
function makePort() {
  let ctl = null;
  const enc = new TextEncoder();
  let buf = "";
  const say = (t) => { if (ctl) ctl.enqueue(enc.encode(t)); };
  return {
    getInfo: () => ({ usbVendorId: 0x303a, usbProductId: 0x1001 }),
    async open() { if (board.gone) throw new Error("not there"); board.opens++;
      this.readable = new ReadableStream({ start(c) { ctl = c; } });
      this.writable = new WritableStream({ write(chunk) {
        buf += new TextDecoder().decode(chunk);
        let i;
        while ((i = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, i); buf = buf.slice(i + 1); board.lines.push(line.slice(0, 7));
          if (line === "FK?") {
            const done = board.settings !== null;
            // scenario.line: what a reader that joined the Wi-Fi sends after its settings (a late or cut-short report)
            say(done && scenario.line ? scenario.line + "\n"
              : `FK-READER 1794501335 configured=${done ? 1 : 0} wifi=${done ? scenario.wifi : "-"} server=${done ? scenario.server : "-"}\n`);
          } else if (line.startsWith("FK-SET ")) {
            if (scenario.refuse) { say(`FK-ERR ${scenario.refuse}\n`); continue; }
            board.settings = JSON.parse(line.slice(7));
            say("FK-OK\n");
            setTimeout(() => { board.gone = true; try { ctl.close(); } catch {} setTimeout(() => { board.gone = false; }, 800); }, 50);
          }
        }
      } });
    },
    async setSignals() {},
    async close() {},
  };
}
// a minute of waiting for the report passes in about a second when the scenario sends a line
const t0 = Date.now(), FastDate = { now: () => t0 + (Date.now() - t0) * 60 };
const ctx = { navigator: { serial: { getPorts: async () => [makePort()] } }, window: { isSecureContext: true },
  TextEncoder, TextDecoder, setTimeout, clearTimeout, console, Date: scenario.line ? FastDate : Date, JSON, Promise, Math };
vm.createContext(ctx);
vm.runInContext(src, ctx);
const settings = { ssid: "Home", pass: "pw", base: "https://kit.example.net/filament/reader", token: "t".repeat(43), buzzer_low: false };
const steps = [];
ctx.provisionOverSerial({ usbVendorId: 0x303a, usbProductId: 0x1001 }, settings, (s) => steps.push(s)).then((r) => {
  console.log(JSON.stringify({ ...r, steps, settings: board.settings, sent: board.lines.filter((l) => l !== "FK?") }));
});
"""


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
@pytest.mark.parametrize("scenario, ok, code", [
    ({"wifi": "ok", "server": "ok"}, True, None),
    ({"wifi": "fail", "server": "-"}, False, "wifi"),
    ({"wifi": "ok", "server": "unauth"}, False, "hub_refused"),
    ({"wifi": "ok", "server": "fail"}, False, "hub_unreachable"),
    ({"wifi": "ok", "server": "ok", "refuse": "not a JSON object"}, False, "refused"),
])
def test_the_browser_provisioning_against_a_fake_board(tmp_path, scenario, ok, code):
    script = tmp_path / "fake.js"
    script.write_text(FAKE_SERIAL, encoding="utf-8")
    out = subprocess.run([shutil.which("node"), str(script), str(STATIC / "setup_flash.js"), json.dumps(scenario)],
                         capture_output=True, text=True, timeout=120, check=True).stdout
    r = json.loads(out)
    assert r["ok"] is ok and r.get("code") == code and r.get("steps") == (["restarting", "sending", "report"] if ok or code != "refused" else ["restarting", "sending"]), r
    if code == "refused":
        assert r["reason"] == "not a JSON object"
    if not scenario.get("refuse"):
        assert r["settings"]["token"] == "t" * 43 and r["sent"] == ["FK-SET "]


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
@pytest.mark.parametrize("line", [
    "FK-READER 1794501335 configured=1 wifi=ok server=-",      # joined the Wi-Fi, the Hub check not finished: no report comes
    "FK-READER 1794501335 configured=1 wifi=ok server=o",      # a report cut short
    "FK-READER 1794501335 configured=1 wifi=o",
    "FK-READER 1794501335 configured=1 wifi=f",
])
def test_a_late_or_cut_short_report_from_a_reader_that_joined_the_wifi_never_says_wifi(tmp_path, line):
    """Reproduces the owner's wrong text. On 58731d34 these gave "report" or "hub_unreachable" ("did not report back" or
    "could not reach the Hub"); the page's answer for a report that does not come is its own "late" (R17)."""
    script = tmp_path / "fake.js"
    script.write_text(FAKE_SERIAL, encoding="utf-8")
    out = subprocess.run([shutil.which("node"), str(script), str(STATIC / "setup_flash.js"), json.dumps({"line": line})],
                         capture_output=True, text=True, timeout=120, check=True).stdout
    r = json.loads(out)
    assert r["ok"] is False and r["code"] == "late", r
    texts = json.loads((ROOT / "strings" / "en.json").read_text(encoding="utf-8"))
    assert texts["flash_error_late"] == "The reader did not answer in time. Run this again."


def test_the_flash_form_offers_the_printer_and_writes_the_homes_settings():
    js = (STATIC / "setup_flash.js").read_text(encoding="utf-8")
    # "Choose on the reader" first, so it is the default; then the Hub's printers by name
    assert 'options: [{ value: "", label: SW.t("flash_printer_reader") }, ...(F.printers || []).map(' in js
    assert 'printer: printer._control.value' in js
    # the browser way sends it to provision, the server way to flash/server, and the board gets what provision answered
    assert 'api("flash/provision", { code: theCode(), name: a.name || undefined, printer: a.printer })' in js
    assert 'port, printer: a.printer, name: a.name || undefined }' in js
    assert "root_ca: tok.root_ca, hub_ip: tok.hub_ip, printer: tok.printer" in js
    # no refusal for a missing public address
    assert "noPublic" not in js and "flash_no_public" not in js and "settings_ok" not in js
    texts = json.loads((ROOT / "strings" / "en.json").read_text(encoding="utf-8"))
    assert "flash_no_public" not in texts and texts["flash_printer"] == "Printer" and texts["flash_printer_reader"] == "Choose on the reader"


@pytest.mark.skipif(not shutil.which("node"), reason="needs node")
def test_the_hub_unreachable_text_names_the_home_address_it_tried(tmp_path):
    js = (STATIC / "setup_flash.js").read_text(encoding="utf-8")
    fn = "function provisionText" + js.split("function provisionText")[1].split("\n}\n")[0] + "\n}\n"
    texts = json.loads((ROOT / "strings" / "en.json").read_text(encoding="utf-8"))
    code = ("const T = " + json.dumps({k: texts[k] for k in ("flash_error_hub_unreachable", "flash_error_wifi")}) + ";\n"
            "const SW = { t: (k, v) => T[k].replace(/\\{(\\w+)\\}/g, (_, n) => v[n]) };\n" + fn +
            'console.log(JSON.stringify([provisionText({ code: "hub_unreachable" }, "Home", "https://fkhub.local:8443/reader (192.0.2.7)"),'
            ' provisionText({ code: "wifi" }, "Home", "x")]));')
    out = subprocess.run([shutil.which("node"), "-e", code], capture_output=True, encoding="utf-8", timeout=30, check=True).stdout
    unreachable, wifi = json.loads(out)
    assert "https://fkhub.local:8443/reader (192.0.2.7)" in unreachable and "public address" not in unreachable
    assert "could not join 'Home'" in wifi


def test_every_text_the_flash_section_says_is_in_the_catalogue():
    """The section says nothing of its own (S2.16): each key it asks the catalogue for exists, with the words 03 11 gives."""
    import re
    js = (STATIC / "setup_flash.js").read_text(encoding="utf-8")
    texts = json.loads((ROOT / "strings" / "en.json").read_text(encoding="utf-8"))
    keys = set(re.findall(r'SW\.t\("([a-z_]+)"', js)) | {"flash_step_" + k for k in re.findall(r'"(connecting|writing|restarting|sending|report)"', js)}
    keys |= {k for k in re.findall(r'"((?:flash|common)_[a-z_]+)"', js)}
    keys = {k for k in keys if not k.endswith("_")}      # the two built from a name (flash_step_<step>, job_title_<job>)
    assert keys and all(k in texts for k in keys), sorted(k for k in keys if k not in texts)
    assert texts["flash_error_wifi"] == "The Tag Reader could not join '{wifi}'. Check the Wi-Fi name and password (2.4 GHz networks only)."
