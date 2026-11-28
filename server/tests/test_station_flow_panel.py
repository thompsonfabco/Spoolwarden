"""The intake flow on the button panel (station/flow.py + the panel relay), ported from panel sync 5's test_panel.py:
a fake ServerApi in place of the kit server, a recording serial port in place of the D1 mini, the flow's threads run
inline. The run against the real kit API is test_station_flow_e2e.py."""
from __future__ import annotations

import json
import os
import threading
import time
import types
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from filament_kit import config
from filament_kit.station import beep, camera, flow, nfc, panel, scale as station_scale, server_api, speech
from filament_kit.station import http as station_http
from filament_kit.station import state as st

JPEG = b"\xff\xd8\xff\xe0" + b"j" * 2000
SPOOL = {"id": 123, "vendor": "SUNLU", "name": "PLA+ Black", "material": "PLA+", "colour_hex": "1A1A1A",
         "remaining_g": 640, "spool_weight_g": 165, "location": "Rack 1"}
FIL = {"vendor": "Elegoo", "name": "PLA Galaxy Blue", "material": "PLA", "color_hex": "1F3A93", "net": 1000,
       "tare": 160, "sealed": False}
LINKED = "4:aa:aa"


class FakeApi:
    """The kit server's three calls as server_api.ServerApi answers them: one linked tag; recognition as `mode`."""

    def __init__(self):
        self.mode, self.calls, self.paired = "ok", [], True
        self.during = None  # called inside recognize (e.g. red pressed while it runs)

    def tag_lookup(self, uid):
        self.calls.append(("tag", uid))
        if uid == LINKED:
            return 200, {"known": True, "uid": uid, "spool": SPOOL,
                         "display": ["Spool #123", "SUNLU PLA+ Black", "640 g left"]}
        return 200, {"known": False, "uid": uid, "display": ["New Tag Scanned"]}

    def recognize(self, photos, uid=None):
        self.calls.append(("recognize", photos, uid, flow.flow["step"]))
        if self.during:
            self.during()
        if self.mode == "ok":
            return 200, {"source": "claude", "item": FIL, "confidence": "high", "questions": [],
                         "display": ["Recognised", "Elegoo PLA Galaxy Blue", "opened"]}
        if self.mode == "no_ai":
            return 409, {"error": "no_recognizer", "display": ["No AI on server", "use phone"]}
        if self.mode == "down":
            return 0, {"error": "unreachable", "display": list(server_api.UNREACHABLE)}
        return 422, {"error": "nothing_read", "display": ["No match"]}

    def save(self, body):
        self.calls.append(("save", body))
        if self.mode == "save_fails":
            return 409, {"error": "tag_in_use", "display": ["Tag in use", "linked to another spool"]}
        sid = body.get("spool_id") or 124
        return 200, {"spool_id": sid, "created": "spool_id" not in body, "remaining_g": body["gross_g"] - 160,
                     "display": ["Saved", "Spool #%d" % sid]}


class Ser:
    port = "/dev/ttyFAKE"

    def __init__(self):
        self.lines: list[str] = []

    def write(self, data):
        self.lines += [ln for ln in data.decode().split("\n") if ln]


@pytest.fixture
def rig(monkeypatch):
    with st.lock:
        saved_state, saved_flow = dict(st.state), dict(st.flow)
        st.state.update(tag=None, tag_at=None, scale=None, scale_at=None, last=None, last_at=None, held=None,
                        held_at=None, blink_until=0, photo=None, photo_at=None, photos=[])
        st.flow.update(step="idle", seq=0, spool=None, filament=None, display=[], message="", at=0.0)
    ser = Ser()
    monkeypatch.setitem(st.panel, "ser", ser)
    monkeypatch.setitem(st.settings, "beep", True)
    monkeypatch.setitem(st.settings, "flow_timeout", 600)
    api = FakeApi()
    monkeypatch.setattr(flow, "api", lambda: api)
    monkeypatch.setattr(flow, "spawn", lambda fn, *args: fn(*args))
    monkeypatch.setattr(camera, "grab_photo", lambda: (JPEG, None))
    monkeypatch.setattr(station_scale, "find_scale", lambda: "/dev/hidraw0")  # the computer has a scale
    said = []
    monkeypatch.setattr(speech, "say", said.append)
    seen = panel.Shown()

    class Rig:
        pass
    r = Rig()
    r.api, r.ser, r.said, r.seen = api, ser, said, seen

    def show():
        st.panel_dirty.set()
        panel.refresh(seen, time.time())
    r.show = show
    r.lamp = lambda: (show(), [ln for ln in ser.lines if ln.startswith("L ")][-1])[1]
    r.row = lambda n: (show(), next((ln[4:] for ln in reversed(ser.lines) if ln.startswith("D %d" % n)), None))[1]
    r.step = lambda: flow.flow["step"]
    yield r
    with st.lock:
        st.state.clear()
        st.state.update(saved_state)
        st.flow.clear()
        st.flow.update(saved_flow)


def fk_paths(monkeypatch, tmp_path):
    """Every kit path under tmp_path: pairing files (server_api.save_pairing) never reach the real /etc."""
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir(exist_ok=True)
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))


def scale(grams, status="stable"):
    with st.lock:
        st.state.update(scale={"grams": grams, "status": status, "unit": 2}, scale_at=time.time())


def state_json():
    """GET /state through the real handler over st.view."""
    srv = ThreadingHTTPServer(("127.0.0.1", 0), station_http.make_handler(st.view, {"127.0.0.1"}))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/state", timeout=5) as r:
            return json.loads(r.read())
    finally:
        srv.shutdown()


def recognised_and_weighed(rig, grams=1098.0):
    nfc.on_tag("4:bb:bb")
    panel.on_line(b"L H")
    scale(grams)
    panel.on_line(b"P H")
    assert rig.step() == "ready"


# --- idle ------------------------------------------------------------------------------------------------------------

def test_idle_lamps_off_display_ready(rig):
    assert rig.lamp() == "L 0 0" and rig.row(0) == "Ready" and rig.row(1) == "Scan a spool's tag"


def test_yellow_with_no_tag_refused(rig):
    panel.on_line(b"P H")
    assert "B 400 250" in rig.ser.lines and rig.step() == "idle" and rig.row(4) == "Scan a tag first"
    panel.on_line(b"L H")
    assert rig.step() == "idle" and rig.api.calls == []


# --- a new tag: camera, recognition, weight, save ----------------------------------------------------------------------

def test_new_tag_goes_to_the_camera(rig):
    nfc.on_tag("4:bb:bb")
    assert "B 2200 90" in rig.ser.lines and rig.step() == "camera"
    assert rig.lamp() == "L 2 0" and rig.row(0) == "New Tag Scanned"
    assert rig.row(1) == "Yellow: take photo" and rig.row(2) == "Hold yellow: AI" and rig.row(3) == "Red: cancel"
    assert rig.api.calls == [("tag", "4:bb:bb")]
    assert rig.said[-1] == "New tag. Show the spool to the camera and press yellow to take a photo, or hold yellow to recognise."


def test_long_press_outside_ready_does_nothing(rig):
    nfc.on_tag(LINKED)
    panel.on_line(b"L H")
    assert rig.step() == "weigh" and not [c for c in rig.api.calls if c[0] != "tag"]


def test_short_press_yellow_at_camera_captures_and_accumulates_photos(rig):
    nfc.on_tag("4:bb:bb")
    panel.on_line(b"P H")
    assert rig.step() == "camera" and len(st.state["photos"]) == 1 and st.state["photo"] == JPEG
    assert rig.row(0) == "1 photo" and rig.row(1) == "Yellow: add photo" and rig.row(2) == "Hold yellow: AI"
    assert rig.row(3) == "Red: undo / Hold:quit" and rig.row(4) == ""
    assert rig.said[-1] == "Photo 1. Press yellow for another, or hold yellow to recognise."
    assert not [c for c in rig.api.calls if c[0] == "recognize"]

    panel.on_line(b"P H")
    assert rig.step() == "camera" and len(st.state["photos"]) == 2
    assert rig.row(0) == "2 photos" and rig.row(4) == ""
    assert rig.said[-1] == "Photo 2. Press yellow for another, or hold yellow to recognise."

    panel.on_line(b"L H")
    (_, photos, uid, during), = [c for c in rig.api.calls if c[0] == "recognize"]
    assert (photos, uid, during) == ([JPEG, JPEG], "4:bb:bb", "recognising")
    assert rig.step() == "weigh"


def test_camera_caps_at_max_station_photos(rig):
    nfc.on_tag("4:bb:bb")
    for _ in range(st.MAX_STATION_PHOTOS):
        panel.on_line(b"P H")
    assert rig.step() == "camera" and len(st.state["photos"]) == st.MAX_STATION_PHOTOS
    panel.on_line(b"P H")
    assert rig.step() == "camera" and len(st.state["photos"]) == st.MAX_STATION_PHOTOS
    assert rig.row(4) == f"Max {st.MAX_STATION_PHOTOS} photos"
    assert rig.said[-1] == "Maximum photos taken. Hold yellow to recognise."
    assert "B 400 250" in rig.ser.lines
    panel.on_line(b"L H")
    (_, photos, _, _), = [c for c in rig.api.calls if c[0] == "recognize"]
    assert len(photos) == st.MAX_STATION_PHOTOS and rig.step() == "weigh"


def test_yellow_at_camera_recognises(rig):
    nfc.on_tag("4:bb:bb")
    panel.on_line(b"L H")
    (_, photos, uid, during), = [c for c in rig.api.calls if c[0] == "recognize"]
    assert (photos, uid, during) == ([JPEG], "4:bb:bb", "recognising")  # yellow rapid while it runs
    assert flow.STEP_LAMPS["recognising"] == (4, 1)
    assert rig.step() == "weigh"
    assert rig.row(0) == "Recognised" and rig.row(1) == "Elegoo PLA Galaxy ..."  # cut to 21 characters with ellipsis
    assert rig.row(3) == "Press yellow: weigh" and rig.lamp() == "L 2 1"
    assert rig.said[-1] == "Recognised Elegoo PLA Galaxy Blue. Put it on the scale, or hold red to reject."
    s = state_json()
    assert s["flow"]["step"] == "weigh" and s["flow"]["filament"] == FIL and s["photo"]["url"] == "/photo"


def test_weighing_needs_a_settled_spool(rig):
    nfc.on_tag("4:bb:bb")
    panel.on_line(b"L H")
    panel.on_line(b"P H")
    assert rig.step() == "weigh" and rig.row(4) == "Scale not settled"
    scale(1098.0, "moving")
    panel.on_line(b"P H")
    assert rig.step() == "weigh"
    scale(12.0)  # under MIN_GRAMS: an empty pan
    panel.on_line(b"P H")
    assert rig.step() == "weigh"
    scale(1098.0)
    panel.on_line(b"P H")
    assert rig.step() == "ready" and rig.lamp() == "L 1 1"
    assert rig.row(0) == "1098 g" and rig.row(1) == "Recognised" and rig.row(3) == "Hold yellow: save"
    assert rig.said[-1] == "1098 grams. Hold yellow to save."
    s = state_json()
    assert s["last_weight"]["held"] and s["last_weight"]["grams"] == 1098.0 and s["tag"]["held"]


def test_yellow_again_at_ready_reweighs(rig):
    recognised_and_weighed(rig)
    scale(1101.0)
    panel.on_line(b"P H")
    assert rig.step() == "ready" and rig.row(0) == "1101 g"


def test_long_press_at_ready_saves_the_recognised_filament(rig):
    recognised_and_weighed(rig)
    panel.on_line(b"L H")
    assert rig.step() == "done" and rig.row(0) == "Saved" and rig.row(1) == "Spool #124" and rig.lamp() == "L 3 0"
    (_, body), = [c for c in rig.api.calls if c[0] == "save"]
    assert body == {"uid": "4:bb:bb", "gross_g": 1098.0, "filament": FIL}  # recognize's item, unchanged
    assert rig.said[-1] == "Saved."


def test_save_refused_goes_back_to_ready_with_the_reason(rig):
    recognised_and_weighed(rig)
    rig.api.mode = "save_fails"
    panel.on_line(b"L H")
    assert rig.step() == "ready" and rig.row(4) == "Tag in use" and rig.said[-1] == "Save failed."


# --- a linked tag: no camera -------------------------------------------------------------------------------------------

def test_linked_tag_skips_the_camera_and_saves_by_spool_id(rig):
    nfc.on_tag(LINKED)
    assert rig.step() == "weigh"
    assert (rig.row(0), rig.row(1), rig.row(2)) == ("Spool #123", "SUNLU PLA+ Black", "640 g left")
    assert rig.said[-1] == "Known spool. Put it on the scale."
    scale(1098.0)
    panel.on_line(b"P H")
    assert rig.step() == "ready"
    panel.on_line(b"L H")
    assert rig.step() == "done"
    assert [c for c in rig.api.calls if c[0] == "save"][-1][1] == {"uid": LINKED, "gross_g": 1098.0, "spool_id": 123}
    assert not [c for c in rig.api.calls if c[0] == "recognize"]


# --- red, and a new scan ---------------------------------------------------------------------------------------------------

def test_red_resets_from_anywhere(rig):
    nfc.on_tag(LINKED)
    scale(1098.0)
    panel.on_line(b"P H")
    panel.on_line(b"P D")
    assert rig.step() == "idle" and rig.lamp() == "L 0 3" and rig.row(0) == "Ready"
    assert "B 700 180" in rig.ser.lines and rig.said[-1] == "Cancelled."
    s = state_json()
    assert s["tag"]["nfc_id"] is None and s["last_weight"] is None and s["photo"] is None  # nothing held
    with st.lock:
        st.state["blink_until"] = 0
    assert rig.lamp() == "L 0 0"


def test_red_when_idle_only_buzzes(rig):
    panel.on_line(b"P D")
    assert "B 400 250" in rig.ser.lines and rig.said == [] and rig.step() == "idle"


def test_long_press_red_at_weigh_rejects_ai_match(rig):
    nfc.on_tag("4:bb:bb")
    panel.on_line(b"P H")
    panel.on_line(b"L H")
    assert rig.step() == "weigh" and flow.flow["filament"] == FIL and flow.flow["spool"] is None
    panel.on_line(b"L D")
    assert rig.step() == "camera" and rig.row(4) == "Match rejected, retry"
    assert st.state["photos"] == [] and st.state["photo"] is None and st.state["held"] is None
    assert flow.flow["filament"] is None and st.state["tag"] == "4:bb:bb"
    assert rig.said[-1] == "Match rejected. Take new photos."


def test_long_press_red_at_ready_rejects_ai_match(rig):
    recognised_and_weighed(rig)
    assert rig.step() == "ready" and st.state["held"] == 1098.0
    panel.on_line(b"L D")
    assert rig.step() == "camera" and rig.row(4) == "Match rejected, retry"
    assert st.state["photos"] == [] and st.state["photo"] is None and st.state["held"] is None
    assert flow.flow["filament"] is None and st.state["tag"] == "4:bb:bb"


def test_short_press_red_on_ai_match_cancels_to_idle(rig):
    recognised_and_weighed(rig)
    panel.on_line(b"P D")
    assert rig.step() == "idle" and st.state["tag"] is None and rig.said[-1] == "Cancelled."


def test_long_press_red_on_known_spool_cancels_to_idle(rig):
    nfc.on_tag(LINKED)
    assert rig.step() == "weigh" and flow.flow["spool"]["id"] == 123
    panel.on_line(b"L D")
    assert rig.step() == "idle" and st.state["tag"] is None and rig.said[-1] == "Cancelled."


def test_a_new_scan_restarts_the_flow(rig):
    recognised_and_weighed(rig)
    nfc.on_tag(LINKED)
    assert rig.step() == "weigh" and st.state["held"] is None and st.state["photo"] is None
    assert flow.flow["filament"] is None and flow.flow["spool"]["id"] == 123


# --- recognition can't help --------------------------------------------------------------------------------------------

def test_no_match_back_to_the_camera(rig):
    rig.api.mode = "no_match"
    nfc.on_tag("4:cc:cc")
    panel.on_line(b"L H")
    assert rig.step() == "camera" and rig.row(4) == "No match" and rig.said[-1] == "Not recognised. Try again."
    rig.api.mode = "down"
    panel.on_line(b"L H")
    assert rig.step() == "camera" and rig.row(4) == server_api.UNREACHABLE[0]


def test_no_recognizer_says_use_phone(rig):
    rig.api.mode = "no_ai"
    nfc.on_tag("4:cc:cc")
    panel.on_line(b"L H")
    assert rig.step() == "phone" and rig.row(0) == "Use phone" and rig.row(1) == "No AI on server"
    assert rig.row(2) == "" and rig.row(3) == "Red: start over" and rig.lamp() == "L 0 1"  # "use phone" dropped
    panel.on_line(b"P H")
    assert rig.step() == "phone"
    panel.on_line(b"P D")
    assert rig.step() == "idle"


def test_camera_missing_or_failing(rig, monkeypatch):
    nfc.on_tag("4:cc:cc")
    monkeypatch.setattr(camera, "grab_photo", lambda: (None, "camera failed"))
    panel.on_line(b"P H")
    assert rig.step() == "camera" and rig.row(4) == "Camera failed, retry"
    monkeypatch.setattr(camera, "grab_photo", lambda: (None, "no camera"))
    panel.on_line(b"P H")
    assert rig.step() == "phone" and rig.row(1) == "No camera"
    assert not [c for c in rig.api.calls if c[0] == "recognize"]


# --- no server -----------------------------------------------------------------------------------------------------------

def test_not_paired_or_unreachable_says_use_phone(rig, monkeypatch, tmp_path):
    fk_paths(monkeypatch, tmp_path)  # no pairing files: the real ServerApi answers without a call
    monkeypatch.setattr(flow, "api", lambda: server_api.ServerApi())
    nfc.on_tag("4:dd:dd")
    assert rig.step() == "phone" and rig.row(1) == "Not paired" and rig.said[-1] == "The server can't help. Use the phone."
    down = FakeApi()
    down.tag_lookup = lambda uid: (0, {"error": "unreachable", "display": list(server_api.UNREACHABLE)})
    monkeypatch.setattr(flow, "api", lambda: down)
    nfc.on_tag("4:dd:dd")
    assert rig.step() == "phone" and rig.row(1) == "Server offline" and rig.row(2) == "check the network"


# --- late answers, timeouts -----------------------------------------------------------------------------------------------

def test_a_late_recognition_after_red_is_dropped(rig):
    nfc.on_tag("4:ee:ee")
    rig.api.during = flow.on_red  # red pressed while the server reads the photo
    panel.on_line(b"L H")
    assert rig.step() == "idle" and flow.flow["filament"] is None and rig.said[-1] == "Cancelled."


def test_a_late_save_after_a_new_scan_is_dropped(rig):
    recognised_and_weighed(rig)
    real = rig.api.save
    rig.api.save = lambda body: (nfc.on_tag(LINKED), real(body))[1]
    panel.on_line(b"L H")
    assert rig.step() == "weigh" and flow.flow["spool"]["id"] == 123


def test_no_progress_goes_back_to_idle(rig):
    nfc.on_tag(LINKED)
    flow.flow_tick(time.time())
    assert rig.step() == "weigh"
    flow.flow_tick(time.time() + st.settings["flow_timeout"] + 1)
    assert rig.step() == "idle" and st.state["tag"] is None


def test_done_goes_back_to_idle_after_ten_seconds(rig):
    recognised_and_weighed(rig)
    panel.on_line(b"L H")
    flow.flow_tick(time.time() + 5)
    assert rig.step() == "done"
    flow.flow_tick(time.time() + flow.DONE_SECONDS + 1)
    assert rig.step() == "idle"


# --- the panel protocol ------------------------------------------------------------------------------------------------

def test_display_rows_sent_only_when_they_change(rig):
    rig.show()
    first = [ln for ln in rig.ser.lines if ln.startswith("D ")]
    assert first == ["D 0 Ready", "D 1 Scan a spool's tag", "D 2", "D 3", "D 4"]  # an empty row clears it
    rig.ser.lines.clear()
    rig.show()
    assert rig.ser.lines == ["L 0 0"]  # the lamps every time (the panel's watchdog), the rows only on change


def test_lamps_resent_every_second(rig):
    t = time.time()
    st.panel_dirty.clear()
    assert panel.refresh(rig.seen, t)
    rig.ser.lines.clear()
    assert panel.refresh(rig.seen, t + 0.5) and rig.ser.lines == []
    assert panel.refresh(rig.seen, t + 1.1) and rig.ser.lines == ["L 0 0"]


def test_refresh_reports_a_vanished_panel(rig):
    st.panel["ser"] = None
    assert panel.refresh(rig.seen, time.time()) is False


def test_screen_rows_are_ascii_and_cut():
    assert flow.ascii_text("Café Crème PLA, très très long nom") == "Cafe Creme PLA, tr..."
    assert len(flow.ascii_text("x" * 40, flow.TITLE_WIDTH)) == 16


# --- the api seam -------------------------------------------------------------------------------------------------------

def test_server_api_built_once_and_again_after_pairing(tmp_path, monkeypatch):
    fk_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(flow, "_api", {"key": None, "api": None})
    a = flow.api()
    assert flow.api() is a and not a.paired
    link = server_api.parse_link(f"fkit://pair?h=kit.example.com&p=8710&t={'t' * 43}&fp={'ab' * 32}&ip=192.0.2.5")
    server_api.save_pairing(link, owner=None)
    b = flow.api()
    assert b is not a and b.paired and b.ips == ["192.0.2.5"]
    assert b.timeout == st.settings["recognise_timeout"]
    os.utime(server_api.token_file(), ns=(1, 1))  # a new pairing rewrites the token
    assert flow.api() is not b
    assert config.paths().etc == tmp_path / "etc"


# --- fix round 1 ---------------------------------------------------------------------------------------------------

def test_no_server_call_when_the_step_change_was_dropped(rig, monkeypatch):
    """M3: save / recognise start only when go() moved the flow (a red press in between drops them)."""
    recognised_and_weighed(rig)
    started = []
    monkeypatch.setattr(flow, "spawn", lambda fn, *args: started.append(fn.__name__))
    monkeypatch.setattr(flow, "go", lambda *a, **kw: False)
    panel.on_line(b"L H")
    assert started == []
    with st.lock:
        st.flow["step"] = "camera"
    panel.on_line(b"L H")
    assert started == []


def test_a_stale_save_or_recognise_does_nothing(rig):
    """M3: save() and recognise() check the attempt under the lock before they call the server."""
    recognised_and_weighed(rig)
    old = flow.flow["seq"]
    flow.on_red()
    calls = len(rig.api.calls)
    flow.save(old)
    flow.recognise(old, "4:bb:bb")
    assert len(rig.api.calls) == calls and rig.step() == "idle"


def test_the_flow_times_out_without_a_panel(rig, monkeypatch):
    """M5: the flow's timer runs on its own thread, so a tap's flow (and /state's tag) still times out."""
    st.panel["ser"] = None
    nfc.on_tag(LINKED)
    with st.lock:
        st.flow["at"] -= st.settings["flow_timeout"] + 1
    rounds = []

    def one_round(s):
        rounds.append(s)
        if len(rounds) > 1:
            raise SystemExit
    monkeypatch.setattr(flow, "time", types.SimpleNamespace(sleep=one_round, time=time.time))   # this module's clock only
    with pytest.raises(SystemExit):
        flow.tick_loop()
    assert rig.step() == "idle" and st.state["tag"] is None and rounds[0] == 1


def test_main_starts_the_flow_timer(monkeypatch):
    from filament_kit.station import __main__ as station_main
    started = []

    class FakeThread:
        def __init__(self, target, args=(), kwargs=None, daemon=False):
            self.target = target

        def start(self):
            started.append(self.target)

    class FakeServer:
        def __init__(self, *a, **kw):
            made.append(a)

        def serve_forever(self):
            pass
    made = []
    monkeypatch.setattr(station_main, "threading", types.SimpleNamespace(Thread=FakeThread))
    # the start path: the station's own certificate, the HTTPS listener, then the hello announcing it to the Hub
    monkeypatch.setattr(station_main.st, "ensure_cert", lambda: ("cert.pem", "key.pem", "ab" * 32))
    monkeypatch.setattr(station_main.http, "tls_context", lambda cert, key: "context")
    monkeypatch.setattr(station_main.http, "TlsServer", FakeServer)
    monkeypatch.setattr(station_main, "signal", types.SimpleNamespace(SIGTERM=15, SIGINT=2, signal=lambda *a: None))
    monkeypatch.setattr(station_main.beep, "beep_device", lambda: None)
    monkeypatch.setattr(station_main.camera, "camera_dev", lambda: None)
    monkeypatch.setattr(station_main.flow, "api", lambda: types.SimpleNamespace(paired=True))
    station_main.main()
    assert flow.tick_loop in started
    assert made and made[0][0][1] == station_main.st.settings["port"] and made[0][2] == "context"   # HTTPS listener
    assert station_main.server_api.announce in started                                              # tells the Hub


# --- final fix wave: tag_not_linked keeps the spool, so the next long press can't add a second ----------------------

def test_tag_not_linked_keeps_the_spool_and_the_retry_sends_it(rig):
    recognised_and_weighed(rig)
    real = rig.api.save

    def not_linked(body):
        rig.api.calls.append(("save", body))
        rig.api.save = real
        return 502, {"error": "tag_not_linked", "spool_id": 124,
                     "display": ["Saved #124", "tag not linked:", "link it on the phone"]}
    rig.api.save = not_linked
    panel.on_line(b"L H")
    assert rig.step() == "ready" and rig.row(4) == "Saved #124"
    panel.on_line(b"L H")
    assert rig.step() == "done"
    saves = [c[1] for c in rig.api.calls if c[0] == "save"]
    assert len(saves) == 2 and saves[1] == {"uid": "4:bb:bb", "gross_g": 1098.0, "spool_id": 124}


def test_recognise_says_the_hubs_speak_text_when_there_is_one(rig, monkeypatch):
    real = rig.api.recognize

    def with_speak(photos, uid=None):
        status, r = real(photos, uid)
        return status, {**r, "speak": "Recognised Elegoo P L A Galaxy Blue"}
    monkeypatch.setattr(rig.api, "recognize", with_speak)
    nfc.on_tag("4:bb:bb")
    panel.on_line(b"L H")
    assert rig.row(1) == "Elegoo PLA Galaxy ..."  # the screen keeps "PLA"
    assert rig.said[-1] == "Recognised Elegoo P L A Galaxy Blue. Put it on the scale, or hold red to reject."


def test_an_older_hub_without_speak_is_spoken_from_the_display_lines(rig):
    nfc.on_tag("4:bb:bb")
    panel.on_line(b"L H")
    assert rig.said[-1] == "Recognised Elegoo PLA Galaxy Blue. Put it on the scale, or hold red to reject."


# --- a checked-out spool opens the weigh step in returning mode ---------------------------------------------

OUT = "4:cc:dd"
OUT_SPOOL = {**SPOOL, "checked_out": True, "home": "Rack 1", "slot": "H2D - AMS 1"}


def checked_out(rig, home="Rack 1", **extra):
    """A Hub that knows checked-out spools: the tag's answer, the save with `return`, and the return route (stubs of )."""
    sp = {**OUT_SPOOL, "home": home}
    real_lookup = rig.api.tag_lookup
    rig.api.returns = []

    def lookup(uid):
        if uid != OUT:
            return real_lookup(uid)
        rig.api.calls.append(("tag", uid))
        return 200, {"known": True, "uid": uid, "spool": sp,
                     "display": ["PLA+ Black", "Home: %s" % home if home else "Home: not recorded"]}

    def save(body):
        rig.api.calls.append(("save", body))
        if rig.api.mode == "not_out":
            return 409, {"error": "not_checked_out", "message": "This spool is not checked out."}
        return 200, {"spool_id": 123, "location": home, "display": ["Returned", "PLA+ Black"] + (
            ["to %s" % home] if home else []) + ["%d g left" % (body["gross_g"] - 160)], **extra}

    def give_back(uid, spool_id):
        rig.api.returns.append((uid, spool_id))
        rig.api.calls.append(("return", uid, spool_id))
        if rig.api.mode == "not_out":
            return 409, {"error": "not_checked_out", "message": "This spool is not checked out."}
        return 200, {"spool_id": spool_id, "location": home,
                     "display": ["Returned", "PLA+ Black"] + (["to %s" % home] if home else []), **extra}
    rig.api.tag_lookup, rig.api.save, rig.api.return_spool = lookup, save, give_back


def sent(rig):
    return [c for c in rig.api.calls if c[0] in ("save", "return")]


def test_a_checked_out_tag_opens_the_returning_step(rig):
    checked_out(rig)
    nfc.on_tag(OUT)
    assert rig.step() == "weigh" and flow.flow["returning"] is True and rig.lamp() == "L 2 1"
    assert [rig.row(n) for n in range(5)] == ["Returning", "PLA+ Black", "Home: Rack 1", "Yellow: weigh it",
                                              "Hold: skip weighing"]
    assert rig.said[-1] == "Checked out spool. Press yellow to weigh it, or hold yellow to skip weighing."


def test_a_checked_out_spool_with_no_home_says_not_recorded(rig):
    checked_out(rig, home=None)
    nfc.on_tag(OUT)
    assert rig.row(2) == "Home: not recorded"
    panel.on_line(b"L H")
    assert rig.step() == "done" and rig.said[-1] == "Returned."


def test_a_missing_display_is_built_from_the_spool(rig):
    checked_out(rig)
    inner = rig.api.tag_lookup
    rig.api.tag_lookup = lambda uid: (lambda s, r: (s, {**r, "display": []}))(*inner(uid))
    nfc.on_tag(OUT)
    assert (rig.row(1), rig.row(2)) == ("PLA+ Black", "Home: Rack 1")


def test_short_press_weighs_then_the_save_also_returns(rig):
    checked_out(rig)
    nfc.on_tag(OUT)
    panel.on_line(b"P H")
    assert rig.step() == "weigh" and rig.row(4) == "Scale not settled" and rig.row(0) == "Returning"
    scale(12.0)  # an empty pan: not a spool
    panel.on_line(b"P H")
    assert rig.step() == "weigh"
    scale(800.0)
    panel.on_line(b"P H")
    assert rig.step() == "ready" and rig.row(0) == "800 g" and rig.row(3) == "Hold yellow: save"
    assert rig.said[-1] == "800 grams. Hold yellow to save."
    panel.on_line(b"L H")
    assert rig.step() == "done" and rig.lamp() == "L 3 0"
    (_, body), = [c for c in rig.api.calls if c[0] == "save"]
    assert body == {"uid": OUT, "gross_g": 800.0, "spool_id": 123, "return": True}
    assert [rig.row(n) for n in range(5)] == ["Returned", "PLA+ Black", "to Rack 1", "640 g left", ""]
    assert rig.said[-1] == "Returned to Rack 1."
    assert rig.api.returns == []  # the weighed return is one call: save


def test_long_press_returns_without_weighing(rig):
    checked_out(rig)
    nfc.on_tag(OUT)
    panel.on_line(b"L H")
    assert rig.step() == "done" and rig.api.returns == [(OUT, 123)]
    assert not [c for c in rig.api.calls if c[0] == "save"]
    assert [rig.row(n) for n in range(5)] == ["Returned", "PLA+ Black", "to Rack 1", "", ""]  # no weight: no "g left"
    assert rig.said[-1] == "Returned to Rack 1."


def test_the_return_says_the_hubs_speak_text_when_there_is_one(rig):
    checked_out(rig, speak="Returned to Shelf A")
    nfc.on_tag(OUT)
    panel.on_line(b"L H")
    assert rig.said[-1] == "Returned to Shelf A"


def test_the_place_the_rules_chose_is_said(rig):
    """A spool with no home is placed by the rules; the answer's location is what the voice says."""
    checked_out(rig, home=None, location="Box 2")
    nfc.on_tag(OUT)
    panel.on_line(b"L H")
    assert rig.said[-1] == "Returned to Box 2."


def test_red_cancels_the_returning_step_and_nothing_is_sent(rig):
    checked_out(rig)
    nfc.on_tag(OUT)
    panel.on_line(b"P D")
    assert rig.step() == "idle" and flow.flow["returning"] is False and rig.said[-1] == "Cancelled."
    assert sent(rig) == []
    nfc.on_tag(LINKED)  # the next, plain spool is not in returning mode
    assert flow.flow["returning"] is False and rig.row(0) == "Spool #123" and rig.row(3) == "Press yellow: weigh"


def test_red_after_weighing_cancels_too(rig):
    checked_out(rig)
    nfc.on_tag(OUT)
    scale(800.0)
    panel.on_line(b"P H")
    panel.on_line(b"P D")
    assert rig.step() == "idle" and sent(rig) == []


def test_not_checked_out_shows_the_hubs_sentence_and_stays_on_the_step(rig):
    checked_out(rig)
    rig.api.mode = "not_out"
    nfc.on_tag(OUT)
    panel.on_line(b"L H")
    assert rig.step() == "weigh" and rig.row(0) == "Returning" and rig.row(4) == "This spool is not ..."  # cut to 21
    assert rig.said[-1] == "This spool is not checked out."
    scale(800.0)
    panel.on_line(b"P H")
    panel.on_line(b"L H")
    assert rig.step() == "ready" and rig.row(4) == "This spool is not ..."  # the weighed save, refused the same way


def test_an_older_hub_runs_the_plain_flow(rig):
    """No `checked_out` in the answer, so nothing changes."""
    nfc.on_tag(LINKED)
    assert flow.flow["returning"] is False and rig.row(0) == "Spool #123" and rig.row(3) == "Press yellow: weigh"
    panel.on_line(b"L H")  # a long press at weigh stays a refusal, as before
    assert rig.step() == "weigh" and sent(rig) == []
    scale(1098.0)
    panel.on_line(b"P H")
    panel.on_line(b"L H")
    assert [c for c in rig.api.calls if c[0] == "save"][-1][1] == {"uid": LINKED, "gross_g": 1098.0, "spool_id": 123}
    assert rig.said[-1] == "Saved."


def test_a_spool_that_is_not_checked_out_is_plain_even_when_the_hub_says_so(rig):
    sp = {**SPOOL, "checked_out": False, "home": None, "slot": None}
    rig.api.tag_lookup = lambda uid: (200, {"known": True, "uid": uid, "spool": sp,
                                            "display": ["Spool #123", "SUNLU PLA+ Black", "640 g left"]})
    nfc.on_tag(LINKED)
    assert flow.flow["returning"] is False and rig.row(0) == "Spool #123"


def test_a_late_return_after_a_new_scan_is_dropped(rig):
    checked_out(rig)
    real = rig.api.return_spool
    rig.api.return_spool = lambda uid, sid: (nfc.on_tag(LINKED), real(uid, sid))[1]
    nfc.on_tag(OUT)
    panel.on_line(b"L H")
    assert rig.step() == "weigh" and flow.flow["returning"] is False and flow.flow["spool"]["id"] == 123


# ---------- a Weigh Station without a scale ----------

def no_computer_scale(monkeypatch):
    monkeypatch.setattr(station_scale, "find_scale", lambda: None)


def panel_scale(grams, status="stable"):
    """What the panel service keeps for a panel's `W` lines (a stub of WL1): st.state["panel_scale"]."""
    with st.lock:
        st.state["panel_scale"] = {"grams": grams, "status": status, "unit": 2}


def test_no_scale_short_press_says_no_scale_here_and_changes_nothing(rig, monkeypatch):
    no_computer_scale(monkeypatch)
    nfc.on_tag(LINKED)
    panel.on_line(b"P H")
    assert rig.step() == "weigh" and rig.row(4) == "No scale here" and rig.said[-1] == "No scale here."
    assert st.state["held"] is None and rig.api.calls == [("tag", LINKED)]


def test_no_scale_in_returning_mode_short_press_answers_and_long_press_returns(rig, monkeypatch):
    no_computer_scale(monkeypatch)
    checked_out(rig)
    nfc.on_tag(OUT)
    panel.on_line(b"P H")
    assert rig.step() == "weigh" and flow.flow["returning"] is True and rig.said[-1] == "No scale here."
    assert [rig.row(n) for n in range(5)] == ["Returning", "PLA+ Black", "Home: Rack 1", "Yellow: weigh it",
                                              "No scale here"]
    assert sent(rig) == []
    panel.on_line(b"L H")
    assert rig.step() == "done" and rig.api.returns == [(OUT, 123)]
    assert rig.said[-1] == "Returned to Rack 1."


def test_no_scale_a_new_tag_goes_to_use_phone(rig, monkeypatch):
    no_computer_scale(monkeypatch)
    nfc.on_tag("4:ee:ee")
    assert rig.step() == "phone" and rig.lamp() == "L 0 1"
    assert [rig.row(n) for n in range(5)] == ["Use phone", "No scale here", "", "Red: start over", ""]
    assert rig.said[-1] == "No scale here. Use the phone."
    assert not [c for c in rig.api.calls if c[0] == "recognize"]


def test_the_scale_rows_are_ascii_within_title_16_and_rows_21(rig, monkeypatch):
    no_computer_scale(monkeypatch)
    checked_out(rig)
    for tag in ("4:ee:ee", OUT, LINKED):
        nfc.on_tag(tag)
        panel.on_line(b"P H")
        rows = [rig.row(n) for n in range(5)]
        assert len(rows[0]) <= 16 and all(len(r) <= 21 for r in rows)
        assert all(all(" " <= ch <= "~" for ch in r) for r in rows)


def test_a_sleeping_computer_scale_is_still_a_scale(rig):
    """The USB device is there but has sent no reading yet: the plain "not settled" answer, not "No scale here"."""
    nfc.on_tag(LINKED)
    panel.on_line(b"P H")
    assert rig.row(4) == "Scale not settled"
    nfc.on_tag("4:ee:ee")
    assert rig.step() == "camera"


def test_a_panel_scale_is_used_when_the_computer_has_none(rig, monkeypatch):
    no_computer_scale(monkeypatch)
    panel_scale(1098.0)
    nfc.on_tag("4:ee:ee")
    assert rig.step() == "camera"  # a scale exists: the new tag is recognised as usual
    panel.on_line(b"L H")
    assert rig.step() == "weigh"
    panel_scale(12.0)  # an empty pan
    panel.on_line(b"P H")
    assert rig.step() == "weigh" and rig.row(4) == "Scale not settled"
    panel_scale(1098.0)
    panel.on_line(b"P H")
    assert rig.step() == "ready" and rig.row(0) == "1098 g" and rig.said[-1] == "1098 grams. Hold yellow to save."
    panel.on_line(b"L H")
    assert rig.step() == "done" and [c for c in rig.api.calls if c[0] == "save"][-1][1]["gross_g"] == 1098.0


def test_a_panel_scale_weighs_a_checked_out_spool(rig, monkeypatch):
    no_computer_scale(monkeypatch)
    panel_scale(800.0)
    checked_out(rig)
    nfc.on_tag(OUT)
    panel.on_line(b"P H")
    assert rig.step() == "ready" and rig.row(0) == "800 g"


def test_the_computers_scale_wins_when_both_exist(rig):
    scale(900.0)
    panel_scale(500.0)
    nfc.on_tag(LINKED)
    panel.on_line(b"P H")
    assert rig.step() == "ready" and rig.row(0) == "900 g"
    scale(900.0, "moving")  # the computer's scale is unsettled: the panel's settled reading is not borrowed
    panel.on_line(b"P H")
    assert rig.step() == "ready" and rig.row(4) == "Scale not settled"


def test_a_device_with_no_reading_beats_the_panels_scale(rig):
    panel_scale(500.0)  # the computer's USB scale is plugged in (rig) but silent: it wins
    nfc.on_tag(LINKED)
    panel.on_line(b"P H")
    assert rig.step() == "weigh" and rig.row(4) == "Scale not settled"


def test_wrong_scale_unit_says_set_scale_to_grams(rig):
    st.state.update(scale={"grams": None, "status": "unit oz", "unit": 11, "stable": True}, scale_at=time.time())
    nfc.on_tag(LINKED)
    panel.on_line(b"P H")
    assert rig.step() == "weigh" and rig.row(4) == "Set scale to grams"
    assert rig.said[-1] == "Set the scale to grams."
