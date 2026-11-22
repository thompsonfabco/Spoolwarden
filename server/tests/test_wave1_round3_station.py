"""Wave round 3, the Weigh Station: `page_from` in GET /settings and STATION_PAGE_FROM reaching the handler
(the behaviour of 02.2), and the handler's refusal sentences from the catalogue (D13)."""
from __future__ import annotations

import json
import re
import threading
import types
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from filament_kit.station import guard, page, settings
from filament_kit.station import http as station_http

CATALOGUE = json.loads((Path(__file__).resolve().parents[2] / "strings" / "en.json").read_text(encoding="utf-8"))


def _view():
    return {"tag": None, "tag_at": None, "scale": None, "scale_at": None, "last": None, "last_at": None, "held": None,
            "held_at": None, "air": None, "air_at": 0, "photo": None, "photo_at": None, "panel": None,
            "panel_version": None, "panel_nfc": None, "flow": {"step": "idle", "message": "", "spool": None, "filament": None}}


def _settings_answer(tmp_path, **kw) -> dict:
    store = settings.Store(tmp_path / "settings.xml", {"beep": True, "speech": True, "tts": "auto", "speaker_volume": 50,
                                                       "panel_swap": False}, {})
    srv = ThreadingHTTPServer(("127.0.0.1", 0), station_http.make_handler(_view, {"127.0.0.1"}, None, store, **kw))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/settings", timeout=5) as r:
            return json.load(r)
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.parametrize("page_from", ["home", "subnets", "allowed"])
def test_get_settings_answers_the_page_access_value(tmp_path, page_from):
    got = _settings_answer(tmp_path, page_ranges="", page_from=page_from)
    assert got["page_from"] == page_from
    assert [s["id"] for s in got["settings"]][:2] == ["beep", "speech"]


def test_an_unknown_page_access_value_is_answered_as_home(tmp_path):
    assert _settings_answer(tmp_path, page_ranges="", page_from="everyone")["page_from"] == "home"


def test_main_hands_station_page_from_to_the_handler(monkeypatch, tmp_path):
    from filament_kit.station import __main__ as station_main

    class Stop(Exception):
        pass

    seen = {}

    def spy(view, allow, access=None, store=None, **kw):
        seen.update(kw)
        raise Stop
    monkeypatch.setenv("STATION_PAGE_FROM", "subnets")
    monkeypatch.setenv("STATION_PAGE_RANGES", "192.0.2.0/24")
    monkeypatch.setattr(station_main.st, "state_dir", lambda: tmp_path)
    monkeypatch.setattr(station_main.beep, "beep_device", lambda: None)
    monkeypatch.setattr(station_main.camera, "camera_dev", lambda: None)
    monkeypatch.setattr(station_main.flow, "api", lambda: types.SimpleNamespace(paired=True))
    monkeypatch.setattr(station_main.http, "make_handler", spy)
    with pytest.raises(Stop):
        station_main.main()
    assert seen == {"page_ranges": "192.0.2.0/24", "page_from": "subnets"}
    monkeypatch.delenv("STATION_PAGE_FROM")
    with pytest.raises(Stop):
        station_main.main()
    assert seen["page_from"] == "home"


def test_the_handlers_refusal_sentences_are_catalogue_texts():
    """station/http.py says no sentence of its own: each refusal is guard.TEXTS, which reads the catalogue."""
    source = Path(station_http.__file__).read_text(encoding="utf-8")
    assert not re.findall(r'_error\([^)]*"[A-Z][^"]* [^"]*"', source)
    for key, text in (("not_json", "The request is not a JSON object."),
                      ("too_many_waiting_tickets", "Too many tickets are waiting. Try again in a minute."),
                      ("ticket_not_valid", "That ticket is not valid.")):
        assert CATALOGUE[f"station_refusal_{key}"] == text == guard.TEXTS[key]


def test_the_station_answers_a_wrong_ticket_and_a_body_that_is_not_an_object_in_catalogue_words(tmp_path):
    access = page.Access(lambda: page.token_key("T0ken_for_the_station_pairing_0123456789"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), station_http.make_handler(_view, set(), access))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        def post(body: bytes):
            req = urllib.request.Request(f"http://127.0.0.1:{srv.server_port}/session", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                urllib.request.urlopen(req, timeout=5)
            except urllib.error.HTTPError as e:
                return e.code, json.load(e)
            raise AssertionError("refused expected")
        assert post(b'{"ticket": "' + b"x" * 40 + b'"}') == (403, {"error": "forbidden",
                                                                  "message": CATALOGUE["station_refusal_ticket_not_valid"]})
        assert post(b"[1]") == (400, {"error": "bad_request", "message": CATALOGUE["station_refusal_not_json"]})
    finally:
        srv.shutdown()
        srv.server_close()


def test_the_ruled_texts_of_round_3():
    """The texts as the rulings give them."""
    c = CATALOGUE
    readers = "Tag Readers set up for this Hub must be set up again."
    assert readers not in c["settings_certificate_body"]              # the public certificate: readers do not check it
    assert c["cli_certificate_made"] == "Made a new certificate. " + readers
    assert c["station_page_page_from_subnets_note"] == "If this machine's network has changed, run: sudo {command} features apply"
    assert page.TEXTS["station_page_page_from_subnets_note"] == \
        "If this machine's network has changed, run: sudo spoolwarden features apply"
    assert c["devices_station_allow_list_only"] == "This Weigh Station answers only the addresses on its allow-list."
    assert c["cli_activity_empty_ever"] == "Nothing has been done yet."
    assert "200" not in c["cli_activity_more"]
    for key in ("station_page_unreachable", "station_settings_remove_failed"):                  # no bare "station"
        assert re.sub(r"Weigh Station", "", c[key]).lower().count("station") == 0, key
    assert "intake" not in c["station_page_step"].lower()
    for key in ("station_page_no_photo", "station_page_on", "station_page_off"):              # nothing shows them
        assert key not in c and key not in page.TEXTS
    assert set(page.TEXTS) <= set(c)
