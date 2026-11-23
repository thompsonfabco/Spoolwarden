"""/ the stops that live in the modules that reach outside, and the wiring: while the demo is on
no connection is made, no release is fetched, no backup is made or restored, the root helper changes nothing, a Tag
Reader gets no firmware or token, and what the Hub records lands in the demo's folder."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from filament_kit import __main__ as main_mod
from filament_kit import setup_code
from filament_kit import alerts, backup, demo, event_sink, events, reader_tokens, release, setup_helper, storage
from filament_kit.bridge import printer_report, registry
from filament_kit.bridge.connection import Manager
from filament_kit.bridge.klipper.manager import KlipperFamily


class Empty:
    def get(self, path):
        return []


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch, mountable):
    for name in ("etc", "state", "opt", "backups"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    monkeypatch.setenv("FK_NO_SYSTEMD", "1")
    monkeypatch.setenv("FK_SETUP_RUN", str(tmp_path / "run"))
    monkeypatch.setattr(event_sink, "runs", event_sink.RunBook())
    yield tmp_path
    event_sink.set_sink(None)


def on():
    return demo.start(spoolman=Empty(), registry_load=lambda: {})


def test_no_connection_is_made_in_the_demo(env):
    """The real registry holds a printer of each family: with the demo on, sync still connects nothing."""
    registry.add_bambu("00M00A000000001", "X1", "X1C", "192.0.2.9", "12345678")
    registry.add_klipper("Voron", "192.0.2.30")
    on()
    made = []
    m = Manager(client_factory=lambda *a, **k: made.append(a) or SimpleNamespace(), port=1)
    m.sync()
    assert made == [] and m._conns == {}
    klipper = KlipperFamily(Empty())
    klipper._started = True
    klipper.sync()
    assert klipper._conns == {}


def test_discovery_and_listeners_stay_off_in_the_demo(env):
    on()
    seen = []
    main_mod.discovery_callback(SimpleNamespace(state=lambda s: seen.append(s)))(
        {"serial": "00M00A000000001", "ip": "192.0.2.5"})
    assert seen == [] and not (env / "state" / "printers.xml").exists()
    assert isinstance(main_mod._browser(lambda *a: None), main_mod._NoBrowser)


def test_the_lifespan_starts_no_listener_in_the_demo(env):
    on()
    built = []
    manager = SimpleNamespace(start=lambda: None, stop=lambda: None)
    life = main_mod.printer_lifespan(
        manager, listener_factory=lambda cb: built.append(cb) or SimpleNamespace(start=lambda: None))

    async def run():
        async with life(None):
            pass
    asyncio.run(run())
    assert built == []


def test_a_release_is_not_fetched_in_the_demo(env):
    class Opener:
        def open(self, *a, **k):
            raise AssertionError("reached out")
    f = release.Fetcher(opener=Opener())
    assert release.NOT_IN_DEMO == demo.NOT_IN_DEMO
    with pytest.raises(AssertionError, match="reached out"):          # off: it would reach out
        f.get("https://api.github.com/x", 10)
    on()
    with pytest.raises(release.ReleaseError) as e:
        f.get("https://api.github.com/x", 10)
    assert str(e.value) == demo.NOT_IN_DEMO


def test_get_sh_carries_the_release_file():
    root = Path(__file__).resolve().parents[2]
    assert "def _demo_on()" in (root / "get.sh").read_text(encoding="utf-8")


def test_no_backup_is_made_or_restored_in_the_demo(env):
    on()
    with pytest.raises(backup.InDemo) as e:
        backup.create(env / "backups")
    assert str(e.value) == demo.NOT_IN_DEMO and list((env / "backups").iterdir()) == []
    said = []
    assert backup.restore(env / "x.zip", yes=True, out=said.append) == 1 and said == [f"Error: {demo.NOT_IN_DEMO}"]
    with pytest.raises(backup.InDemo):
        backup.pin_upload()


def test_the_root_helper_changes_nothing_in_the_demo(env):
    setup_code.write("12345678")
    on()
    for op in ("apply", "backup_keep", "module", "setting", "trust_status", "backup_last", "journal"):
        r = setup_helper.handle({"op": op, "code": "12345678"})
        assert (r["ok"], r["status"], r["error"], r["code"]) == (False, 409, demo.NOT_IN_DEMO, "in_demo"), op
    r = setup_helper.handle({"op": "check", "code": "12345678"})
    assert r.get("code") != "in_demo"                          # the page's own unlock is not stopped


def test_the_tag_reader_gets_no_token_in_the_demo(env):
    store = reader_tokens.TokenStore()
    tid, _ = store.issue("one")
    on()
    with pytest.raises(reader_tokens.TokenFileError, match="Not in the demo"):
        store.issue("two")
    with pytest.raises(reader_tokens.TokenFileError):
        store.revoke(tid)
    assert [t["id"] for t in store.list()] == [tid]


def test_the_web_readers_firmware_scan_and_hand_over_stop_in_the_demo(env):
    from filament_kit.web import reader as web_reader
    r = web_reader.Reader(SimpleNamespace(kit_bridge=lambda: None), None)
    on()
    for call in (r.fw, r.scan):
        out = asyncio.run(call(SimpleNamespace(path_params={"name": "firmware.bin"})))
        assert out.status_code == 409 and json.loads(out.body)["message"] == demo.NOT_IN_DEMO
    r._handing.acquire()
    r._hand_over()                                             # records nothing, and lets the next one in
    assert r._handing.acquire(blocking=False)


def test_the_hub_records_in_the_demo_folder(env):
    state = env / "state"
    assert alerts._path() == state / alerts.FILE and storage._file() == state / "storage.xml"
    assert events.EventStore().root == state / "events"
    on()
    d = state / "demo"
    assert alerts._path() == d / alerts.FILE and storage._file() == d / "storage.xml"
    assert events.EventStore().root == d / "events"
    assert printer_report._folder("S1") == d / "printers" / "S1"
    demo.leave()
    assert alerts._path() == state / alerts.FILE


def test_the_package_data_holds_the_demo_sample():
    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert '"demo/*.xml"' in text and (root / "filament_kit" / "demo" / "sample.xml").is_file()


def test_the_presets_cannot_be_changed_in_the_demo(env):
    from filament_kit import profiles_api
    from filament_kit.profiles.errors import ProfileError
    seen = []

    async def fn(request):
        seen.append(1)
        return "ok"
    guarded = profiles_api._no_writes_in_demo(fn)
    post, get = SimpleNamespace(method="POST"), SimpleNamespace(method="GET")
    assert asyncio.run(guarded(post)) == "ok"
    on()
    assert asyncio.run(guarded(get)) == "ok"
    with pytest.raises(ProfileError) as e:
        asyncio.run(guarded(post))
    assert (e.value.status, e.value.code, e.value.message) == (409, "in_demo", demo.NOT_IN_DEMO) and seen == [1, 1]

def test_main_wires_the_real_and_the_pretend_family_through_a_switch(env, monkeypatch):
    """`main()` itself (the server not served): the printers' family is a Switch that follows the demo's marker at each call
    (real Manager off, pretend family on), the camera hub is the one app and lifespan share."""
    from filament_kit import config, modules
    from filament_kit.bridge import camera
    from filament_kit.demo import printers as demo_printers
    cfg = config.load()
    cfg["modules"] = {n: n in {*modules.MODES["server"], "klipper"} for n in modules.MODULES}
    config.save(cfg)
    seen = {}
    real_create = main_mod.create_app
    monkeypatch.setattr(main_mod, "create_app", lambda *a, **kw: seen.update(printers=kw.get("printers"),
                                                                         hub=kw.get("camera_hub")) or real_create(*a, **kw))
    monkeypatch.setattr(main_mod, "serve", lambda app, hub, **kw: None)
    main_mod.main()
    bambu, klipper, hub = seen["printers"].bambu, seen["printers"].klipper, seen["hub"]
    assert isinstance(bambu, demo_printers.Switch) and isinstance(klipper, demo_printers.Switch)
    assert isinstance(hub, camera.Hub)
    assert isinstance(bambu._now(), Manager) and isinstance(klipper._now(), KlipperFamily)          # the demo is off
    on()
    assert isinstance(bambu._now(), demo_printers.Family) and bambu._now().family == "bambu"        # the demo is on
    assert isinstance(klipper._now(), demo_printers.Family) and klipper._now().family == "klipper"
    demo.leave()
    assert isinstance(bambu._now(), Manager)                                                         # and back without a restart


def test_the_registry_shows_the_demos_printers_and_takes_no_write(env):
    """`registry.load()` and `peek()` answer the demo's printers while it is on; every writer takes `_locked()`, which stops."""
    real = registry.add_bambu("00M00A0000000R1", "Real", "X1C", "192.0.2.9", "12345678")
    on()
    assert real.serial not in registry.load() and real.serial not in registry.peek()
    assert registry.load() and set(registry.load()) == set(registry.peek())
    with pytest.raises(demo.NotInDemo):
        registry.add_bambu("00M00A0000000R2", "Other", "X1C", "192.0.2.10", "12345678")
    with pytest.raises(demo.NotInDemo):
        registry.remove(next(iter(registry.load())))
    demo.leave()
    assert set(registry.load()) == {real.serial}                       # the real registry is as it was


def test_the_catalog_download_stops_in_the_demo(env):
    from filament_kit import library
    on()
    with pytest.raises(library.LibraryError, match="Not in the demo."):
        library.update("https://example.invalid/library.tar.gz", opener=lambda *a, **k: pytest.fail("reached outside"))


def test_usage_lands_in_the_demos_folder_and_not_in_the_real_state(env):
    """The pretend printers' usage (the log, the monthly files, the slot map) never lands in <state>/printers."""
    from filament_kit.bridge import usage
    on()
    usage.append("00M00A000000001", {"job": "j1", "grams": 5.0, "spool_id": 1, "slot": "A1", "observe": False})
    assert usage.entries("00M00A000000001", days=3650)
    assert not (env / "state" / "printers").exists()
    assert list((env / "state" / "demo" / "printers" / "00M00A000000001").rglob("*.xml"))
    demo.leave()
    assert not (env / "state" / "printers").exists() and not (env / "state" / "demo").exists()


def test_the_demos_event_ids_continue_above_the_real_log_and_the_real_log_above_the_demo(env):
    """A cursor taken in the real log (or in the demo) never skips the events after a demo start (or leave)."""
    who = {"type": "hub", "name": "Hub"}
    real = [events.write("print.started", {"type": "printer", "id": "p1", "name": "P"}, who, {"job": f"j{i}"})
            for i in range(5)]
    top = real[-1]["id"]
    on()
    first = events.write("print.started", {"type": "printer", "id": "p1", "name": "P"}, who, {"job": "d1"})
    second = events.write("print.started", {"type": "printer", "id": "p1", "name": "P"}, who, {"job": "d2"})
    assert first["id"] > top and second["id"] == first["id"] + 1
    demo.leave()
    after = events.write("print.started", {"type": "printer", "id": "p1", "name": "P"}, who, {"job": "r6"})
    assert after["id"] > second["id"]


def test_a_demo_print_goes_through_the_real_tracker_and_the_switch_and_leaves_nothing_behind(env):
    """The Tracker wired to the demo's Switch (as `main()` wires it) sees the pretend X1's print end; the
    demo records it itself, so no file lands under the real state folder and no `no_mapping` entry piles up."""
    from filament_kit.bridge import usage
    from filament_kit.demo import printers as demo_printers
    pid = "00M00A000000001"
    real = Manager(client_factory=lambda *a, **k: SimpleNamespace(), port=1)
    switch = demo_printers.Switch(real, demo_printers.Family("bambu", Empty()))
    tracker = usage.Tracker(switch, Empty())
    on()
    assert switch.command(pid, "pause")["ok"] and switch.command(pid, "stop")["ok"]       # RUNNING, PAUSE, FAILED
    tracker.flush(5)
    assert tracker._watch == {} and tracker._futures == []                # nothing is watched or queued
    assert usage.entries(pid, days=3650) == []                          # no `no_mapping` entry (GET .../usage shows these)
    assert not (env / "state" / "printers").exists()
    assert [e["job"] for e in demo_printers.usage_entries(pid, 3650)]    # the demo's own log has the print
    demo.leave()
    assert not (env / "state" / "printers").exists()


def test_the_slot_snapshot_lands_in_the_demos_folder_and_not_in_the_real_state(env):
    """What the slot test must put back is kept in the demo's folder, so leaving the demo leaves no pending restore."""
    from filament_kit.bridge import snapshot
    pid = "00M00A000000001"
    on()
    snapshot.save(pid, "slottest", [{"label": "A1", "ams_id": 0, "tray_id": 0, "setting": {"tray_type": "PLA"}}])
    assert snapshot.pending(pid) and snapshot.pending_serials() == [pid] and snapshot.load(pid)["kind"] == "slottest"
    assert not (env / "state" / "printers").exists()
    assert list((env / "state" / "demo" / "printers" / pid).rglob("*.xml"))
    demo.leave()
    assert not (env / "state" / "printers").exists() and not (env / "state" / "demo").exists()
    assert not snapshot.pending(pid) and snapshot.pending_serials() == []
