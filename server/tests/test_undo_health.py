"""Unit tests for HD1 undo, health history, event filters, viewer role, and history_cli."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone

import pytest

from filament_kit import (
    config,
    event_kinds,
    event_sink,
    events,
    health,
    history_cli,
    inventory,
    pairing,
    undo,
)


class _StubSM:
    def __init__(self, spools=None):
        self.spools = {s["id"]: dict(s) for s in (spools or [])}

    def get(self, path: str):
        if path == "spool":
            return list(self.spools.values())
        if path.startswith("spool/"):
            sid = int(path.split("/", 1)[1])
            return dict(self.spools[sid])
        raise KeyError(path)

    def patch(self, path: str, body: dict):
        if path.startswith("spool/"):
            sid = int(path.split("/", 1)[1])
            s = self.spools[sid]
            for k, v in body.items():
                if k == "extra" and isinstance(v, dict):
                    s["extra"] = {**(s.get("extra") or {}), **v}
                else:
                    s[k] = v
            return dict(s)
        raise KeyError(path)


@pytest.fixture()
def store(tmp_path):
    st = events.EventStore(tmp_path / "events")
    event_sink.set_sink(event_sink.StoreSink(st))
    yield st
    event_sink.set_sink(None)


def _spool(sid=1, *, weight=500.0, location="Shelf A", archived=False, tag=""):
    return {
        "id": sid,
        "remaining_weight": weight,
        "initial_weight": 1000.0,
        "spool_weight": 200.0,
        "location": location,
        "archived": archived,
        "first_used": "2026-11-01T00:00:00Z",
        "comment": "",
        "extra": {"nfc_id": json.dumps(tag)} if tag else {},
        "filament": {"id": 10, "name": "PLA Basic", "material": "PLA", "vendor": {"name": "Bambu Lab"}},
    }


def test_undoable_kinds_and_undo_execution(store):
    sm = _StubSM([_spool(1, weight=500.0, location="Shelf A")])
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    # 1. spool.weighed
    ev_w = store.write("spool.weighed", {"type": "spool", "id": "1"}, "page",
                       {"spool": "1", "before_grams": "500", "after_grams": "420"}, now=now)
    sm.patch("spool/1", {"remaining_weight": 420.0})
    res = undo.undo_event(ev_w["id"], sm, store=store, now=now + timedelta(hours=1))
    assert res["undone"] is True
    assert res["event"]["undo_of"] == ev_w["id"]
    assert sm.spools[1]["remaining_weight"] == 500.0
    assert store.get(ev_w["id"])["undone"] is True

    # 2. spool.moved
    ev_m = store.write("spool.moved", {"type": "spool", "id": "1"}, "cli",
                       {"spool": "1", "before_location": "Shelf A", "after_location": "Shelf B"}, now=now)
    sm.patch("spool/1", {"location": "Shelf B"})
    res_m = undo.undo_event(ev_m["id"], sm, store=store, now=now + timedelta(hours=1))
    assert res_m["undone"] is True
    assert sm.spools[1]["location"] == "Shelf A"

    # 3. spool.emptied
    ev_e = store.write("spool.emptied", {"type": "spool", "id": "1"}, "page",
                       {"spool": "1", "before_grams": "500", "after_grams": "0", "before_archived": "false"},
                       now=now)
    sm.patch("spool/1", {"remaining_weight": 0.0, "archived": True})
    res_e = undo.undo_event(ev_e["id"], sm, store=store, now=now + timedelta(hours=1))
    assert res_e["undone"] is True
    assert sm.spools[1]["archived"] is False
    assert sm.spools[1]["remaining_weight"] == 500.0

    # 4. spool.loaded & spool.unloaded include printer note
    ev_l = store.write("spool.loaded", {"type": "spool", "id": "1"}, "page",
                       {"spool": "1", "before_location": "Shelf A", "after_location": "X1 · A1",
                        "before_slot": "", "after_slot": "A1"}, now=now)
    sm.patch("spool/1", {"location": "X1 · A1"})
    res_l = undo.undo_event(ev_l["id"], sm, store=store, now=now + timedelta(hours=1))
    assert res_l["note"] == undo.LOAD_UNLOAD_NOTE
    assert sm.spools[1]["location"] == "Shelf A"

    ev_u = store.write("spool.unloaded", {"type": "spool", "id": "1"}, "page",
                       {"spool": "1", "before_location": "X1 · A1", "after_location": "Shelf A",
                        "before_slot": "A1", "after_slot": ""}, now=now)
    res_u = undo.undo_event(ev_u["id"], sm, store=store, now=now + timedelta(hours=1))
    assert res_u["note"] == undo.LOAD_UNLOAD_NOTE
    assert sm.spools[1]["location"] == "X1 · A1"

    # 5. slot.cleared
    ev_c = store.write("slot.cleared", {"type": "spool", "id": "1"}, "page",
                       {"spool": "1", "slot": "A1", "before_location": "X1 · A1", "after_location": "Shelf A"},
                       now=now)
    sm.patch("spool/1", {"location": "Shelf A"})
    res_c = undo.undo_event(ev_c["id"], sm, store=store, now=now + timedelta(hours=1))
    assert res_c["undone"] is True
    assert sm.spools[1]["location"] == "X1 · A1"


def test_undo_spool_returned_puts_the_spool_back_as_checked_out(store):
    now = datetime.now(timezone.utc)
    sm = _StubSM([_spool(1, weight=500.0, location="Shelf A")])
    assert "spool.returned" in undo.UNDOABLE_KINDS
    ev = store.write("spool.returned", {"type": "spool", "id": "1"}, "page",
                     {"spool": "1", "before_location": "X1 · A1", "after_location": "Shelf A", "before_slot": "A1"},
                     now=now)
    res = undo.undo_event(ev["id"], sm, store=store, now=now + timedelta(hours=1))
    assert res["undone"] is True and res["note"] == undo.LOAD_UNLOAD_NOTE
    assert sm.spools[1]["location"] == "X1 · A1"
    assert json.loads(sm.spools[1]["extra"]["prev_location"]) == "Shelf A"       # checked out again, plain home


def test_undo_why_not_reasons(store):
    sm = _StubSM([_spool(2, weight=400.0, location="Shelf A")])
    now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    # not_undoable
    ev_bad = store.write("printer.error", {"type": "printer", "id": "p1"}, "hub",
                         {"code": "01", "text": "err"}, now=now)
    with pytest.raises(undo.UndoError) as exc_info:
        undo.undo_event(ev_bad["id"], sm, store=store, now=now)
    assert exc_info.value.code == "not_undoable"

    # too_old (> 7 days)
    old_ev = store.write("spool.moved", {"type": "spool", "id": "2"}, "page",
                         {"spool": "2", "before_location": "Shelf A", "after_location": "Shelf B"},
                         now=now - timedelta(days=8))
    assert undo.evaluate(old_ev, all_evs=[old_ev], now=now)["why_not"] == "too_old"

    # already_undone
    sm.patch("spool/2", {"location": "Shelf B"})
    ev_ok = store.write("spool.moved", {"type": "spool", "id": "2"}, "page",
                        {"spool": "2", "before_location": "Shelf A", "after_location": "Shelf B"}, now=now)
    undo.undo_event(ev_ok["id"], sm, store=store, now=now + timedelta(minutes=5))
    with pytest.raises(undo.UndoError) as exc_info2:
        undo.undo_event(ev_ok["id"], sm, store=store, now=now + timedelta(minutes=10))
    assert exc_info2.value.code == "already_undone"

    # changed_since
    ev1 = store.write("spool.weighed", {"type": "spool", "id": "2"}, "page",
                      {"spool": "2", "before_grams": "400", "after_grams": "350"}, now=now)
    store.write("spool.moved", {"type": "spool", "id": "2"}, "page",
                {"spool": "2", "before_location": "Shelf A", "after_location": "Shelf C"}, now=now)
    with pytest.raises(undo.UndoError) as exc_info3:
        undo.undo_event(ev1["id"], sm, store=store, now=now + timedelta(minutes=5))
    assert exc_info3.value.code == "changed_since"

    # 404
    with pytest.raises(undo.EventNotFound):
        undo.undo_event(999999, sm, store=store, now=now)


def test_event_id_present_on_real_mutation_and_absent_on_dry_run(store):
    sm = _StubSM([_spool(5, weight=500.0, location="Shelf A")])
    dry_res = inventory.edit_spool(sm, 5, remaining=450.0, dry=True)
    assert "event_id" not in dry_res

    real_res = inventory.edit_spool(sm, 5, remaining=450.0, dry=False)
    assert isinstance(real_res.get("event_id"), int)
    assert real_res["event"]["kind"] == "spool.weighed"


def test_events_read_filters_and_enrichment(store):
    t0 = datetime(2026, 11, 20, 10, 0, tzinfo=timezone.utc)
    store.write("print.started", {"type": "printer", "id": "p1"}, "hub", {"job": "a.3mf"}, now=t0)
    store.write("spool.moved", {"type": "spool", "id": "10"}, {"type": "device", "id": "d1", "name": "Phone"},
                {"spool": "10", "before_location": "A", "after_location": "B"}, now=t0)
    store.write("spool.weighed", {"type": "spool", "id": "11"}, "cli",
                {"spool": "11", "before_grams": "500", "after_grams": "400"}, now=t0)

    act = store.read(activity=True)
    assert [e["kind"] for e in act["events"]] == ["spool.moved", "spool.weighed"]
    assert all("undo" in e and "undone" in e for e in act["events"])

    by_subj = store.read(activity=True, subject="spool:10")
    assert len(by_subj["events"]) == 1 and by_subj["events"][0]["subject"]["id"] == "10"

    by_actor = store.read(activity=True, actor="device:d1")
    assert len(by_actor["events"]) == 1 and by_actor["events"][0]["actor"]["id"] == "d1"


def test_health_view_spans_and_changes(store):
    t0 = datetime(2026, 11, 15, 10, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 11, 18, 10, 0, tzinfo=timezone.utc)
    t2 = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
    store.write("check.failed", {"type": "hub", "id": "disk", "name": "Disk space"}, "hub",
                {"text": "Disk space low"}, key="disk", now=t1)
    store.write("check.ok", {"type": "hub", "id": "disk", "name": "Disk space"}, "hub",
                {"text": "Disk space low"}, key="disk", now=t2)

    hv = health.health_view(7, store=store, now=t2 + timedelta(days=1))
    assert hv["since"] == t1.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert len(hv["checks"]) == 1
    chk = hv["checks"][0]
    assert chk["key"] == "disk"
    assert chk["now"] == "ok"
    assert len(chk["spans"]) == 1
    assert chk["spans"][0]["state"] == "look"
    assert len(hv["changes"]) == 2
    assert hv["changes"][0]["to"] == "ok"


def test_pairing_viewer_role(tmp_path, monkeypatch):
    monkeypatch.setenv("FK_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("FK_ETC", str(tmp_path / "etc"))
    etc = tmp_path / "etc"
    etc.mkdir(parents=True, exist_ok=True)
    # Generate a minimal self-signed cert using cryptography
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=10))
        .sign(key, hashes.SHA256())
    )
    (etc / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))

    pr = pairing.issue("Home Assistant", role=pairing.VIEWER_ROLE)
    tok = pairing.redeem_code(pr.code)
    dev_id, scope = pairing.authorize(tok)
    assert scope == pairing.VIEWER_ROLE
    devs = pairing.devices()
    assert len(devs) == 1
    assert devs[0]["id"] == dev_id
    assert devs[0]["type"] == "home-assistant"


def test_history_cli_parsing():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd")
    history_cli.add_parser(sub)

    a1 = parser.parse_args(["activity", "--spool", "12", "--who", "Hub page", "--days", "30"])
    assert (a1.cmd, a1.spool, a1.who, a1.days) == ("activity", 12, "Hub page", 30)

    a2 = parser.parse_args(["undo", "42", "-y"])
    assert (a2.cmd, a2.event_id, a2.yes) == ("undo", 42, True)

    a3 = parser.parse_args(["usage", "--by", "month", "--printer", "X1"])
    assert (a3.cmd, a3.by, a3.printer) == ("usage", "month", "X1")

    a4 = parser.parse_args(["export", "activity", "--out", "out.csv", "--days", "7"])
    assert (a4.cmd, a4.dataset, a4.out, a4.days) == ("export", "activity", "out.csv", 7)

    a5 = parser.parse_args(["import", "spools", "spools.csv", "-y"])
    assert (a5.cmd, a5.kind, a5.file, a5.yes) == ("import", "spools", "spools.csv", True)

    a6 = parser.parse_args(["report", "--out", "rep.txt"])
    assert (a6.cmd, a6.out) == ("report", "rep.txt")
