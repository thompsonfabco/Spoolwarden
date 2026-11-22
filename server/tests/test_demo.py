"""RC6: demo mode (R85 section 4): the sample set, the demo's own folder, the inventory behind Spoolman's client, the
pretend printers and the scripted alert, the routes and the command line, and that nothing real is reached."""
from __future__ import annotations

import json
import os
import socket
import urllib.request

import pytest

from filament_kit import demo, demo_cli, event_sink, output, watch
from filament_kit.bridge import loading, registry
from filament_kit.bridge.connection import PrinterOffline
from filament_kit.demo import inventory, printers, routes
from filament_kit.event_sink import StoreSink
from filament_kit.events import EventStore
from filament_kit.spoolman import Spoolman, SpoolmanError

DASH = "–"


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, mountable):
    for name in ("etc", "state", "opt", "backups"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    monkeypatch.setenv("FK_NO_SYSTEMD", "1")
    monkeypatch.setattr(event_sink, "runs", event_sink.RunBook())
    yield tmp_path
    event_sink.set_sink(None)


@pytest.fixture
def no_network(monkeypatch):
    """Every way out of the process fails the test: Spoolman's client, sockets, name lookups."""
    def refuse(*a, **k):
        raise AssertionError(f"the demo reached outside: {a[:2]}")
    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket.socket, "connect_ex", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)


class Empty:
    def get(self, path):
        return []


class Has:
    def __init__(self, spools):
        self.spools = spools

    def get(self, path):
        return self.spools


class Broken:
    def __init__(self, status):
        self.status = status

    def get(self, path):
        raise SpoolmanError("x", status=self.status)


def on():
    return demo.start(spoolman=Empty(), registry_load=lambda: {})


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def to(self, seconds):
        self.t = 1000.0 + seconds


# ---------------------------------------------------------------- the sample set

def test_the_sample_set_is_the_workshop_hub():
    inv = inventory.Inventory.parse(demo.SAMPLE.read_bytes())
    assert (len(inv.spools), len(inv.filaments), len(inv.vendors)) == (64, 23, 9)
    assert inv.locations == ["Sealed stock", "Rack 1", "Storage box", "Overflow"]
    assert inv.spools[187]["location"] == f"Workshop X1 {DASH} AMS A1" and inv.spools[187]["remaining_weight"] == 620
    assert inv.filaments[1]["name"] == "PolyTerra PLA Charcoal Black" and inv.filaments[1]["color_hex"] == "2B2B2E"
    assert json.loads(inv.filaments[1]["extra"]["nozzle_range"]) == "190-230"
    assert {s["filament_id"] for s in inv.spools.values()} <= set(inv.filaments)


def test_the_sample_printers():
    got = printers.registry_printers()
    assert [(p.name, p.family, p.configured) for p in got.values()] == [
        ("Workshop X1", "bambu", True), ("Garage Voron", "klipper", True), ("Study A1 mini", "bambu", True),
        ("mainsailos", "klipper", False)]
    assert got["00M00A000000001"].ip == "192.0.2.40" and got["garage-voron"].host == "voron.example"


# ---------------------------------------------------------------- switching on and off

def test_off_by_default_and_state_says_so(env):
    assert not demo.is_on() and demo.state() == {"demo": False} and routes.hub_state() == {"demo": False}


def test_start_makes_the_folder_and_nothing_else(env):
    before = sorted(p.name for p in (env / "state").iterdir())
    assert on() == {"demo": True}
    assert demo.is_on() and demo.state() == {"demo": True}
    assert sorted(p.name for p in (env / "state").iterdir()) == sorted(before + ["demo"])
    assert {p.name for p in (env / "state" / "demo").iterdir()} == {"demo.xml", "inventory.xml", "usage.xml"}
    assert demo.started() is not None
    assert on() == {"demo": True}                      # already on: a plain yes


def test_leave_deletes_the_folder_and_brings_the_hub_back(env):
    before = sorted(p.name for p in (env / "state").iterdir())
    on()
    assert demo.leave() == {"demo": False}
    assert not demo.is_on() and not (env / "state" / "demo").exists()
    assert sorted(p.name for p in (env / "state").iterdir()) == before
    with pytest.raises(demo.NotDemo):
        demo.leave()


@pytest.mark.parametrize("spoolman,printers_,why", [
    (Has([{"id": 1}]), {}, "spools"),
    (Empty(), {"S1": registry.Printer("S1", configured=True)}, "printers"),
    (Broken(500), {}, "spools"),
])
def test_only_on_an_empty_hub(env, spoolman, printers_, why):
    assert demo.real_data(spoolman, lambda: printers_) == why
    with pytest.raises(demo.NotEmpty) as e:
        demo.start(spoolman=spoolman, registry_load=lambda: printers_)
    assert e.value.message == "The demo is for an empty Hub. This Hub has real data."
    assert not demo.is_on() and not (env / "state" / "demo").exists()


def test_a_found_printer_or_no_spoolman_does_not_count(env):
    found = {"S1": registry.Printer("S1", configured=False)}
    assert demo.real_data(Empty(), lambda: found) is None
    assert demo.start(spoolman=Empty(), registry_load=lambda: found) == {"demo": True}


def test_an_unreachable_spoolman_is_not_an_empty_hub(env, monkeypatch, capsys):
    with pytest.raises(demo.CannotTell) as e:
        demo.real_data(Broken(None), lambda: {})
    assert e.value.code == "cannot_tell_if_empty" and e.value.message == demo.CANNOT_TELL
    with pytest.raises(demo.CannotTell):
        demo.start(spoolman=Broken(None), registry_load=lambda: {})
    assert not demo.is_on() and not (env / "state" / "demo").exists()
    monkeypatch.setattr(demo, "real_data", lambda *a: (_ for _ in ()).throw(demo.CannotTell()))
    assert routes.post_demo({"on": True}) == (409, {"error": demo.CANNOT_TELL, "code": "cannot_tell_if_empty"})
    assert demo_cli.run_demo(Args("on")) == 1
    assert demo.CANNOT_TELL in " ".join(capsys.readouterr().err.split())      # the command line wraps the sentence


def test_the_real_registry_is_read_when_none_is_given(env):
    registry.set_code("SER1", "12345678", "H2D")
    assert demo.real_data(Empty()) == "printers"


def test_a_marker_that_is_a_link_is_no_demo(env):
    (env / "state" / "demo").mkdir()
    target = env / "elsewhere.xml"
    target.write_text("<demo/>")
    try:
        os.symlink(target, env / "state" / "demo" / "demo.xml")
    except (OSError, NotImplementedError):
        pytest.skip("no symbolic links here")
    assert not demo.is_on()


# ---------------------------------------------------------------- the routes

def test_the_route_list():
    got = [(r.method, r.path, r.who, r.web_token) for r in routes.ROUTES]
    assert got == [("POST", "/setup/api/demo", "page", False), ("POST", "/api/demo/leave", "paired", True),
                   ("POST", "/intake/api/demo/leave", "web", False)]


def test_the_routes_are_mounted_only_while_the_demo_is_mountable(monkeypatch):
    monkeypatch.setattr(demo, "MOUNTABLE", False)
    assert routes.mountable() == []
    monkeypatch.setattr(demo, "MOUNTABLE", True)
    assert routes.mountable() == routes.ROUTES


def test_post_demo(env, monkeypatch):
    assert routes.post_demo({"on": "yes"})[0] == 400 and routes.post_demo({})[0] == 400
    monkeypatch.setattr(demo, "real_data", lambda *a: "spools")
    assert routes.post_demo({"on": True}) == (409, {"error": demo.NOT_EMPTY, "code": "not_empty"})
    assert routes.post_demo({"on": 1}) == (400, {"error": routes.BAD_ON, "code": "bad_request"})
    assert routes.post_leave({})[1] == {"error": "not_in_demo", "message": "The demo is not on."}    # the kit's shape stays
    monkeypatch.setattr(demo, "real_data", lambda *a: None)
    assert routes.post_demo({"on": True}) == (200, {"demo": True})
    assert routes.post_demo({"on": False}) == (200, {"demo": False})
    assert routes.post_demo({"on": False}) == (200, {"demo": False})      # off while off: harmless


def test_leave_route(env):
    assert routes.post_leave({}) == (409, {"error": "not_in_demo", "message": "The demo is not on."})
    on()
    assert routes.post_leave({}) == (200, {"demo": False})
    assert routes.outside() == (409, {"error": "in_demo", "message": "Not in the demo."})


def test_the_stop_raises_only_in_the_demo(env):
    demo.stop()
    on()
    with pytest.raises(demo.NotInDemo) as e:
        demo.stop()
    assert str(e.value) == "Not in the demo."


def test_where_gives_the_demos_own_place(env):
    real = env / "state" / "events"
    assert demo.where(real, "events") == real
    on()
    assert demo.where(real, "events") == env / "state" / "demo" / "events"


# ---------------------------------------------------------------- Spoolman's client over the demo inventory

def test_the_client_answers_from_the_demo_and_never_the_network(env, no_network):
    on()
    sm = Spoolman("http://127.0.0.1:1")
    assert sm.ping() == "demo"
    spools = sm.get("spool")
    assert len(spools) == 64 and spools[0]["filament"]["vendor"]["name"]
    s187 = sm.get("spool/187")
    assert s187["remaining_weight"] == 620 and s187["used_weight"] == 380
    assert s187["location"] == f"Workshop X1 {DASH} AMS A1"
    assert len(sm.get("filament")) == 23 and len(sm.get("vendor")) == 9
    assert sm.missing_fields() == [] and sm.ensure_fields() == []


def test_the_client_is_the_real_one_while_the_demo_is_off(env, monkeypatch):
    seen = []

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"version": "1.2"}'
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: seen.append(req.full_url) or Resp())
    assert Spoolman("http://spoolman.test:7912").ping() == "1.2" and seen == ["http://spoolman.test:7912/api/v1/info"]


def test_writes_go_to_the_demo_inventory(env, no_network):
    on()
    sm = Spoolman("http://127.0.0.1:1")
    v = sm.post("vendor", {"name": "Acme", "empty_spool_weight": 120})
    f = sm.post("filament", {"name": "PLA Red", "vendor_id": v["id"], "material": "PLA", "color_hex": "FF0000",
                             "weight": 1000, "spool_weight": 120, "extra": {"line": json.dumps("Acme PLA")}})
    s = sm.post("spool", {"filament_id": f["id"], "remaining_weight": 900, "location": "Rack 1", "comment": "hi"})
    assert s["id"] == 215 and s["filament"]["vendor"]["name"] == "Acme" and s["used_weight"] == 100
    patched = sm.patch(f"spool/{s['id']}", {"location": "Overflow", "extra": {"nfc_id": json.dumps("04AA")}})
    assert patched["location"] == "Overflow"
    assert sm.get(f"spool/{s['id']}")["extra"] == {"nfc_id": '"04AA"'}
    assert sm.use(s["id"], 50)["remaining_weight"] == 850
    assert sm.use(s["id"], -10)["remaining_weight"] == 860                     # an undo gives grams back
    assert sm.use(s["id"], 5000)["remaining_weight"] == 0
    sm.patch(f"spool/{s['id']}", {"archived": True})
    assert s["id"] not in [x["id"] for x in sm.get("spool")]
    assert s["id"] in [x["id"] for x in sm.get("spool?allow_archived=true")]
    sm.post("field/spool/nfc_id", {"name": "nfc_id", "order": 0, "field_type": "text"})
    # the file on disk is XML and a new process reads the same data
    assert (env / "state" / "demo" / "inventory.xml").read_bytes().lstrip().startswith(b"<?xml")
    assert len(inventory.load().spools) == 65


def test_errors_have_spoolmans_shapes(env, no_network):
    on()
    sm = Spoolman("http://127.0.0.1:1")
    for call, status in ((lambda: sm.get("spool/9999"), 404), (lambda: sm.get("nothing"), 404),
                         (lambda: sm.post("spool", {"filament_id": 999}), 422),
                         (lambda: sm.patch("spool/151", {"remaining_weight": "x"}), 422)):
        with pytest.raises(SpoolmanError) as e:
            call()
        assert e.value.status == status


def test_a_client_made_after_leaving_is_the_real_one_again(env, monkeypatch):
    on()
    demo.leave()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(SpoolmanError) as e:
        Spoolman("http://127.0.0.1:1").get("spool")
    assert str(e.value) == "spoolman_unreachable"


def test_a_vanished_folder_reads_as_unreachable_not_a_crash(env):
    on()
    (env / "state" / "demo" / "inventory.xml").unlink()
    with pytest.raises(SpoolmanError) as e:
        Spoolman("http://x").get("spool")
    assert e.value.status is None


# ---------------------------------------------------------------- the pretend printers

@pytest.fixture
def fams(env):
    on()
    clock = Clock()
    sm = Spoolman("http://127.0.0.1:1")
    return printers.Family("bambu", sm, clock), printers.Family("klipper", sm, clock), clock, sm


def test_the_x1_is_printing_at_42_percent(fams):
    b, k, clock, sm = fams
    st = b.status("00M00A000000001")
    assert (st["gcode_state"], st["job"], st["percent"], st["layer"], st["total_layers"]) == (
        "RUNNING", "bracket-v3.3mf", 42, 95, 228)
    assert st["temps"]["nozzle"][0]["current"] == 220 and st["temps"]["bed"]["current"] == 65 and st["light"] == "on"
    assert [s["label"] for s in st["ams"][0]["slots"]] == ["A1", "A2", "A3", "A4"]
    assert [s["spool"]["id"] if s["spool"] else None for s in st["ams"][0]["slots"]] == [187, 198, 176, None]
    assert b.summary("00M00A000000001") == {"gcode_state": "RUNNING", "percent": 42, "remaining_min": 72,
                                            "job": "bracket-v3.3mf"}
    assert b.state("00M00A000000001") == "connected" and b.state("03000A000000002") == "offline"
    assert b.status("03000A000000002")["gcode_state"] == "UNKNOWN"


def test_progress_follows_the_clock(fams):
    b, k, clock, sm = fams
    clock.to(60 * 60)                                      # an hour of the 72 minutes left: nearly done
    assert 90 <= b.status("00M00A000000001")["percent"] <= 100
    clock.to(73 * 60)                                      # the 72 minutes are over
    assert b.status("00M00A000000001")["gcode_state"] == "FINISH"
    assert printers.usage_entries("00M00A000000001", 30)[0]["grams"] == 18.0       # the whole print is recorded
    clock.to(73 * 60 + printers.IDLE_AFTER_END + 1)
    assert b.status("00M00A000000001")["gcode_state"] == "IDLE"
    clock.to(73 * 60 + printers.IDLE_AFTER_END + printers.RESTART_AFTER + 2)
    assert b.status("00M00A000000001")["gcode_state"] == "RUNNING"                 # and it prints again


def test_pause_resume_stop_and_the_filament_used_is_recorded(fams):
    b, k, clock, sm = fams
    pid = "00M00A000000001"
    assert b.command(pid, "pause")["ok"] and b.summary(pid)["gcode_state"] == "PAUSE"
    assert b.command(pid, "resume")["ok"] and b.summary(pid)["gcode_state"] == "RUNNING"
    assert b.command(pid, "light_off")["ok"] and b.status(pid)["light"] == "off"
    before = sm.get("spool/187")["remaining_weight"]
    assert b.command(pid, "stop")["ok"] and b.summary(pid)["gcode_state"] == "FAILED"
    used = printers.usage_entries(pid, 30)
    assert used[0]["grams"] == 7.6 and used[0]["spool_id"] == 187 and used[0]["slot"] == "A1"     # 42 % of 18 g
    assert sm.get("spool/187")["remaining_weight"] == round(before - 7.6, 3)
    assert b.command(pid, "pause")["ok"] is False            # nothing to pause now: refused with a reason
    with pytest.raises(ValueError):
        b.command(pid, "explode")
    with pytest.raises(PrinterOffline):
        b.command("03000A000000002", "pause")


def test_the_earlier_prints_are_in_the_usage_log(fams):
    b, k, clock, sm = fams
    got = printers.usage_entries("00M00A000000001", 30)
    assert [(e["grams"], e["spool_id"], e["slot"]) for e in got] == [(18.4, 187, "A1")]
    assert [e["grams"] for e in printers.usage_entries("garage-voron", 30)] == [42.0]
    assert printers.usage_entries("garage-voron", 1) == []


def test_load_and_unload_a_slot(fams):
    b, k, clock, sm = fams
    pid = "00M00A000000001"
    assert b.command(pid, "pause")["ok"]
    dry = b.load(sm, pid, "A4", 151, True)
    assert dry["dry_run"] and sm.get("spool/151")["location"] != f"Workshop X1 {DASH} AMS A4"
    r = b.load(sm, pid, "A4", 151, False)
    assert r["confirmed"] is True and sm.get("spool/151")["location"] == f"Workshop X1 {DASH} AMS A4"
    assert b.status(pid)["ams"][0]["slots"][3]["spool"]["id"] == 151
    b.load(sm, pid, "A4", 152, False)                                   # a second spool: the first goes back
    assert sm.get("spool/151")["location"] != f"Workshop X1 {DASH} AMS A4"
    u = b.unload(sm, pid, "A4", False)
    assert u["spool"]["id"] == 152 and sm.get("spool/152")["location"] != f"Workshop X1 {DASH} AMS A4"
    with pytest.raises(loading.NotFound):
        b.unload(sm, pid, "A4", False)
    with pytest.raises(loading.NotFound):
        b.load(sm, pid, "Z9", 151, False)
    with pytest.raises(PrinterOffline):
        b.load(sm, "03000A000000002", "A1", 151, False)


def test_the_slot_feeding_a_running_print_cannot_be_loaded(fams):
    b, k, clock, sm = fams
    with pytest.raises(loading.SlotInUse):
        b.load(sm, "00M00A000000001", "A1", 151, False)


def test_no_camera(fams):
    b, k, clock, sm = fams
    assert b.camera_kind("00M00A000000001") is None and b.camera_url("00M00A000000001") is None
    assert demo.NO_CAMERA == "No camera in the demo."


def test_the_klipper_script_and_the_scripted_alert(fams, env):
    b, k, clock, sm = fams
    pid = "garage-voron"
    assert k.live(pid)["print"] == "standby" and k.status(pid)["gcode_state"] == "IDLE"
    assert k.status("mainsailos") is None and k.state("mainsailos") == "not_configured"
    store = EventStore(demo.recorded("events"))
    event_sink.set_sink(StoreSink(store))
    w = watch.Watcher(sink=StoreSink(store), clock=clock).attach(klipper=k)
    k.tick()
    clock.to(printers.KLIPPER_PRINT_AT + 5)
    k.tick()
    assert k.live(pid)["print"] == "printing" and k.status(pid)["job"] == "gear-housing.gcode"
    assert store.read()["events"] == []                                   # the first sight writes nothing
    clock.to(printers.ALERT_AT - 1)
    k.tick()
    assert k.live(pid)["print"] == "printing" and store.read()["events"] == []
    clock.to(printers.ALERT_AT + 1)
    k.tick()
    ev = store.read()["events"]
    assert [e["kind"] for e in ev] == ["print.runout"]                    # "Filament ran out", on the Klipper printer
    assert ev[0]["subject"]["id"] == pid and ev[0]["data"]["slot"] == "T0" and store.is_raised(f"print-paused:{pid}")
    assert k.live(pid)["sensors"] == {printers.RUNOUT_SENSOR: False}
    k.command(pid, "resume")
    assert k.live(pid)["print"] == "printing" and k.live(pid)["sensors"] == {printers.RUNOUT_SENSOR: True}
    clock.to(printers.ALERT_AT + 600)
    k.tick()
    assert [e["kind"] for e in store.read()["events"]] == ["print.runout", "print.resumed"]     # said once
    w.stop()


def test_the_alert_starts_again_when_the_demo_restarts(fams):
    b, k, clock, sm = fams
    clock.to(printers.ALERT_AT + 1)
    k.tick()
    assert k.live("garage-voron")["print"] == "paused"
    clock.to(500)
    printers.rearm_all()
    assert k.live("garage-voron")["print"] == "standby"
    clock.to(500 + printers.ALERT_AT + 1)
    k.tick()
    assert k.live("garage-voron")["print"] == "paused"


def test_the_switch_is_the_real_family_until_the_demo_is_on(env):
    class Real:
        def __init__(self):
            self.heard, self.started = [], 0

        def add_listener(self, fn):
            self.heard.append(fn)

        def start(self):
            self.started += 1

        def stop(self):
            self.started -= 1

        def state(self, pid):
            return "real"
    real = Real()
    pretend = printers.Family("klipper", Spoolman("http://x"), Clock())
    sw = printers.Switch(real, pretend)
    heard = []
    sw.add_listener(heard.append)
    assert real.heard == [heard.append] and heard.append in pretend._listeners
    assert sw.state("garage-voron") == "real"
    sw.start()
    sw.stop()
    assert real.started == 0
    on()
    assert sw.state("garage-voron") == "connected" and sw.state("nothing") == "not_configured"
    demo.leave()
    assert sw.state("garage-voron") == "real"
    with pytest.raises(AttributeError):
        sw.__wrapped__


def test_the_pretend_printers_tell_nobody_while_the_demo_is_off(env):
    clock = Clock()
    f = printers.Family("klipper", Spoolman("http://x"), clock)
    heard = []
    f.add_listener(lambda *a: heard.append(a))
    clock.to(printers.ALERT_AT + 1)
    f.tick()
    assert heard == [] and f.p["garage-voron"].gcode == "IDLE"


def test_the_thread_moves_time_and_stops(env):
    on()
    f = printers.Family("klipper", Spoolman("http://x"))
    f.start()
    f.stop()
    assert f._thread is None


# ---------------------------------------------------------------- the command line

class Args:
    def __init__(self, cmd=None, yes=False):
        self.demo_cmd, self.yes = cmd, yes


def test_status_on_off(env, capsys, monkeypatch):
    assert demo_cli.run_demo(Args()) == 0 and capsys.readouterr().out.strip() == "The demo is off."
    monkeypatch.setattr(demo, "real_data", lambda *a: None)
    assert demo_cli.run_demo(Args("on")) == 0
    out = capsys.readouterr().out
    assert out.startswith("The demo is on. Look around on the Hub page:") and "https://" in out
    assert "/setup" in out and "sudo spoolwarden demo off" in out
    assert demo_cli.run_demo(Args("status")) == 0 and capsys.readouterr().out.strip() == "The demo is on."
    assert demo_cli.run_demo(Args("off", yes=True)) == 0
    assert capsys.readouterr().out.strip() == "The demo is off." and not demo.is_on()
    assert demo_cli.run_demo(Args("off", yes=True)) == 1
    assert "The demo is not on." in capsys.readouterr().err


def test_on_refuses_a_hub_with_data(env, capsys, monkeypatch):
    monkeypatch.setattr(demo, "real_data", lambda *a: "spools")
    assert demo_cli.run_demo(Args("on")) == 1
    assert "Error: The demo is for an empty Hub. This Hub has real data." in capsys.readouterr().err


def test_off_asks_once_with_the_dialogs_words(env, monkeypatch):
    on()
    asked = []
    real = output.confirm

    def confirm(question, **k):
        asked.append(question)
        return real(question, ask=lambda prompt: "n", **k)
    monkeypatch.setattr(output, "confirm", confirm)
    assert demo_cli.run_demo(Args("off")) == 5
    assert asked == ["Leave the demo? Everything in it is deleted."] and demo.is_on()


def test_demo_data_line(env, capsys):
    demo_cli.announce()
    assert capsys.readouterr().out == ""
    on()
    demo_cli.announce()
    assert capsys.readouterr().out == "Demo data.\n"


def test_demo_data_line_with_quiet_and_json(env, capsys):
    on()
    output.configure(quiet=True)
    try:
        demo_cli.announce()
        assert capsys.readouterr().out == "Demo data.\n"                 # also with --quiet
        output.configure(quiet=False, json=True)
        demo_cli.announce()
        cap = capsys.readouterr()
        assert cap.out == "" and "Demo data." in cap.err                   # --json: standard error
    finally:
        output.configure(quiet=False, json=False)


def test_the_parser(env):
    import argparse
    p = argparse.ArgumentParser()
    demo_cli.add_parsers(p.add_subparsers(dest="cmd"))
    assert p.parse_args(["demo"]).demo_cmd is None
    assert p.parse_args(["demo", "on"]).demo_cmd == "on"
    a = p.parse_args(["demo", "off", "-y"])
    assert a.demo_cmd == "off" and a.yes


def test_on_and_off_need_root_without_the_test_switch(env, monkeypatch, capsys):
    monkeypatch.delenv("FK_NO_SYSTEMD")
    from filament_kit import cli
    monkeypatch.setattr(cli, "_is_root", lambda: False)
    monkeypatch.setattr(cli, "_invoked", [])                  # what an earlier test's main() left would name its command
    assert demo_cli.run_demo(Args("on")) == 3 and not demo.is_on()
    assert demo_cli.run_demo(Args("off")) == 3
    assert "sudo spoolwarden demo on" in capsys.readouterr().err
