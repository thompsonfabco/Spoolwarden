"""RC6b / RC7: the stops of R85 4.3 that live in the modules that reach outside, and the wiring: while the demo is on
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
def env(tmp_path, monkeypatch):
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
    """The demo starts on an empty registry, so a printer is added after it (a stale registry): sync still connects nothing."""
    on()
    registry.add_bambu("00M00A000000001", "X1", "X1C", "192.0.2.9", "12345678")
    registry.add_klipper("Voron", "192.0.2.30")
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
