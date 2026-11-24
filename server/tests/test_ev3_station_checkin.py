"""(d): the station's 5-minute check-in (server_api.checkin, started by station/__main__.py) and the proof key read from
the station's token (server_api.proof_key)."""
from __future__ import annotations

import types

import pytest

from filament_kit import __version__
from filament_kit.station import page, server_api

FP = "ab" * 32


class Api:
    def __init__(self, answers, paired=True):
        self.answers, self.paired, self.sent = list(answers), paired, []

    def hello(self, values=None):
        self.sent.append(values)
        a = self.answers.pop(0) if self.answers else (200, {"ok": True})
        if isinstance(a, Exception):
            raise a
        return a


def run(api, rounds, **kw):
    waits = []
    taken = server_api.checkin(lambda: api, FP, 5003, sleep=waits.append, rounds=rounds, **kw)
    return taken, waits


def test_the_check_in_is_every_five_minutes_and_waits_before_the_first():
    assert server_api.CHECKIN_SECONDS == 300
    api = Api([])
    taken, waits = run(api, 3)
    assert taken == 3 and waits == [300, 300, 300] and len(api.sent) == 3


def test_each_check_in_is_the_hello_with_fp_port_and_version():
    api = Api([])
    run(api, 1)
    assert api.sent == [{"fp": FP, "port": 5003, "version": __version__}]
    api = Api([])
    run(api, 1, plain="no certificate")
    assert api.sent[0] == server_api.hello_values(FP, 5003, "no certificate")


def test_a_hub_that_does_not_answer_or_a_failed_round_does_not_end_the_loop():
    api = Api([(0, {"error": "unreachable"}), RuntimeError("boom"), (401, {"error": "bad token"}), (200, {"ok": True})])
    taken, waits = run(api, 4)
    assert taken == 1 and len(waits) == 4 and len(api.sent) == 4


def test_an_unpaired_station_calls_nobody_and_a_later_pairing_is_found():
    api = Api([], paired=False)
    taken, waits = run(api, 2)
    assert taken == 0 and api.sent == [] and len(waits) == 2
    seen = []

    def api_of():
        seen.append(1)
        return Api([], paired=len(seen) > 1)
    assert server_api.checkin(api_of, FP, 5003, sleep=lambda s: None, rounds=3) == 2


def test_the_loop_runs_until_stopped_when_it_has_no_bound():
    api = Api([])

    class Stop(Exception):
        pass

    def sleep(_):
        if len(api.sent) >= 2:
            raise Stop

    with pytest.raises(Stop):
        server_api.checkin(lambda: api, FP, 5003, sleep=sleep)
    assert len(api.sent) == 2


def test_main_starts_the_check_in_beside_the_first_hello(monkeypatch, tmp_path):
    from filament_kit.station import __main__ as station_main
    started = []

    class FakeThread:
        def __init__(self, target, args=(), kwargs=None, daemon=False):
            self.target, self.args, self.kwargs = target, args, kwargs

        def start(self):
            started.append(self)

    class FakeServer:
        def __init__(self, *a, **kw):
            pass

        def serve_forever(self):
            pass
    monkeypatch.setattr(station_main.st, "state_dir", lambda: tmp_path)       # (a Windows path has a colon: STATE_DIRECTORY is a list)
    monkeypatch.setattr(station_main, "threading", types.SimpleNamespace(Thread=FakeThread))
    monkeypatch.setattr(station_main.st, "ensure_cert", lambda: ("cert.pem", "key.pem", FP))
    monkeypatch.setattr(station_main.http, "tls_context", lambda cert, key: "context")
    monkeypatch.setattr(station_main.http, "TlsServer", FakeServer)
    monkeypatch.setattr(station_main, "signal", types.SimpleNamespace(SIGTERM=15, SIGINT=2, signal=lambda *a: None))
    monkeypatch.setattr(station_main.beep, "beep_device", lambda: None)
    monkeypatch.setattr(station_main.camera, "camera_dev", lambda: None)
    monkeypatch.setattr(station_main.flow, "api", lambda: types.SimpleNamespace(paired=True))
    seen = {}
    real = station_main.http.make_handler

    def spy(view, allow, access=None, store=None, **kw):
        seen.update(access=access, store=store)
        return real(view, allow, access, store, **kw)
    monkeypatch.setattr(station_main.http, "make_handler", spy)
    station_main.main()
    targets = [t.target for t in started]
    assert server_api.announce in targets and server_api.checkin in targets
    check = next(t for t in started if t.target is server_api.checkin)
    assert check.args[1:] == (FP, station_main.st.settings["port"]) and check.kwargs == {"plain": None}
    assert isinstance(seen["access"], page.Access) and seen["access"].key_of is server_api.proof_key
    assert seen["store"].path == tmp_path / "settings.xml"


def test_the_proof_key_comes_from_the_stations_token(monkeypatch, tmp_path):
    token = tmp_path / "station_token"
    monkeypatch.setattr(server_api, "token_file", lambda: token)
    assert server_api.proof_key() is None                      # no token: not paired
    token.write_text("\n", encoding="utf-8")
    assert server_api.proof_key() is None
    token.write_text("a_token_of_the_pairing_0123456789\n", encoding="utf-8")
    assert server_api.proof_key() == page.token_key("a_token_of_the_pairing_0123456789")
