"""Dry-box / workshop humidity tracking, alert settings, and 3% hysteresis checks."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from filament_kit import alerts, alerts_api, event_kinds, event_sink, watch
from filament_kit.bridge import layout, registry, status
from filament_kit.event_sink import StoreSink
from filament_kit.events import EventStore
from filament_kit.station_client import StationUnreachable


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for name in ("etc", "state", "backups"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    alerts.reset()
    yield tmp_path
    alerts.reset()
    event_sink.set_sink(None)


@pytest.fixture
def store(env):
    return EventStore(env / "state" / "events")


@pytest.fixture
def sink(store):
    s = StoreSink(store)
    event_sink.set_sink(s)
    return s


def _kinds(store):
    return [e["kind"] for e in store.read()["events"]]


# ---------------------------------------------------------------- bridge layout & status

def test_layout_and_status_preserve_ams_humidity_and_temp():
    report = {
        "ams": {
            "ams": [
                {
                    "id": "0",
                    "humidity": "4",
                    "humidity_raw": "58",
                    "temp": "25.4",
                    "tray": [{"id": "0", "tray_type": "PLA", "tray_color": "FF0000FF"}],
                },
                {
                    "id": "128",
                    "humidity": 2,
                    "humidity_raw": 31,
                    "temp": 29.0,
                    "tray": [{"id": "0", "tray_type": "PETG", "tray_color": "00FF00FF"}],
                },
            ]
        },
        "vt_tray": {"id": "254", "tray_type": "TPU", "tray_color": "0000FFFF"},
    }
    lay = layout.build(report)
    by_id = {u.ams_id: u for u in lay.units}
    assert (by_id[0].humidity, by_id[0].humidity_raw, by_id[0].temp) == (4, 58, 25.4)
    assert (by_id[128].humidity, by_id[128].humidity_raw, by_id[128].temp) == (2, 31, 29.0)
    assert (by_id[254].humidity, by_id[254].humidity_raw, by_id[254].temp) == (None, None, None)

    st = status.summarize(report, [])
    st_by_id = {u["unit"]: u for u in st["ams"]}
    assert (st_by_id[0]["humidity"], st_by_id[0]["humidity_raw"], st_by_id[0]["temp"]) == (4, 58, 25.4)
    assert (st_by_id[128]["humidity"], st_by_id[128]["humidity_raw"], st_by_id[128]["temp"]) == (2, 31, 29.0)
    assert (st_by_id[254]["humidity"], st_by_id[254]["humidity_raw"], st_by_id[254]["temp"]) == (None, None, None)


# ---------------------------------------------------------------- event_kinds, alerts & alerts_api

def test_event_kinds_include_humidity_high_and_ok():
    hi = event_kinds.get("humidity.high")
    ok = event_kinds.get("humidity.ok")
    assert hi is not None and ok is not None
    assert hi.severity == "warn" and hi.condition == event_kinds.RAISES and hi.base == "humidity-high"
    assert ok.severity == "ok" and ok.condition == event_kinds.CLEARS and ok.base == "humidity-high"
    assert event_kinds.condition_key(hi, "p1", "ams:p1:0") == "humidity-high:ams:p1:0"


def test_alerts_humidity_default_xml_and_validation(env):
    s = alerts.load()
    assert s["types"]["humidity"] is True
    assert s["humidity_high_pct"] == 55

    saved = alerts.save({"types": {"humidity": False}, "humidity_high_pct": 60})
    assert saved["types"]["humidity"] is False and saved["humidity_high_pct"] == 60
    alerts.reset()
    reloaded = alerts.load()
    assert reloaded["types"]["humidity"] is False and reloaded["humidity_high_pct"] == 60

    with pytest.raises(alerts.AlertsError):
        alerts.save({"humidity_high_pct": 9})
    with pytest.raises(alerts.AlertsError):
        alerts.save({"humidity_high_pct": 96})

    # Missing <humidity> element in an older XML falls back to 55
    (env / "state" / "alerts.xml").write_text('<alerts version="1"><type key="humidity" on="yes"/></alerts>',
                                              encoding="utf-8")
    alerts.reset()
    assert alerts.load()["humidity_high_pct"] == 55


def test_alerts_api_view_save_and_words():
    view = alerts_api.alerts_view()
    assert view["humidity_high_pct"] == 55
    assert any(t["key"] == "humidity" and t["on"] is True for t in view["types"])

    updated = alerts_api.save({"humidity_high_pct": 48, "types": {"humidity": False}})
    assert updated["humidity_high_pct"] == 48
    assert any(t["key"] == "humidity" and t["on"] is False for t in updated["types"])

    with pytest.raises(alerts_api.AlertsRefused):
        alerts_api.save({"humidity_high_pct": 120})

    ev_station = {
        "kind": "humidity.high",
        "severity": "warn",
        "subject": {"type": "device", "id": "st-1", "name": "Bench Station"},
        "data": {"rh": 58, "threshold": 55, "source": "Bench Station"},
    }
    title, body = alerts_api.words(ev_station)
    assert "Bench Station" in title and "58" in body and "55" in body
    assert alerts_api.action_of(ev_station) == {"place": "devices", "id": "st-1"}

    ev_ams = {
        "kind": "humidity.ok",
        "severity": "ok",
        "subject": {"type": "printer", "id": "01P00A", "name": "Workshop X1"},
        "data": {"rh": 51, "threshold": 55, "source": "Workshop X1 AMS A", "unit": "A"},
    }
    title_ok, body_ok = alerts_api.words(ev_ams)
    assert "Workshop X1 AMS A" in title_ok and "51" in body_ok
    assert alerts_api.action_of({"kind": "humidity.high", "subject": {"type": "printer", "id": "01P00A"}}) == {
        "place": "printer", "id": "01P00A"
    }


# ---------------------------------------------------------------- check_humidity & Loop

def test_check_humidity_station_hysteresis(sink, store):
    devs = [{"id": "st-1", "type": "station", "name": "Bench Station"}]
    rh = [54.0]
    read = lambda _url: {"air": {"temp_c": 22.0, "rh": rh[0]}}

    # Below 55%: no event
    watch.check_humidity(sink, "http://station.local", devs, station_state=read)
    assert _kinds(store) == []

    # At 55%: triggers humidity.high
    rh[0] = 55.0
    watch.check_humidity(sink, "http://station.local", devs, station_state=read)
    assert _kinds(store) == ["humidity.high"]
    assert store.is_raised("humidity-high:station:st-1")

    # Within 3% hysteresis band (54%, 53%): stays raised, no duplicate high, no ok
    rh[0] = 53.0
    watch.check_humidity(sink, "http://station.local", devs, station_state=read)
    assert _kinds(store) == ["humidity.high"]
    assert store.is_raised("humidity-high:station:st-1")

    # At <= 52% (55 - 3): clears with humidity.ok
    rh[0] = 52.0
    watch.check_humidity(sink, "http://station.local", devs, station_state=read)
    assert _kinds(store) == ["humidity.high", "humidity.ok"]
    assert not store.is_raised("humidity-high:station:st-1")


def test_check_humidity_station_unreachable_is_ignored(sink, store):
    devs = [{"id": "st-1", "type": "station", "name": "Bench Station"}]

    def fail(_url):
        raise StationUnreachable("down")

    watch.check_humidity(sink, "http://station.local", devs, station_state=fail)
    assert _kinds(store) == []


def test_check_humidity_bambu_ams_hysteresis(sink, store, monkeypatch):
    pr = registry.Printer(serial="01P00A", ip="10.0.0.5", access_code="12345678", name="Workshop X1",
                          configured=True, family="bambu")
    monkeypatch.setattr(registry, "load", lambda: {"01P00A": pr})

    ams_units = [
        {"unit": 0, "label": "A", "kind": "ams", "humidity": 4, "humidity_raw": 60, "temp": 24.0},
        {"unit": 1, "label": "B", "kind": "ams", "humidity": 2, "humidity_raw": None, "temp": 23.0},
        {"unit": 254, "label": "Ext", "kind": "ext", "humidity": None, "humidity_raw": None, "temp": None},
    ]

    class FakeBambu:
        def status(self, serial):
            return {"ams": ams_units} if serial == "01P00A" else None

    bambu = FakeBambu()
    watch.check_humidity(sink, bambu=bambu)
    assert _kinds(store) == ["humidity.high"]
    assert store.is_raised("humidity-high:ams:01P00A:0")
    assert not store.is_raised("humidity-high:ams:01P00A:1")

    # Drop to 53% (still > 52): stays raised
    ams_units[0]["humidity_raw"] = 53
    watch.check_humidity(sink, bambu=bambu)
    assert _kinds(store) == ["humidity.high"]

    # Drop to 51% (<= 52): clears
    ams_units[0]["humidity_raw"] = 51
    watch.check_humidity(sink, bambu=bambu)
    assert _kinds(store) == ["humidity.high", "humidity.ok"]
    assert not store.is_raised("humidity-high:ams:01P00A:0")


def test_loop_tick_runs_check_humidity(sink, store, env, monkeypatch):
    monkeypatch.setattr(alerts.shutil, "disk_usage",
                        lambda p: SimpleNamespace(total=100 * 10**9, used=50 * 10**9, free=50 * 10**9))
    pr = registry.Printer(serial="01P00A", ip="10.0.0.5", access_code="12345678", name="Workshop X1",
                          configured=True, family="bambu")
    monkeypatch.setattr(registry, "load", lambda: {"01P00A": pr})

    class FakeBambu:
        def status(self, serial):
            return {"ams": [{"unit": 0, "label": "A", "kind": "ams", "humidity": 4, "humidity_raw": 62, "temp": 25.0}]}

    loop = watch.Loop(sink, devices=lambda: [], bambu=FakeBambu())
    loop.tick()
    assert _kinds(store) == ["humidity.high"]
