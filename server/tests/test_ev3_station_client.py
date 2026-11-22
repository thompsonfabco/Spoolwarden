"""EV3 (e): the Hub's pinned, checked calls to the station's own routes (station_client.call / call_json / ticket /
read_state / read_settings / change_settings / read_photo), the proof of the pairing (prove-first item 3) and the one
deadline of a call. A real station handler over the real pinned TLS listener; the Hub side reads the real pairing."""
from __future__ import annotations

import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler

import pytest

from filament_kit import config, pairing, station_client
from filament_kit.station import http as station_http
from filament_kit.station import page, settings
from filament_kit.station import state as st
from filament_kit.station_client import StationCallError, StationClient, StationUnreachable

VIEW = {"tag": "04:aa", "tag_at": None, "held": None, "scale": None, "scale_at": None, "air": None, "air_at": 0,
        "last": None, "last_at": None, "held_at": None, "panel": None, "panel_version": None, "panel_nfc": None,
        "flow": {"step": "idle", "message": "", "spool": None, "filament": None}, "photo": b"\xff\xd8\xff\xe0jpeg",
        "photo_at": None}


@pytest.fixture(autouse=True)
def fk_env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    monkeypatch.setattr(st, "tls_dir", lambda: tmp_path / "station-state" / "tls")
    pairing.make_cert("fkhost", ["127.0.0.1"], tmp_path / "etc")


@pytest.fixture
def kit(tmp_path):
    made = []

    def pair(fp=None, port=None, version="0.6.2", scope="station"):
        p = pairing.issue("station", scope=scope)
        dev, _ = pairing.authorize(p.token)
        if fp:
            assert pairing.record_station_hello(dev, fp, port, version)
        return p.token

    def serve(handler):
        cert, key, fp = st.ensure_cert()
        srv = station_http.TlsServer(("127.0.0.1", 0), handler, station_http.tls_context(cert, key))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        made.append(srv)
        return srv.server_port, fp

    def real(token):
        """The real station handler with its own page.Access keyed from `token`, and a settings store in tmp_path."""
        target = {"beep": True, "speech": True, "tts": "auto", "speaker_volume": 50, "panel_swap": False}
        store = settings.Store(tmp_path / "station-settings.xml", target, {})
        access = page.Access(lambda: page.token_key(token))
        return station_http.make_handler(lambda: dict(VIEW), {"127.0.0.1"}, access, store), access, target

    yield types_ns(pair=pair, serve=serve, real=real)
    for s in made:
        s.shutdown()
        s.server_close()


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


def url(port=5003):
    return f"https://127.0.0.1:{port}"


def station_with_real_handler(kit):
    token = kit.pair()                                    # claimed pairing, no hello yet
    handler, access, target = kit.real(token)
    port, fp = kit.serve(handler)
    dev = next(d for d in pairing._load_devices() if d.get("claimed") is not None)
    assert pairing.record_station_hello(dev["id"], fp, port, "0.6.2")
    return token, access, target


# ---- prove-first item 3, end to end

def test_the_hubs_key_from_the_pairing_record_is_the_stations_key_from_its_token(kit):
    token = kit.pair("ab" * 32, 5003)
    assert station_client.proof_key() == page.token_key(token)
    stored = json.dumps(pairing._load_devices())
    assert token not in stored                                    # the Hub keeps the hash, not the token


def test_no_key_without_a_station_pairing_with_a_certificate_or_with_two(kit):
    assert station_client.proof_key() is None
    kit.pair()                                                    # no hello: no certificate told
    assert station_client.proof_key() is None
    kit.pair("ab" * 32, 5003)
    assert station_client.proof_key() is not None
    kit.pair("cd" * 32, 5004)
    assert station_client.proof_key() is None                     # two: which one would be a guess


def test_the_hub_gets_a_ticket_the_station_accepts_for_a_session(kit):
    _token, access, _ = station_with_real_handler(kit)
    got = station_client.ticket(url())
    assert got["expires_in"] == 60 and len(got["ticket"]) >= 30
    assert access.open_session(got["ticket"]) is not None
    assert access.open_session(got["ticket"]) is None             # once


def test_a_station_paired_with_another_token_refuses_the_proof(kit):
    kit.pair()
    handler, _access, _ = kit.real("a different token 0123456789 for the station")
    port, fp = kit.serve(handler)
    dev = next(d for d in pairing._load_devices() if d.get("claimed") is not None)
    assert pairing.record_station_hello(dev["id"], fp, port, "0.6.2")
    with pytest.raises(StationCallError) as e:
        station_client.ticket(url())
    assert e.value.status == 403 and "may not ask" in e.value.message


def test_the_hub_reads_state_and_settings_and_changes_them_with_the_proof(kit):
    _token, _access, target = station_with_real_handler(kit)
    state = station_client.read_state(url())
    assert state["version"] and state["tag"]["nfc_id"] == "04:aa" and state["photo"] is True and state["settings_changed"] is False
    listed, dropped = station_client.read_settings(url())
    assert dropped is False and [s["id"] for s in listed][:5] == ["beep", "speech", "voice", "volume", "panel_swap"]
    assert any(s["id"] == "lamp.idle" and s["kind"] == "choice" and "yellow" in s["choices"] for s in listed)
    changed, dropped = station_client.change_settings(url(), {"volume": 80, "lamp.weigh": "red"})
    assert {s["id"]: s["value"] for s in changed}["volume"] == 80 and target["speaker_volume"] == 80 and dropped is False
    assert station_client.read_state(url())["settings_changed"] is True
    back, _ = station_client.change_settings(url(), {"reset": True})
    assert {s["id"]: s["value"] for s in back}["volume"] == 50 and target["speaker_volume"] == 50


def test_the_stations_refusal_of_a_value_comes_back_with_its_reason(kit):
    station_with_real_handler(kit)
    with pytest.raises(StationCallError) as e:
        station_client.change_settings(url(), {"volume": 500})
    assert e.value.status == 400 and "volume" in e.value.message


def test_the_photo_is_read_as_a_jpeg_only(kit):
    station_with_real_handler(kit)
    assert station_client.read_photo(url()).startswith(b"\xff\xd8")


# ---- who is called, and how

def test_a_station_that_told_the_hub_no_certificate_is_never_called_or_given_a_proof(kit):
    kit.pair()                                                    # an older station: no hello, so no pin
    hits = []

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            hits.append(self.path)

        do_POST = do_GET

        def log_message(self, *a):
            pass
    port, _fp = kit.serve(H)
    for fn in (lambda: station_client.ticket(url(port)), lambda: station_client.read_state(url(port)),
               lambda: station_client.change_settings(url(port), {"volume": 1})):
        with pytest.raises(StationCallError) as e:
            fn()
        assert e.value.status == 503 and "certificate" in e.value.message
    assert hits == []


def test_a_changed_certificate_is_refused_with_the_error_and_the_fix(kit):
    token = kit.pair()
    handler, _a, _t = kit.real(token)
    port, _fp = kit.serve(handler)
    dev = next(d for d in pairing._load_devices() if d.get("claimed") is not None)
    assert pairing.record_station_hello(dev["id"], "ab" * 32, port, "0.6.2")      # the Hub trusts another certificate
    with pytest.raises(StationCallError) as e:
        station_client.ticket(url())
    assert station_client.TEXT_STATION_CERT_CHANGED in e.value.message and "pair it again" in e.value.message


def test_only_the_stored_host_with_the_stored_port_is_called(kit):
    _token, _a, _t = station_with_real_handler(kit)
    with pytest.raises(StationCallError) as e:
        station_client.read_state("")
    assert e.value.status == 503
    # the url's own port is ignored: the pairing's port is used (the url may still say the old plain port)
    assert station_client.read_state("http://127.0.0.1:9")["tag"]["nfc_id"] == "04:aa"


# ---- the limits of what a station may send

def serving(kit, body, ctype="application/json", status=200, extra=None, chunks=None):
    """A station that answers every GET and POST with `body` as it is told to (a misbehaving one). The pairing before it
    (a test may serve several in turn) is revoked: the Hub has one station."""
    for d in pairing.devices():
        pairing.revoke(d["id"])
    kit.pair()

    class H(BaseHTTPRequestHandler):
        def respond(self):
            n = int(self.headers.get("Content-Length") or 0)
            self.rfile.read(n)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            if chunks:
                self.end_headers()
                try:
                    for _ in range(chunks):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                        time.sleep(0.3)
                except OSError:
                    pass
                return
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = respond

        def log_message(self, *a):
            pass
    port, fp = kit.serve(H)
    dev = next(d for d in pairing._load_devices() if d.get("claimed") is not None)
    assert pairing.record_station_hello(dev["id"], fp, port, "0.6.2")
    return port


def test_an_answer_that_is_not_json_is_refused(kit):
    serving(kit, b"<html>hello</html>", "text/html")
    with pytest.raises(StationCallError, match="JSON"):
        station_client.read_state(url())
    serving(kit, b"[1,2]", "application/json")
    with pytest.raises(StationCallError, match="not valid"):
        station_client.read_state(url())


def test_an_answer_over_256_kib_is_refused(kit):
    serving(kit, {"x": "y" * (station_client.MAX_JSON + 10)})
    with pytest.raises(StationCallError, match="too large"):
        station_client.read_state(url())


def test_a_redirect_is_an_error_and_is_not_followed(kit):
    serving(kit, b"", status=302, extra={"Location": "https://elsewhere.example/"})
    with pytest.raises(StationCallError, match="redirect"):
        station_client.read_state(url())


def test_a_slow_answer_is_cut_at_the_one_deadline_of_the_whole_call(kit):
    serving(kit, b"", chunks=40)            # one byte every 0.3 s for 12 s: each read is quick, the call is not
    t = time.monotonic()
    with pytest.raises(StationCallError, match="too slowly"):
        station_client.call(url(), "GET", "/state", timeout=1.5)
    assert time.monotonic() - t < 4


def test_an_explicit_https_station_without_a_pin_is_never_sent_cleartext(monkeypatch):
    monkeypatch.setattr(station_client, "_station_info", lambda: None)

    def unexpected(*args, **kwargs):
        pytest.fail("an unpinned HTTPS Station must not be called")

    monkeypatch.setattr(station_client, "bounded_call", unexpected)
    url = "https://station.example:5003/"
    with pytest.raises(StationUnreachable, match="certificate"):
        StationClient(url, backoff=0).state()
    assert station_client.station_https(url) == "unreachable"


def test_legacy_http_station_reads_without_sending_a_proof(monkeypatch):
    monkeypatch.setattr(station_client, "_station_info", lambda: None)
    calls = []

    def receive(host, port, fp, method, path, body=None, headers=None, cap=0, timeout=5, plain=False):
        calls.append((host, port, fp, method, path, plain))
        assert headers == {"Accept": "*/*"} and body is None
        return 200, "application/json", b'{"photo":false}'

    monkeypatch.setattr(station_client, "bounded_call", receive)
    url = "http://station.example:5003"
    assert station_client.read_state(url) == {"photo": False}
    with pytest.raises(StationCallError, match="certificate"):
        station_client.ticket(url)
    assert calls == [("station.example", 5003, None, "GET", "/state", True)]


def test_the_call_deadline_also_bounds_dns(monkeypatch):
    started = threading.Event()
    release = threading.Event()

    def lookup(host, port, *, type, flags=0):
        if flags == socket.AI_NUMERICHOST:
            raise socket.gaierror(socket.EAI_NONAME, "not numeric")
        started.set()
        release.wait(3)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    before = time.monotonic()
    try:
        with pytest.raises(TimeoutError):
            station_client.bounded_call("station.example", 5003, None, "GET", "/state", timeout=0.4, plain=True)
        assert started.is_set() and time.monotonic() - before < 2
    finally:
        release.set()


def test_the_state_read_of_the_station_client_has_one_deadline_too(kit):
    serving(kit, b"", chunks=40)
    t = time.monotonic()
    with pytest.raises(StationUnreachable):
        StationClient(url(), timeout=1.5, backoff=0).state()
    assert time.monotonic() - t < 4


def test_a_photo_that_is_not_a_small_jpeg_is_refused(kit):
    serving(kit, b"GIF89a....", "image/gif")
    with pytest.raises(StationCallError, match="JPEG"):
        station_client.read_photo(url())
    serving(kit, b"not a picture", "image/jpeg")
    with pytest.raises(StationCallError, match="JPEG"):
        station_client.read_photo(url())
    serving(kit, b"\xff\xd8" + b"x" * (station_client.MAX_PHOTO + 5), "image/jpeg")
    with pytest.raises(StationCallError, match="too large"):
        station_client.read_photo(url())


def test_a_ticket_that_is_not_a_ticket_is_refused(kit):
    for bad in ({"ticket": "x", "expires_in": 60}, {"ticket": "a" * 40, "expires_in": 600}, {"ticket": 5, "expires_in": 60},
                {"ticket": "a" * 40, "expires_in": True}, {"ticket": "a b" * 20, "expires_in": 60}, {}):
        serving(kit, bad)
        with pytest.raises(StationCallError, match="ticket is not valid"):
            station_client.ticket(url())


def test_the_settings_a_station_sends_are_checked_and_the_odd_ones_dropped():
    good = {"id": "volume", "label": "Volume", "kind": "number", "value": 50, "min": 0, "max": 100}
    odd = [{"id": "Bad Id", "label": "x", "kind": "switch", "value": True},
           {"id": "a", "label": "x", "kind": "weird", "value": 1},
           {"id": "b", "label": "y" * 81, "kind": "switch", "value": True},
           {"id": "c", "label": "x", "kind": "switch", "value": "yes"},
           {"id": "d", "label": "x", "kind": "number", "value": True},
           {"id": "e" * 41, "label": "x", "kind": "text", "value": "t"},
           {"id": "volume", "label": "dup", "kind": "number", "value": 1}, "not a dict", None]
    out, dropped = station_client.clean_settings([good, *odd])
    assert out == [good] and dropped is True
    out, dropped = station_client.clean_settings([{"id": f"s{i}", "label": "x", "kind": "switch", "value": True} for i in range(70)])
    assert len(out) == 60 and dropped is True
    assert station_client.clean_settings(good) == ([], True) and station_client.clean_settings([]) == ([], False)
    choice = {"id": "voice", "label": "Voice", "kind": "choice", "value": "auto", "choices": ["auto", "espeak"]}
    assert station_client.clean_settings([choice]) == ([choice], False)
