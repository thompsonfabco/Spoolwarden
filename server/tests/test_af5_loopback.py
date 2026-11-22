"""AF5 (R82 ruled 2026-11-18 00:28): a station on the Hub's own machine is called as it answers, the not-on-HTTPS row is
never shown for it, the no-downgrade rule still holds, and `status` does not exit 1 for it."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from filament_kit import config, pairing, status
from filament_kit.station import http as station_http
from filament_kit.station import state as st
from filament_kit.station_client import StationCertChanged, StationClient

STATE = {"tag": "04:aa", "tag_at": None, "held": None, "scale": None, "scale_at": None, "air": None, "air_at": 0,
         "last": None, "last_at": None, "held_at": None, "panel": None, "panel_version": None, "panel_nfc": None,
         "flow": {"step": "idle"}, "photo": None, "photo_at": None}


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    monkeypatch.setattr(st, "tls_dir", lambda: tmp_path / "station-state" / "tls")
    pairing.make_cert("fkhost", ["127.0.0.1"], tmp_path / "etc")


@pytest.fixture
def servers():
    made, hits = [], []

    def https():
        cert, key, fp = st.ensure_cert(None)
        srv = station_http.TlsServer(("127.0.0.1", 0), station_http.make_handler(lambda: dict(STATE), {"127.0.0.1"}),
                                     station_http.tls_context(cert, key))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        made.append(srv)
        return srv.server_port, fp

    def plain():
        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                data = json.dumps({**STATE, "tag": {"nfc_id": STATE["tag"]}}).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass
        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        made.append(srv)
        return srv.server_port

    yield https, plain, hits
    for s in made:
        s.shutdown()
        s.server_close()


def paired(fp=None, port=None):
    p = pairing.issue("station", scope="station")
    dev, _ = pairing.authorize(p.qr.split("t=")[1].split("&")[0])
    if fp:
        assert pairing.record_station_hello(dev, fp, port, "0.6.2")


def cfg_for(url):
    cfg = config.load()
    cfg["modules"] = {"station": True}
    cfg["station"] = {"url": url}
    return cfg


def station_rows(url):
    return [r for r in status.satellite_rows(cfg_for(url))]


def test_a_loopback_station_with_a_fingerprint_is_called_over_pinned_https_and_has_no_row(servers):
    https, _plain, hits = servers
    port, fp = https()
    paired(fp, port)
    url = "http://127.0.0.1:5003"                      # the configured address; the pairing's port and certificate win
    assert StationClient(url, backoff=0).state()["tag"]["nfc_id"] == "04:aa" and hits == []
    assert station_rows(url) == []


def test_a_loopback_station_whose_certificate_changed_still_raises_never_goes_plain_and_says_so(servers):
    https, plain, hits = servers
    port, _fp = https()
    paired("ab" * 32, port)
    plain_port = plain()
    url = f"http://127.0.0.1:{plain_port}"
    with pytest.raises(StationCertChanged):
        StationClient(url, backoff=0).state()
    assert hits == []                                  # no downgrade to the plain listener
    (row,) = station_rows(url)                         # the changed-certificate row is not the exempt row
    assert row.state == "fail" and row.key == "devices.station-certificate"


def test_a_loopback_station_without_a_fingerprint_is_called_over_plain_http_with_no_row_and_no_exit_1(servers, capsys):
    _https, plain, hits = servers
    port = plain()
    paired()
    url = f"http://127.0.0.1:{port}"
    assert StationClient(url, backoff=0).state()["tag"]["nfc_id"] == "04:aa" and hits == ["/state"]
    assert station_rows(url) == []
    rows = list(status.station_rows(url))
    assert not any(r.state == status.FAIL for r in rows)


def test_a_station_on_another_machine_with_no_fingerprint_gets_the_row(servers):
    paired()
    (row,) = station_rows("http://192.0.2.5:5003")     # unreachable here, but "plain" is decided by the pairing alone
    assert row.state == "fail" and row.key == "devices.station-https"
    assert "192.0.2.5:5003 is not using HTTPS" in row.text
