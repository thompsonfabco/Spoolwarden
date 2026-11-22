"""AS1 (Stage 12 Auto): auto slot assignment engine, status annotation, pending guards, Klipper live watch and API."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from fakes.fake_paho import FakeClient, FakePrinter
from filament_kit import config, event_sink, pairing, profiles
from filament_kit.api import create_app
from filament_kit.bridge import auto_slot, connection, family, loading, pending, registry
from filament_kit.bridge.connection import Manager, PrinterOffline, UnknownPrinter
from filament_kit.bridge.klipper import hardware as kh
from filament_kit.bridge.klipper import loads
from filament_kit.bridge.klipper.manager import KlipperFamily
from filament_kit.bridge.registry import Printer
from filament_kit.events import EventStore
from filament_kit.spoolman import Spoolman

DATA_KLIPPER = Path(__file__).parent / "data" / "klipper"
SERIAL = "S1"
SERIAL2 = "S2"
FULL = {"id": "0", "state": 11, "tray_type": "PLA", "tray_color": "FF0000FF", "tray_info_idx": "Pa111111",
        "nozzle_temp_min": "190", "nozzle_temp_max": "230", "tray_sub_brands": ""}
EMPTY = {"id": "2", "state": 10}
RFID = dict(FULL, id="2", tray_info_idx="GFA00", tray_uuid="A1B2C3D4E5F60718293A4B5C6D7E8F90")
PRESET = profiles.PresetRef(filament_id="P8a1b2c3", name="SUNLU PLA+ Grey @Bambu Lab H2D 0.4 nozzle",
                            nozzle_temp_min=200, nozzle_temp_max=230, filament_type="PLA", setting_id="")


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def _report(a2=EMPTY, a3=EMPTY, bits="70003", reading="0", gcode="IDLE", active_tray="255"):
    return {
        "gcode_state": gcode,
        "ams": {
            "tray_exist_bits": bits,
            "tray_reading_bits": reading,
            "tray_now": active_tray,
            "ams": [
                {"id": "0", "tray": [dict(FULL, id="0"), dict(FULL, id="1"),
                                     dict(a2, id="2"), dict(a3, id="3")]},
                {"id": "128", "tray": [dict(EMPTY, id="0")]},
                {"id": "129", "tray": [dict(EMPTY, id="0")]},
                {"id": "130", "tray": [dict(EMPTY, id="0")]},
            ],
        },
        "vt_tray": dict(FULL, id="254"),
    }


def _push(rig, report):
    m, p, _ = rig
    prev = m._conn(SERIAL).report()
    p.push(report)
    assert m.wait_report(SERIAL, lambda r: r is not prev, 2) is not None


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    pairing.make_cert("fkhost", [], tmp_path / "etc")
    config.save({})
    kh._reset_cache()
    monkeypatch.setattr(connection, "PUSHALL_AFTER_COMMAND", 0.05)
    monkeypatch.setattr(connection, "COMMAND_TIMEOUT", 1.0)
    monkeypatch.setattr(loading, "CONFIRM_TIMEOUT", 0.5)
    monkeypatch.setattr(profiles, "preset_for", lambda *a, **kw: PRESET)
    monkeypatch.setattr(pending, "STORE", pending.Store(start_thread=False))
    monkeypatch.setattr(auto_slot, "STORE", auto_slot.Store(start_thread=False))
    event_sink.set_sink(None)
    yield tmp_path
    event_sink.set_sink(None)
    kh._reset_cache()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def astore(clock, monkeypatch):
    s = auto_slot.Store(clock, start_thread=False)
    monkeypatch.setattr(auto_slot, "STORE", s)
    return s


@pytest.fixture
def sm(spoolman_url):
    s = Spoolman(spoolman_url)
    s.ensure_fields()
    v = s.post("vendor", {"name": "SUNLU"})
    f = s.post("filament", {"name": "PLA+ Grey", "vendor_id": v["id"], "material": "PLA", "color_hex": "8a8c8e"})
    f_nameless = s.post("filament", {"material": "PETG"})
    s.sp = s.post("spool", {"filament_id": f["id"], "location": "Rack 1"})
    s.sp2 = s.post("spool", {"filament_id": f["id"], "location": "Rack 2"})
    s.nameless = s.post("spool", {"filament_id": f_nameless["id"], "location": "Rack 3"})
    return s


@pytest.fixture
def ev_sink(sm, tmp_path):
    store = EventStore(tmp_path / "state" / "events")
    event_sink.set_sink(event_sink.StoreSink(store))
    return store


@pytest.fixture
def rig():
    cs = []
    m = Manager(client_factory=lambda cid: cs.append(FakeClient(cid)) or cs[-1])
    registry.upsert(Printer(SERIAL, "Office H2D", "O1D", "10.0.0.5", "13572468", True))
    m.sync()
    p = FakePrinter(cs[0], _report(a2=EMPTY, a3=EMPTY, bits="30003"))
    cs[0].connected(0)
    assert m.wait_report(SERIAL, lambda r: "ams" in r, 2) is not None
    yield m, p, cs[0]
    m.stop()


# --- watchable & helpers ----------------------------------------------------------------------------------------------

def test_watchable_bambu_units_and_presence_gates():
    st = {
        "family": "bambu",
        "presence": "bits",
        "ams": [
            {"unit": 0, "kind": "ams", "slots": [{"label": "A1", "present": True}, {"label": "A2", "present": False}]},
            {"unit": 1, "kind": "ams_2pro", "slots": [{"label": "B1", "present": False}]},
            {"unit": 128, "kind": "ams_ht", "slots": [{"label": "HT-A", "present": True}]},
            {"unit": 129, "kind": "ams_ht", "slots": [{"label": "HT-B", "present": False}]},
            {"unit": 130, "kind": "ams_ht", "slots": [{"label": "HT-C", "present": False}]},
            {"unit": 255, "kind": "ext", "slots": [{"label": "Ext", "present": False}]},
        ],
    }
    assert auto_slot.watchable_labels(st) == ["A1", "A2", "B1", "HT-A", "HT-B"]
    assert auto_slot.is_auto_possible(st) is True

    # presence=="tray_type" has no sensor Auto can trust, even if slots claim present=False
    tray_type_st = {**st, "presence": "tray_type"}
    assert auto_slot.watchable_labels(tray_type_st) == []
    assert auto_slot.is_auto_possible(tray_type_st) is False

    # < 2 watchable slots (e.g. one verified HT-A + Ext + unverified HT-C) -> False
    single_ht = {
        "family": "bambu",
        "presence": "bits",
        "ams": [
            {"unit": 128, "kind": "ams_ht", "slots": [{"label": "HT-A", "present": False}]},
            {"unit": 130, "kind": "ams_ht", "slots": [{"label": "HT-C", "present": False}]},
            {"unit": 255, "kind": "ext", "slots": [{"label": "Ext", "present": False}]},
        ],
    }
    assert auto_slot.watchable_labels(single_ht) == ["HT-A"]
    assert auto_slot.is_auto_possible(single_ht) is False


def test_watchable_klipper_adapters():
    afc = {
        "family": "klipper",
        "ams": [{"unit": 0, "kind": "lane", "slots": [
            {"label": "lane1", "present": True}, {"label": "lane2", "present": False},
            {"label": "lane3", "present": False},
        ]}],
    }
    assert auto_slot.watchable_labels(afc, adapter="afc") == ["lane1", "lane2", "lane3"]
    assert auto_slot.is_auto_possible(afc, adapter="afc") is True
    assert auto_slot.watchable_labels(afc, adapter="single") == []
    assert auto_slot.watchable_labels(afc, adapter="toolchanger") == []
    assert auto_slot.watchable_labels(afc, adapter="lanedata") == []

    hh = {
        "family": "klipper",
        "ams": [{"unit": 0, "kind": "lane", "slots": [
            {"label": "G0", "present": True}, {"label": "G1", "present": None}, {"label": "G2", "present": False},
        ]}],
    }
    assert auto_slot.watchable_labels(hh, adapter="happyhare") == ["G0", "G2"]
    assert auto_slot.is_auto_possible(hh, adapter="happyhare") is True

    hh_unknown = {
        "family": "klipper",
        "ams": [{"unit": 0, "kind": "lane", "slots": [
            {"label": "G0", "present": None}, {"label": "G1", "present": None},
        ]}],
    }
    assert auto_slot.is_auto_possible(hh_unknown, adapter="happyhare") is False


def test_reserved_label_and_spool_naming():
    for bad in ("auto", "Auto", "AUTO", " auto "):
        assert auto_slot.is_reserved_label(bad) is True
    for ok in ("A1", "lane1", "HT-A", "", None):
        assert auto_slot.is_reserved_label(ok) is False

    assert auto_slot.format_spool_name({"id": 4, "filament": {"vendor": {"name": "SUNLU"}, "name": "PLA+ Grey"}}) == \
        "SUNLU PLA+ Grey"
    assert auto_slot.format_spool_name({"id": 4, "filament": {"name": "PETG Black"}}) == "PETG Black"
    nameless = auto_slot.format_spool_name({"id": 7, "filament": {"material": "PETG"}})
    assert nameless == "Spool 7" and "#" not in nameless
    assert auto_slot.format_spool_name(None, 12) == "Spool 12"
    assert auto_slot.format_spool_name(None, None) == "the spool"


# --- Store.start, tick, choose, cancel, annotate, TTL -----------------------------------------------------------------

def test_start_dry_run_replaces_and_per_printer_limit(rig, sm, astore):
    m, _, _ = rig
    dry = astore.start(m, sm, SERIAL, sm.sp["id"], dry=True)
    assert dry["auto"] is True and dry["dry_run"] is True
    assert "A1" in dry["watching"] and "HT-A" in dry["watching"] and "HT-C" not in dry["watching"] and "Ext" not in dry["watching"]
    assert "replaces" not in dry and astore.jobs() == {}

    started = astore.start(m, sm, SERIAL, sm.nameless["id"], dry=False)
    assert started == {
        "auto": True,
        "pending": True,
        "confirmed": None,
        "expires_in": 900,
        "message": f"Waiting for Spool {sm.nameless['id']} to be loaded into any slot of Office H2D.",
    }

    dry2 = astore.start(m, sm, SERIAL, sm.sp["id"], dry=True)
    assert dry2["replaces"] == {"spool_id": sm.nameless["id"], "name": f"Spool {sm.nameless['id']}"}

    # Replacing on same printer leaves 1 job; another printer can hold its own
    astore.start(m, sm, SERIAL, sm.sp["id"], dry=False)
    assert set(astore.jobs()) == {SERIAL}

    class OtherMgr:
        def state(self, _): return "connected"
        def status(self, _):
            return {"family": "bambu", "presence": "bits",
                    "ams": [{"unit": 0, "kind": "ams", "slots": [
                        {"label": "A1", "present": False}, {"label": "A2", "present": False}]}]}
    registry.upsert(Printer(SERIAL2, "Garage P1S", "C12", "10.0.0.6", "11112222", True))
    astore.start(OtherMgr(), sm, SERIAL2, sm.sp2["id"], dry=False)
    assert set(astore.jobs()) == {SERIAL, SERIAL2}


def test_start_refusals(sm, astore):
    with pytest.raises(UnknownPrinter):
        astore.start(object(), sm, "NOPE", sm.sp["id"], dry=True)

    registry.upsert(Printer(SERIAL, "Office H2D", "O1D", "10.0.0.5", "13572468", True))

    class OfflineMgr:
        def state(self, _): return "disconnected"
        def status(self, _): return None
    with pytest.raises(PrinterOffline):
        astore.start(OfflineMgr(), sm, SERIAL, sm.sp["id"], dry=True)

    class ExtOnlyMgr:
        def state(self, _): return "connected"
        def status(self, _):
            return {"family": "bambu", "presence": "bits",
                    "ams": [{"unit": 255, "kind": "ext", "slots": [{"label": "Ext", "present": False}]}]}
    with pytest.raises(auto_slot.AutoError) as exc:
        astore.start(ExtOnlyMgr(), sm, SERIAL, sm.sp["id"], dry=True)
    assert exc.value.code == "auto_not_possible" and exc.value.status == 409

    class GoodMgr:
        def state(self, _): return "connected"
        def status(self, _):
            return {"family": "bambu", "presence": "bits",
                    "ams": [{"unit": 0, "kind": "ams", "slots": [
                        {"label": "A1", "present": False}, {"label": "A2", "present": False}]}]}
    with pytest.raises(loading.NotFound):
        astore.start(GoodMgr(), sm, SERIAL, 999999, dry=True)


def test_tick_waits_for_reading_and_settle_then_applies_with_auto_event_flag(rig, sm, ev_sink, clock, astore):
    m, _, _ = rig
    # Prior spool was recorded on Office H2D - AMS A3 even though slot starts physically empty -> triggers swap unload+load
    old_spool = sm.post("spool", {"filament_id": sm.sp["filament"]["id"], "location": "Office H2D \u2013 AMS A3"})
    astore.start(m, sm, SERIAL, sm.sp["id"], dry=False)

    # A1 started full and stays full -> ignored; A3 goes empty -> loaded while reading (bit 2 -> reading bit 2)
    _push(rig, _report(a2=FULL, a3=EMPTY, bits="30007", reading="4"))
    astore.tick()
    assert astore.jobs()[SERIAL].since == {}

    # Reading finishes: settle timer starts at t=1000
    _push(rig, _report(a2=FULL, a3=EMPTY, bits="30007", reading="0"))
    astore.tick()
    assert SERIAL in astore.jobs() and "A3" in astore.jobs()[SERIAL].since

    clock.t += auto_slot.SETTLE - 0.5
    astore.tick()
    assert SERIAL in astore.jobs()

    # After 3s settle: applies load into A3
    clock.t += 1.0
    astore.tick()
    assert astore.jobs() == {}
    assert sm.get(f"spool/{sm.sp['id']}")["location"] == "Office H2D \u2013 AMS A3"

    loaded_evs = [e for e in ev_sink.read(kinds=("spool.loaded",))["events"] if str(e["data"].get("spool")) == str(sm.sp["id"])]
    unloaded_evs = [e for e in ev_sink.read(kinds=("spool.unloaded",))["events"] if str(e["data"].get("spool")) == str(old_spool["id"])]
    assert len(loaded_evs) == 1 and loaded_evs[0]["data"].get("auto") == "yes"
    assert len(unloaded_evs) == 1 and "auto" not in unloaded_evs[0]["data"]


def test_tick_skips_rfid_active_while_printing_and_unchanged_loaded(rig, sm, clock, astore):
    m, _, _ = rig
    # A1, A2, A4 start present=True (bits=0xb); A3 and HT-A start empty
    _push(rig, _report(a2=EMPTY, a3=FULL, bits="0000b"))
    astore.start(m, sm, SERIAL, sm.sp["id"], dry=False)

    # A3 loads an RFID spool; HT-A becomes present=True while active and RUNNING -> both must be skipped
    _push(rig, {
        "gcode_state": "RUNNING",
        "ams": {
            "tray_exist_bits": "1000f",
            "tray_reading_bits": "0",
            "tray_now": "128",
            "ams": [
                {"id": "0", "tray": [dict(FULL, id="0"), dict(FULL, id="1"), RFID, dict(FULL, id="3")]},
                {"id": "128", "tray": [dict(FULL, id="0")]},
                {"id": "129", "tray": [dict(EMPTY, id="0")]},
                {"id": "130", "tray": [dict(EMPTY, id="0")]},
            ],
        },
        "vt_tray": dict(FULL, id="254"),
    })
    clock.t += 10
    astore.tick()
    assert SERIAL in astore.jobs() and astore.jobs()[SERIAL].since == {}

    # When printer becomes IDLE, active flag on HT-A does NOT block loading
    _push(rig, {
        "gcode_state": "IDLE",
        "ams": {
            "tray_exist_bits": "1000f",
            "tray_reading_bits": "0",
            "tray_now": "128",
            "ams": [
                {"id": "0", "tray": [dict(FULL, id="0"), dict(FULL, id="1"), RFID, dict(FULL, id="3")]},
                {"id": "128", "tray": [dict(FULL, id="0")]},
                {"id": "129", "tray": [dict(EMPTY, id="0")]},
                {"id": "130", "tray": [dict(EMPTY, id="0")]},
            ],
        },
        "vt_tray": dict(FULL, id="254"),
    })
    astore.tick()
    clock.t += auto_slot.SETTLE + 1
    astore.tick()
    assert astore.jobs() == {}
    assert sm.get(f"spool/{sm.sp['id']}")["location"] == "Office H2D \u2013 AMS HT-A"


def test_tick_two_slots_enter_choose_and_choose_or_cancel(rig, sm, ev_sink, clock, astore):
    m, _, _ = rig
    astore.start(m, sm, SERIAL, sm.sp["id"], dry=False)

    # Both A3 (bit 2) and A4 (bit 3) were empty at baseline, then both load -> state="choose"
    _push(rig, _report(a2=FULL, a3=FULL, bits="3000f"))
    astore.tick()
    clock.t += auto_slot.SETTLE + 5
    astore.tick()  # still waiting on choice; must not auto-apply either slot

    view = m.status(SERIAL)
    assert view["auto_possible"] is True
    assert view["auto"]["state"] == "choose"
    assert view["auto"]["slots"] == ["A3", "A4"]
    assert "Two slots were loaded" in view["auto"]["text"]

    # Wrong slot / waiting-only errors
    with pytest.raises(auto_slot.AutoError) as exc:
        astore.choose(m, sm, SERIAL, "A1")
    assert exc.value.code == "nothing_to_choose"

    # Choose A4 -> applies load with auto="yes" and clears Auto
    res = astore.choose(m, sm, SERIAL, "A4")
    assert res["confirmed"] is True
    assert astore.jobs() == {}
    assert sm.get(f"spool/{sm.sp['id']}")["location"] == "Office H2D \u2013 AMS A4"
    evs = [e for e in ev_sink.read(kinds=("spool.loaded",))["events"] if str(e["data"].get("spool")) == str(sm.sp["id"])]
    assert evs and evs[0]["data"].get("auto") == "yes"

    # Cancel errors & success
    with pytest.raises(auto_slot.AutoError) as exc2:
        astore.cancel(SERIAL)
    assert exc2.value.code == "not_waiting"
    astore.start(m, sm, SERIAL, sm.sp["id"], dry=False)
    assert astore.cancel(SERIAL) == {"cancelled": True}
    assert astore.active_view(SERIAL) is None


@pytest.mark.parametrize("change", [{"present": False}, {"reading": True}, {"rfid": True}, {"watchable": False}])
def test_choose_rechecks_the_selected_slot(rig, sm, astore, monkeypatch, change):
    m, _, _ = rig
    astore.start(m, sm, SERIAL, sm.sp["id"], dry=False)
    _push(rig, _report(a2=FULL, a3=FULL, bits="3000f"))
    astore.tick()
    current = m.status(SERIAL)
    first = next(sl for u in current["ams"] for sl in u["slots"] if sl["label"] == "A3")
    first.update(change)
    monkeypatch.setattr(m, "status", lambda serial: current)
    sent = []
    monkeypatch.setattr(loading, "load", lambda _m, _sm, serial, label, spool, dry, **kw: sent.append(label) or {})

    with pytest.raises(auto_slot.AutoError) as exc:
        astore.choose(m, sm, SERIAL, "A3")
    assert exc.value.code == "nothing_to_choose" and sent == []
    astore.choose(m, sm, SERIAL, "A4")
    assert sent == ["A4"]


def test_reserved_klipper_lane_never_becomes_a_watch_candidate(sm, astore):
    pid = "KP1"
    registry.upsert(Printer(pid, "Box Turtle", "generic", "10.0.0.9", "", True, family="klipper"))
    reserved = {"label": "AuTo", "present": False, "watchable": True}
    lanes = [{"label": "lane1", "present": False}, reserved]
    st = {"family": "klipper", "gcode_state": "IDLE", "auto_possible": True, "ams": [{"kind": "lane", "slots": lanes}]}
    manager = type("Manager", (), {"status": lambda self, serial: st})()
    with pytest.raises(auto_slot.AutoError, match="no slots"):
        astore.start(manager, sm, pid, sm.sp["id"])
    lanes.append({"label": "lane2", "present": False})
    assert astore.start(manager, sm, pid, sm.sp["id"])["watching"] == ["lane1", "lane2"]
    astore.start(manager, sm, pid, sm.sp["id"], dry=False)
    reserved["present"] = True
    lanes[0]["present"] = True
    astore.tick()
    assert astore.active_view(pid)["state"] == "waiting"
    assert set(astore.jobs()[pid].since) == {"lane1"}


def test_ttl_expiry_and_drop_and_cancel_for_spool(rig, sm, clock, astore):
    m, _, _ = rig
    astore.start(m, sm, SERIAL, sm.sp["id"], dry=False)
    clock.t += auto_slot.TTL + 1
    assert astore.active_view(SERIAL) is None

    astore.start(m, sm, SERIAL, sm.sp["id"], dry=False)
    assert astore.cancel_for_spool(999999) == 0
    assert astore.cancel_for_spool(sm.sp["id"]) == 1
    assert astore.jobs() == {}

    astore.start(m, sm, SERIAL, sm.sp["id"], dry=False)
    assert astore.drop_printer(SERIAL) is True
    assert astore.drop_printer(SERIAL) is False


# --- pending guards & cancellation ------------------------------------------------------------------------------------

def test_pending_rejects_auto_and_cancels_on_real_load(rig, sm, astore, monkeypatch):
    m, _, _ = rig
    for fn in (lambda: pending.load_or_wait(m, sm, SERIAL, "auto", sm.sp["id"], True),
               lambda: pending.load_or_wait(m, sm, SERIAL, "Auto", sm.sp["id"], False),
               lambda: pending.unload(m, sm, SERIAL, " AUTO ", True),
               lambda: pending.clear_slot(m, sm, SERIAL, "auto", True)):
        with pytest.raises(ValueError, match="auto"):
            fn()

    astore.start(m, sm, SERIAL, sm.sp["id"], dry=False)
    # dry=True does NOT cancel Auto
    pending.load_or_wait(m, sm, SERIAL, "A1", sm.sp["id"], True)
    assert astore.active_view(SERIAL) is not None

    def refused(*args, **kw):
        raise loading.SlotWriteFailed("write refused")

    with monkeypatch.context() as fail:
        fail.setattr(loading, "load", refused)
        with pytest.raises(loading.SlotWriteFailed):
            pending.load_or_wait(m, sm, SERIAL, "A1", sm.sp["id"], False)
    assert astore.active_view(SERIAL) is not None

    # Real named load (even queued into empty slot A3) cancels Auto for that spool
    pending.load_or_wait(m, sm, SERIAL, "A3", sm.sp["id"], False)
    assert astore.active_view(SERIAL) is None


def test_deferred_named_load_cancels_a_newer_auto_for_the_same_spool(rig, sm, astore, clock, monkeypatch):
    m, _, _ = rig
    waiting = pending.Store(clock, start_thread=False)
    monkeypatch.setattr(pending, "STORE", waiting)
    pending.load_or_wait(m, sm, SERIAL, "A3", sm.sp["id"], False)
    astore.start(m, sm, SERIAL, sm.sp["id"], dry=False)
    _push(rig, _report(a2=FULL, bits="30007"))
    waiting.tick()
    clock.t += pending.SETTLE + 1
    waiting.tick()
    assert waiting.jobs()[SERIAL, "A3"].applied
    assert astore.active_view(SERIAL) is None


def test_auto_skips_a_slot_reserved_for_a_named_spool(rig, sm, astore, clock, monkeypatch):
    m, _, _ = rig
    waiting = pending.Store(clock, start_thread=False)
    monkeypatch.setattr(pending, "STORE", waiting)
    waiting.add(m, sm, SERIAL, "A3", sm.sp2["id"])
    astore.start(m, sm, SERIAL, sm.sp["id"], dry=False)
    _push(rig, _report(a2=FULL, bits="30007"))
    waiting.tick()
    astore.tick()
    clock.t += pending.SETTLE + 1
    named_sending, finish_named = threading.Event(), threading.Event()
    sends = []
    def load(_manager, _sm, serial, label, spool_id, dry, **kw):
        sends.append(spool_id)
        if spool_id == sm.sp2["id"]:
            named_sending.set()
            assert finish_named.wait(5)
        return {"confirmed": True}

    automatic = threading.Thread(target=astore.tick, daemon=True)
    named = threading.Thread(target=waiting.tick, daemon=True)
    monkeypatch.setattr(loading, "load", load)
    named.start()
    try:
        assert named_sending.wait(5)
        automatic.start()
        automatic.join(5)
        assert not automatic.is_alive()
        assert sends == [sm.sp2["id"]]
    finally:
        finish_named.set()
        named.join(5)
        if automatic.ident is not None:
            automatic.join(5)
    assert not named.is_alive() and not automatic.is_alive()
    astore.tick()
    assert sends == [sm.sp2["id"]]
    assert waiting.jobs()[SERIAL, "A3"].applied
    assert astore.active_view(SERIAL)["state"] == "waiting"


def test_an_applied_auto_ends_other_printers_waiting_for_the_same_spool(rig, sm, astore, clock, monkeypatch):
    m, _, _ = rig
    registry.upsert(Printer(SERIAL2, "Garage P1S", "C12", "10.0.0.6", "11112222", True))
    monkeypatch.setattr(m, "state", lambda serial: "connected")
    report = m.status(SERIAL)
    actual_status = m.status
    monkeypatch.setattr(m, "status", lambda serial: report if serial == SERIAL2 else actual_status(serial))
    monkeypatch.setattr(loading, "load", lambda *args, **kw: {"confirmed": True})
    sid = sm.sp["id"]
    astore.start(m, sm, SERIAL, sid, dry=False)
    astore.start(m, sm, SERIAL2, sid, dry=False)
    _push(rig, _report(a2=FULL, bits="30007"))
    astore.tick()
    clock.t += auto_slot.SETTLE + 1
    astore.tick()
    assert astore.jobs() == {}


# --- Klipper live watch + SlotInUse retry -----------------------------------------------------------------------------

def test_klipper_afc_status_annotation_and_live_watch_and_slot_in_use_retry(moonraker, sm, ev_sink, clock, astore,
                                                                           monkeypatch):
    monkeypatch.setattr(loads, "CONFIRM_TIMEOUT", 1.5)
    orig_run_gcode = moonraker._run_gcode

    def afc_gcode(script: str):
        err = orig_run_gcode(script)
        if err is None and script.startswith("SET_SPOOL_ID "):
            parts = dict(kv.split("=", 1) for kv in script.split()[1:])
            sid = int(parts["SPOOL_ID"]) if parts["SPOOL_ID"] else None
            moonraker.push({f"AFC_lane {parts['LANE']}": {"spool_id": sid}})
        return err

    monkeypatch.setattr(moonraker, "_run_gcode", afc_gcode)
    moonraker.objects = json.loads((DATA_KLIPPER / "afc" / "objects.json").read_text())["objects"]
    moonraker.status = json.loads((DATA_KLIPPER / "afc" / "status.json").read_text())
    pid = registry.add_klipper("Box Turtle", moonraker.host, moonraker.port).serial

    kf = KlipperFamily(sm, backoff=(0.05, 0.2))
    kf.start()
    try:
        assert kf.wait_settled(pid, 8) == "unknown_model"
        st = kf.status(pid)
        assert st["auto_possible"] is True
        assert all(sl["watchable"] is True for u in st["ams"] for sl in u["slots"])

        astore.start(kf, sm, pid, sm.sp["id"], dry=False)
        assert kf.status(pid)["auto"]["state"] == "waiting"

        # Temporarily force SlotInUse while printing so _apply stays waiting and retries
        real_load = kf.load
        calls = []

        def flaky_load(sm_arg, p_arg, lab, sid, dry, **kw):
            calls.append(lab)
            if len(calls) == 1:
                raise loading.SlotInUse("in use while printing")
            return real_load(sm_arg, p_arg, lab, sid, dry, **kw)

        monkeypatch.setattr(kf, "load", flaky_load)

        # Push lane3 from load=false to load=true via Moonraker live status
        moonraker.push({"AFC_lane lane3": {
            "name": "lane3", "unit": "Turtle_1", "lane": 3, "map": "T2",
            "load": True, "prep": True, "tool_loaded": False,
            "material": "", "spool_id": None, "color": "#000000", "weight": 0, "status": "Ready",
        }})
        for _ in range(40):
            if kf.status(pid)["ams"][0]["slots"][2]["present"] is True:
                break
            time.sleep(0.05)

        astore.tick()
        clock.t += auto_slot.SETTLE + 0.5
        astore.tick()
        # First attempt raised SlotInUse -> job stays alive in waiting
        assert calls == ["lane3"] and pid in astore.jobs()

        # Next tick retries and succeeds
        astore.tick()
        assert len(calls) == 2 and astore.jobs() == {}
        evs = [e for e in ev_sink.read(kinds=("spool.loaded",))["events"] if str(e["data"].get("spool")) == str(sm.sp["id"])]
        assert evs and evs[0]["data"].get("auto") == "yes"
    finally:
        kf.stop()


# --- Kit API routes ---------------------------------------------------------------------------------------------------

def test_api_auto_routes_reserved_label_and_delete_drop(sm, rig, clock, astore):
    m, _, _ = rig
    app = create_app(sm, printers=m)
    c = TestClient(app)
    phone_h = {"Authorization": "Bearer " + pairing.redeem_code(pairing.issue().code)}
    web_h = {"Authorization": "Bearer " + pairing.redeem_code(pairing.issue("web (MCP)", role=pairing.WEB_ROLE).code)}

    # Unlisted/reserved "auto" (any case) in named slot load/unload returns 400 invalid_slot
    for label in ("Auto", "AUTO"):
        r = c.post(f"/api/printers/{SERIAL}/slots/{label}/load",
                   json={"spool_id": sm.sp["id"], "dry_run": True}, headers=phone_h)
        assert r.status_code == 400 and r.json()["error"] == "invalid_slot"
    for label in ("auto", "Auto"):
        r = c.post(f"/api/printers/{SERIAL}/slots/{label}/unload", json={"dry_run": True}, headers=phone_h)
        assert r.status_code == 400 and r.json()["error"] == "invalid_slot"

    # Exact lowercase /slots/auto/load hits auto_load BEFORE {label}/load (with web token)
    dry_r = c.post(f"/api/printers/{SERIAL}/slots/auto/load",
                   json={"spool_id": sm.sp["id"], "dry_run": True}, headers=web_h)
    assert dry_r.status_code == 200 and dry_r.json()["auto"] is True and dry_r.json()["dry_run"] is True

    # Start live Auto assignment
    live_r = c.post(f"/api/printers/{SERIAL}/slots/auto/load",
                    json={"spool_id": sm.sp["id"], "dry_run": False}, headers=phone_h)
    assert live_r.status_code == 200 and live_r.json()["pending"] is True

    # Printer list & detail include auto_possible and auto
    plist = c.get("/api/printers", headers=web_h)
    assert plist.status_code == 200
    pv = next(p for p in plist.json()["printers"] if p["serial"] == SERIAL)
    assert pv["auto_possible"] is True and pv["auto"]["spool_id"] == sm.sp["id"]
    pget = c.get(f"/api/printers/{SERIAL}", headers=web_h)
    assert pget.status_code == 200 and pget.json()["auto"]["state"] == "waiting"
    assert pget.json()["printer"]["auto_possible"] is True

    # Two slots qualify -> auto/choose succeeds
    _push(rig, _report(a2=FULL, a3=FULL, bits="3000f"))
    astore.tick()
    assert c.post(f"/api/printers/{SERIAL}/auto/choose", json={"slot": "auto"}, headers=web_h).status_code == 400
    ch = c.post(f"/api/printers/{SERIAL}/auto/choose", json={"slot": "A3"}, headers=web_h)
    assert ch.status_code == 200 and ch.json()["confirmed"] is True
    assert astore.active_view(SERIAL) is None

    # Cancel when idle -> 409 not_waiting; start again & cancel with empty body -> 200
    nc = c.post(f"/api/printers/{SERIAL}/auto/cancel", content=b"", headers=web_h)
    assert nc.status_code == 409 and nc.json() == {"error": "not_waiting", "message": nc.json()["message"]}
    c.post(f"/api/printers/{SERIAL}/slots/auto/load",
           json={"spool_id": sm.sp["id"], "dry_run": False}, headers=web_h)
    ok = c.post(f"/api/printers/{SERIAL}/auto/cancel", content=b"", headers=web_h)
    assert ok.status_code == 200 and ok.json() == {"cancelled": True}

    # DELETE printer drops active Auto assignment
    c.post(f"/api/printers/{SERIAL}/slots/auto/load",
           json={"spool_id": sm.sp["id"], "dry_run": False}, headers=web_h)
    assert astore.active_view(SERIAL) is not None
    del_r = c.delete(f"/api/printers/{SERIAL}", headers=phone_h)
    assert del_r.status_code == 200
    assert astore.active_view(SERIAL) is None


def test_api_klipper_slot_load_cancels_active_auto(sm, astore):
    class FakeKlipper:
        confirmed = False

        def state(self, pid): return "unknown_model"
        def verified(self, pid): return False
        def camera_kind(self, pid): return None
        def model(self, pid):
            class A:
                name = "afc"
            return (None, None, A())
        def status(self, pid):
            return auto_slot.annotate(pid, {
                "family": "klipper",
                "gcode_state": "IDLE",
                "ams": [{"unit": 0, "kind": "lane", "slots": [
                    {"label": "lane1", "present": False}, {"label": "lane2", "present": False}]}],
            }, adapter="afc")
        def summary(self, pid):
            return {"gcode_state": "IDLE", "percent": 0, "remaining_min": 0, "job": None}
        def load(self, sm_arg, pid, label, spool_id, dry, *, sealed_location):
            return {"slot": {"label": label}, "preset": None, "dry_run": dry,
                    "problems": [], "spool": {"id": spool_id}, "confirmed": None if dry else self.confirmed, "moved_back": []}

    registry.upsert(Printer("KP1", "Box Turtle", "generic", "10.0.0.9", "", True, family="klipper"))
    kf = FakeKlipper()
    printers = family.Printers(None, kf)
    app = create_app(sm, printers=printers)
    c = TestClient(app)
    h = {"Authorization": "Bearer " + pairing.redeem_code(pairing.issue().code)}

    r = c.post("/api/printers/KP1/slots/auto/load", json={"spool_id": sm.sp["id"], "dry_run": False}, headers=h)
    assert r.status_code == 200
    assert astore.active_view("KP1") is not None

    # dry load on lane1 keeps Auto
    assert c.post("/api/printers/KP1/slots/lane1/load",
                  json={"spool_id": sm.sp["id"], "dry_run": True}, headers=h).status_code == 200
    assert astore.active_view("KP1") is not None

    # A real load that the printer did not confirm leaves Auto waiting.
    assert c.post("/api/printers/KP1/slots/lane1/load",
                  json={"spool_id": sm.sp["id"], "dry_run": False}, headers=h).json()["confirmed"] is False
    assert astore.active_view("KP1") is not None

    kf.confirmed = True
    assert c.post("/api/printers/KP1/slots/lane1/load",
                  json={"spool_id": sm.sp["id"], "dry_run": False}, headers=h).status_code == 200
    assert astore.active_view("KP1") is None
