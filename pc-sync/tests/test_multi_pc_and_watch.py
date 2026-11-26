"""Feature 7: Multi-PC secondary (pull-only) sync + watch-on-close."""
import io
import json
import urllib.parse
import zipfile

import pytest

import orca_fixture
import studio_fixture
from fksync import __main__ as cli
from fksync import installer, statexml, store, studio, sync
from tls_helper import TlsServer, make_cert

STAMP1 = "20261120-010203"
STAMP2 = "20261121-010203"
T0 = 1_800_000_000.0
LINK_TOKEN = "LinkToken-Secret-0123456789abcdef"

PRESET = {
    "type": "filament",
    "name": "Acme PLA @BBL H2D",
    "inherits": "",
    "from": "User",
    "filament_id": "P1234567",
    "filament_type": ["PLA"],
    "nozzle_temperature": ["215"],
}
ORCA_PRESET = {
    "type": "filament",
    "name": "Acme PLA @Orca",
    "inherits": "Bambu PLA Basic @BBL A1",
    "from": "User",
    "filament_id": "P7654321",
    "filament_type": ["PLA"],
    "nozzle_temperature": ["215"],
}
INFO = {"sync_info": "", "user_id": "", "setting_id": "", "base_id": "", "updated_time": "0"}


def _build_zip(stamp=STAMP1, live_hash="primary-live", orca_live_hash="primary-orca",
               base_presets=None, orca_presets=None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        meta = {"stamp": stamp, "live_hash": live_hash, "orca_live_hash": orca_live_hash, "stale": []}
        zf.writestr("BUILD.json", json.dumps(meta))
        for name, data in (base_presets or {}).items():
            zf.writestr(f"base/{name}.json", json.dumps(data, indent=4))
            zf.writestr(f"base/{name}.info", "sync_info = \nsetting_id = \n")
        for name, data in (orca_presets or {}).items():
            zf.writestr(f"orca/{name}.json", json.dumps(data, indent=4))
            zf.writestr(f"orca/{name}.info", "sync_info = \nsetting_id = \n")
    return buf.getvalue()


class FakeClient:
    def __init__(self, state, builds=None):
        self.st = state
        self.builds = dict(builds or {})
        self.calls = []

    def state(self):
        self.calls.append(("state",))
        return json.loads(json.dumps(self.st))

    def build(self, stamp):
        self.calls.append(("build", stamp))
        return self.builds[stamp]

    def upload(self, kind, data, **kw):
        self.calls.append(("upload", kind))
        raise AssertionError(f"forbidden upload({kind!r}) in secondary mode")

    def rebuild(self, live_hash, orca_live_hash=None):
        self.calls.append(("rebuild", live_hash, orca_live_hash))
        raise AssertionError("forbidden rebuild() in secondary mode")

    def installed(self, stamp, trees=None, written=None):
        self.calls.append(("installed", stamp))
        raise AssertionError("forbidden installed() in secondary mode")

    def heartbeat(self, outcome, waiting_since=None, app=None):
        self.calls.append(("heartbeat", outcome))
        raise AssertionError("forbidden heartbeat() in secondary mode")


def _owner_state(stamp=STAMP1, *, installed_stamp=None, orca_target=True):
    return {
        "owner": "primary-dev",
        "you": "secondary-dev",
        "user_id": "owner-user",
        "live_hash": "primary-live",
        "system_hash": "primary-sys",
        "orca_target": orca_target,
        "orca_live_hash": "primary-orca",
        "orca_system_hash": "primary-osys",
        "build": {"stamp": stamp, "live_hash": "primary-live", "orca_live_hash": "primary-orca"} if stamp else None,
        "installed": {"stamp": installed_stamp, "at": T0} if installed_stamp else None,
        "rebuild_requested": None,
    }


def test_role_validation(tmp_path):
    folders = studio_fixture.make(tmp_path / "appdata", base={"Acme PLA @BBL H2D": (PRESET, INFO)})
    dirs = store.AppDirs(tmp_path / "local")
    c = FakeClient(_owner_state())
    with pytest.raises(ValueError, match="invalid sync role"):
        sync.run_once(c, folders, dirs, running=lambda: False, now=T0, role="bogus")
    assert store.role_of({}) == "auto"
    assert store.role_of({"role": "secondary"}) == "secondary"
    with pytest.raises(ValueError, match="invalid sync role"):
        store.role_of({"role": "bogus"})


def test_auto_and_primary_still_return_other_pc(tmp_path):
    folders = studio_fixture.make(tmp_path / "appdata", base={"Acme PLA @BBL H2D": (PRESET, INFO)})
    dirs = store.AppDirs(tmp_path / "local")

    class NonRaisingClient(FakeClient):
        def heartbeat(self, outcome, waiting_since=None, app=None):
            self.calls.append(("heartbeat", outcome))
            return {"ok": True}

    for role in ("auto", "primary"):
        c = NonRaisingClient(_owner_state())
        assert sync.run_once(c, folders, dirs, running=lambda: False, now=T0, role=role) == "other_pc"
        assert c.calls == [("state",)]


def test_secondary_pull_only_installs_and_tracks_local_stamp(tmp_path):
    folders = studio_fixture.make(tmp_path / "appdata", base={})
    dirs = store.AppDirs(tmp_path / "local")
    z1 = _build_zip(STAMP1, base_presets={"Acme PLA @BBL H2D": dict(PRESET, nozzle_temperature=["220"])})
    z2 = _build_zip(STAMP2, base_presets={"Acme PLA @BBL H2D": dict(PRESET, nozzle_temperature=["225"])})
    # Server says primary already installed STAMP1, but this secondary PC has not.
    c = FakeClient(_owner_state(STAMP1, installed_stamp=STAMP1, orca_target=False), builds={STAMP1: z1, STAMP2: z2})

    assert sync.run_once(c, folders, dirs, running=lambda: False, now=T0, role="secondary") == "installed"
    assert c.calls == [("state",), ("build", STAMP1)]
    st = sync.load_state(dirs)
    assert st["installed"]["stamp"] == STAMP1
    assert "report" not in st and "live_owed" not in st
    live_file = folders.user_filament / "base" / "Acme PLA @BBL H2D.json"
    assert json.loads(live_file.read_text(encoding="utf-8"))["nozzle_temperature"] == ["220"]

    # Next run with same server build: up_to_date, no build download, no uploads/heartbeats.
    c.calls.clear()
    assert sync.run_once(c, folders, dirs, running=lambda: False, now=T0 + 60, role="secondary") == "up_to_date"
    assert c.calls == [("state",)]

    # New build on server (even if server["installed"] is None): secondary downloads & installs STAMP2.
    c.st = _owner_state(STAMP2, installed_stamp=None, orca_target=False)
    c.calls.clear()
    assert sync.run_once(c, folders, dirs, running=lambda: False, now=T0 + 120, role="secondary") == "installed"
    assert c.calls == [("state",), ("build", STAMP2)]
    assert sync.load_state(dirs)["installed"]["stamp"] == STAMP2


def test_secondary_no_build_and_studio_running_never_call_owner_endpoints(tmp_path):
    folders = studio_fixture.make(tmp_path / "appdata", base={})
    dirs = store.AppDirs(tmp_path / "local")
    c = FakeClient(_owner_state(None, orca_target=False))

    assert sync.run_once(c, folders, dirs, running=lambda: True, now=T0, role="secondary") == "studio_running"
    assert c.calls == []  # no heartbeat, no state when Bambu Studio is running

    assert sync.run_once(c, folders, dirs, running=lambda: False, now=T0 + 10, role="secondary") == "no_build"
    assert c.calls == [("state",)]  # no rebuild, no upload, no heartbeat


def test_secondary_orca_holds_local_edits_and_skips_local_deletions(tmp_path):
    folders = studio_fixture.make(tmp_path / "appdata", base={})
    orca = orca_fixture.make(tmp_path / "appdata", presets={})
    dirs = store.AppDirs(tmp_path / "local")

    b1 = tmp_path / "b1"
    (b1 / "base").mkdir(parents=True)
    (b1 / "orca").mkdir(parents=True)
    (b1 / "BUILD.json").write_text(json.dumps({"stamp": STAMP1}))
    for sub, name, data in (
        ("base", "Acme PLA @BBL H2D", PRESET),
        ("orca", "Acme PLA @Orca", ORCA_PRESET),
        ("orca", "Acme PETG @Orca", dict(ORCA_PRESET, name="Acme PETG @Orca")),
        ("orca", "Acme ABS @Orca", dict(ORCA_PRESET, name="Acme ABS @Orca")),
        ("orca", "Acme TPU @Orca", dict(ORCA_PRESET, name="Acme TPU @Orca")),
    ):
        (b1 / sub / f"{name}.json").write_text(json.dumps(data, indent=4), encoding="utf-8")
        (b1 / sub / f"{name}.info").write_text("sync_info = \nsetting_id = \n", encoding="utf-8")

    res1 = installer.install(
        b1, folders, dirs, running=lambda: False, stamp_now=STAMP1,
        orca=orca, orca_running=lambda: False, secondary=True,
    )
    assert sorted(res1.installed_orca) == ["Acme ABS @Orca", "Acme PETG @Orca", "Acme PLA @Orca", "Acme TPU @Orca"]

    # On secondary PC: user edits one Orca preset and deletes one (1 of 4 <= 25%, not an app reset).
    edited = orca.user_filament / "Acme PLA @Orca.json"
    edited.write_text(json.dumps(dict(ORCA_PRESET, nozzle_temperature=["235"]), indent=4), encoding="utf-8")
    deleted = orca.user_filament / "Acme PETG @Orca.json"
    deleted.unlink()

    # Build 2 updates both Orca presets and accepts Acme PLA @Orca.
    b2 = tmp_path / "b2"
    (b2 / "base").mkdir(parents=True)
    (b2 / "orca").mkdir(parents=True)
    (b2 / "BUILD.json").write_text(json.dumps({"stamp": STAMP2}))
    for sub, name, data in (
        ("base", "Acme PLA @BBL H2D", PRESET),
        ("orca", "Acme PLA @Orca", dict(ORCA_PRESET, nozzle_temperature=["220"])),
        ("orca", "Acme PETG @Orca", dict(ORCA_PRESET, name="Acme PETG @Orca", nozzle_temperature=["240"])),
        ("orca", "Acme ABS @Orca", dict(ORCA_PRESET, name="Acme ABS @Orca")),
        ("orca", "Acme TPU @Orca", dict(ORCA_PRESET, name="Acme TPU @Orca")),
    ):
        (b2 / sub / f"{name}.json").write_text(json.dumps(data, indent=4), encoding="utf-8")
        (b2 / sub / f"{name}.info").write_text("sync_info = \nsetting_id = \n", encoding="utf-8")

    res2 = installer.install(
        b2, folders, dirs, running=lambda: False, stamp_now=STAMP2,
        orca=orca, orca_running=lambda: False,
        accept={"orca": ["Acme PLA @Orca"]}, secondary=True,
    )
    assert res2.installed_orca == []
    assert res2.skipped_orca == ["Acme PETG @Orca"]
    assert json.loads(edited.read_text(encoding="utf-8"))["nozzle_temperature"] == ["235"]
    assert not deleted.exists()


def test_secondary_orca_only_sync(tmp_path):
    orca = orca_fixture.make(tmp_path / "appdata", presets={})
    dirs = store.AppDirs(tmp_path / "local")
    z1 = _build_zip(STAMP1, orca_presets={"Acme PLA @Orca": dict(ORCA_PRESET, nozzle_temperature=["222"])})
    c = FakeClient(_owner_state(STAMP1, orca_target=True), builds={STAMP1: z1})

    assert sync.run_once(c, None, dirs, running=lambda: False, now=T0,
                         orca=orca, orca_running=lambda: False, role="secondary") == "installed"
    assert c.calls == [("state",), ("build", STAMP1)]
    assert sync.load_state(dirs)["installed"]["trees"] == ["orca"]
    assert json.loads((orca.user_filament / "Acme PLA @Orca.json").read_text(encoding="utf-8"))["nozzle_temperature"] == ["222"]


def test_watch_on_close_transitions_and_once():
    states = iter([False, True, True, False, False, True, False])
    sleeps = []
    runs = []

    count = sync.watch_on_close(
        lambda: runs.append("ran"),
        is_running=lambda: next(states),
        sleep=sleeps.append,
        interval=2.0,
        settle=1.5,
        max_ticks=7,
        once=False,
    )
    assert count == 2
    assert runs == ["ran", "ran"]
    assert sleeps == [2.0, 2.0, 2.0, 1.5, 2.0, 2.0, 2.0, 1.5]

    # once=True stops immediately after the first True -> False trigger
    states_once = iter([True, False, True, False])
    sleeps_once = []
    runs_once = []
    count_once = sync.watch_on_close(
        lambda: runs_once.append("ran"),
        is_running=lambda: next(states_once),
        sleep=sleeps_once.append,
        interval=3.0,
        settle=1.0,
        once=True,
    )
    assert count_once == 1
    assert runs_once == ["ran"]
    assert sleeps_once == [3.0, 1.0]

    # max_ticks=0 does nothing
    assert sync.watch_on_close(lambda: None, is_running=lambda: True, max_ticks=0) == 0


def test_cli_pair_secondary_preserve_role_run_and_watch_lock_hygiene(tmp_path, monkeypatch, capsys):
    appdata, local = tmp_path / "appdata", tmp_path / "local"
    studio_fixture.make(appdata, base={})
    dirs = store.AppDirs(local / cli.APP)
    z1 = _build_zip(STAMP1, base_presets={"Acme PLA @BBL H2D": dict(PRESET, nozzle_temperature=["220"])})

    requests = []

    def respond(method, path, body):
        requests.append((method, path))
        if path == "/api/info":
            return 200, "application/json", {"name": "fk-hub", "version": "0.2.0", "api": 1}
        if path == "/api/sync/state":
            return 200, "application/json", _owner_state(STAMP1, orca_target=False)
        if path.startswith("/api/sync/build"):
            return 200, "application/zip", z1
        return 404, "application/json", {"error": "not_found", "message": "unexpected"}

    monkeypatch.setattr(studio, "orca_running", lambda tasklist=None: False)
    monkeypatch.setattr(studio, "_registry_entries", lambda: [])

    cert, key, fp = make_cert(tmp_path / "cert")
    with TlsServer(cert, key, respond=respond) as srv:
        q = urllib.parse.urlencode({"h": "127.0.0.1", "p": srv.port, "t": LINK_TOKEN, "fp": fp}, doseq=True)
        link = f"fkit://pair?{q}"

        # 1. pair --secondary saves role="secondary"
        old_stdin = cli.sys.stdin
        cli.sys.stdin = io.StringIO(link + "\n")
        try:
            assert cli.main(["--appdata", str(appdata), "--localappdata", str(local), "pair", "--secondary"]) == 0
        finally:
            cli.sys.stdin = old_stdin
        assert store.load_config(dirs)["role"] == "secondary"

        # 2. Re-pair without --secondary preserves role="secondary"
        cli.sys.stdin = io.StringIO(link + "\n")
        try:
            assert cli.main(["--appdata", str(appdata), "--localappdata", str(local), "pair"]) == 0
        finally:
            cli.sys.stdin = old_stdin
        assert store.load_config(dirs)["role"] == "secondary"

        # 3. status shows secondary role
        capsys.readouterr()
        assert cli.main(["--appdata", str(appdata), "--localappdata", str(local), "status"]) == 0
        assert "secondary (pull-only)" in capsys.readouterr().out

        # 4. watch --once --interval 0.01 --secondary polls without holding single_instance,
        #    and acquires single_instance only inside cmd_run.
        seq = iter([True, False])
        lock_free_during_poll = []

        def fake_running(tasklist=None):
            # Verify single_instance is NOT held during idle polling!
            with store.single_instance(dirs):
                lock_free_during_poll.append(True)
            return next(seq)

        monkeypatch.setattr(studio, "running", fake_running)
        monkeypatch.setattr(sync.time, "sleep", lambda _s: None)
        # During cmd_run itself, studio.running() must be False so install proceeds.
        orig_cmd_run = cli.cmd_run
        run_lock_held = []

        def spy_cmd_run(*a, **kw):
            # Inside cmd_run, single_instance will be acquired; verify it is free right before entering.
            with store.single_instance(dirs):
                run_lock_held.append("free_before")
            monkeypatch.setattr(studio, "running", lambda tasklist=None: False)
            return orig_cmd_run(*a, **kw)

        monkeypatch.setattr(cli, "cmd_run", spy_cmd_run)
        assert cli.main(["--appdata", str(appdata), "--localappdata", str(local),
                         "watch", "--interval", "0.01", "--once", "--secondary"]) == 0

        assert lock_free_during_poll == [True, True]
        assert run_lock_held == ["free_before"]
        # After watch exits, lock is released.
        with store.single_instance(dirs):
            pass

    # Verify only allowed endpoints were ever called.
    sync_calls = [(m, p.split("?")[0]) for m, p in requests if p.startswith("/api/sync")]
    assert sync_calls
    for method, path in sync_calls:
        assert (method, path) in {("GET", "/api/sync/state"), ("GET", "/api/sync/build")}
