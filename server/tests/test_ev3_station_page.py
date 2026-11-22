"""EV3 (b, c): the station's own page, its ticket and session, the settings routes and `version` in /state (station/page.py,
station/http.py). A real listener on loopback with the real handler; `allow` empty stands for an address that is not on the
station's allow-list (a browser elsewhere on the home network)."""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from filament_kit import __version__
from filament_kit.station import http as station_http
from filament_kit.station import guard, page, settings

TOKEN = "T0ken_for_the_station_pairing_0123456789"
OPEN_FROM_HUB = "Open this page from the Hub: Devices, this Weigh Station, Open this device's page."


class Clock:
    def __init__(self):
        self.t = 1_800_000_000.0

    def __call__(self):
        return self.t


def view():
    return {"tag": "04:aa", "tag_at": None, "scale": None, "scale_at": None, "last": None, "last_at": None, "held": None,
            "held_at": None, "air": None, "air_at": 0, "photo": b"\xff\xd8\xff\xe0jpg", "photo_at": None,
            "panel": "/dev/ttyACM0", "panel_version": "11", "panel_nfc": True,
            "flow": {"step": "idle", "message": "", "spool": None, "filament": None}}


class Station:
    def __init__(self, tmp_path, allow, key=None):
        self.clock = Clock()
        self.key = page.token_key(TOKEN) if key is None else key
        self.access = page.Access(lambda: self.key, clock=self.clock)
        self.target = {"beep": True, "speech": True, "tts": "auto", "speaker_volume": 50, "panel_swap": False}
        self.lamps: dict = {}
        self.store = settings.Store(tmp_path / "settings.xml", self.target, self.lamps)
        self.wrong = guard.WrongTickets(self.clock)
        handler = station_http.make_handler(view, allow, access=self.access, store=self.store, wrong_tickets=self.wrong)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def call(self, method, path, body=None, headers=None, raw=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        h = dict(headers or {})
        if data is not None:
            h.setdefault("Content-Type", "application/json")
        try:
            c.request(method, path, body=data, headers=h)
            r = c.getresponse()
            payload = r.read()
            return r.status, r, payload
        finally:
            c.close()

    def json(self, method, path, body=None, headers=None):
        status, r, payload = self.call(method, path, body, headers)
        try:
            return status, json.loads(payload), r
        except ValueError:
            return status, payload, r

    def proof(self, purpose, body=b"", **kw):
        return {page.PROOF_HEADER: page.make_proof(self.key, purpose, body, now=self.clock(), **kw)}

    def ticket(self):
        status, d, _ = self.json("POST", "/ticket", None, self.proof("ticket"))
        assert status == 200, d
        return d["ticket"]

    def session(self):
        status, d, r = self.json("POST", "/session", {"ticket": self.ticket()})
        assert status == 200 and d == {"ok": True}
        return {"Cookie": r.getheader("Set-Cookie").split(";")[0]}

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def hub_ok(tmp_path):
    """A station whose allow-list holds the caller (the Hub's address)."""
    s = Station(tmp_path, {"127.0.0.1"})
    yield s
    s.close()


@pytest.fixture
def elsewhere(tmp_path):
    """A station that does not list the caller: a browser on another address of the home network."""
    s = Station(tmp_path, set())
    yield s
    s.close()


# ---- prove-first item 3: both sides derive the key without the token travelling

def test_hkdf_is_the_rfc_5869_function():
    # RFC 5869 test case 3 (SHA-256, empty salt, empty info, 42 bytes)
    okm = page._hkdf(bytes([0x0B] * 22), b"", 42)
    assert okm.hex() == ("8da4e775a563c18f715f802a063c5a31b8a11f5c5ee1879ec3454e5f3c738d2d9d201395faa4b61a96c8")


def test_the_station_and_the_hub_derive_the_same_key_from_the_pairing_without_the_token():
    from filament_kit import pairing
    station_side = page.token_key(TOKEN)                         # the station has the token
    hub_side = page.proof_key(pairing._hash(TOKEN))              # the Hub keeps only this hash in the pairing record
    assert station_side == hub_side and len(station_side) == 32
    assert TOKEN.encode() not in station_side and pairing._hash(TOKEN) != station_side.hex()
    assert page.token_key(TOKEN + "x") != station_side           # another pairing: another key


# ---- the page's own files

def test_the_page_files_answer_any_address_and_hold_nothing_private(elsewhere):
    status, r, body = elsewhere.call("GET", "/")
    text = body.decode()
    assert status == 200 and r.getheader("Content-Type").startswith("text/html")
    assert "default-src 'none'" in r.getheader("Content-Security-Policy") and "frame-ancestors 'none'" in r.getheader("Content-Security-Policy")
    assert r.getheader("X-Content-Type-Options") == "nosniff" and r.getheader("Referrer-Policy") == "no-referrer"
    assert OPEN_FROM_HUB.replace("'", "\\u0027") in text or OPEN_FROM_HUB in text      # the page's texts are in its data block
    assert "{{" not in text and "<script>" not in text
    for path, kind in (("/static/station.js", "text/javascript"), ("/static/station.css", "text/css")):
        status, r, body = elsewhere.call("GET", path)
        assert status == 200 and r.getheader("Content-Type").startswith(kind) and body
    assert elsewhere.call("GET", "/static/../state")[0] in (403, 404)
    assert elsewhere.call("GET", "/static/station.js/../../state")[0] in (403, 404)
    assert elsewhere.call("GET", "/static/other.js")[0] == 403


@pytest.mark.parametrize("path", ["/state", "/settings", "/photo", "/last-tag", "/nope"])
def test_the_data_routes_refuse_an_address_off_the_list_and_no_session(elsewhere, path):
    status, d, _ = elsewhere.json("GET", path)
    assert status == 403 and set(d) == {"error", "message"} and d["error"] == "forbidden"


def test_an_address_on_the_list_reads_state_with_the_version_and_settings_changed(hub_ok):
    status, d, _ = hub_ok.json("GET", "/state")
    assert status == 200 and d["version"] == __version__ and d["settings_changed"] is False
    assert d["tag"]["nfc_id"] == "04:aa" and d["panel"] == "/dev/ttyACM0" and d["flow"]["step"] == "idle"
    assert d["photo"]["url"] == "/photo"                         # the old shape stays for what already reads it
    hub_ok.store.change({"volume": 20})
    assert hub_ok.json("GET", "/state")[1]["settings_changed"] is True
    assert hub_ok.call("GET", "/photo")[0] == 200 and hub_ok.call("GET", "/photo?x=1")[2].startswith(b"\xff\xd8")
    assert hub_ok.json("GET", "/nope")[0] == 404


# ---- ticket and session

def test_a_ticket_needs_an_address_on_the_list_and_the_proof(hub_ok, elsewhere):
    status, d, _ = hub_ok.json("POST", "/ticket", None, hub_ok.proof("ticket"))
    assert status == 200 and d["expires_in"] == 60 and re.fullmatch(r"[A-Za-z0-9_-]{30,}", d["ticket"])
    assert hub_ok.json("POST", "/ticket")[0] == 403                                       # no proof
    assert hub_ok.json("POST", "/ticket", None, {page.PROOF_HEADER: "garbage"})[0] == 403
    assert elsewhere.json("POST", "/ticket", None, elsewhere.proof("ticket"))[0] == 403    # right proof, wrong address
    wrong = {page.PROOF_HEADER: page.make_proof(page.token_key("another token 0123456789"), "ticket", now=hub_ok.clock())}
    assert hub_ok.json("POST", "/ticket", None, wrong)[0] == 403
    assert hub_ok.json("POST", "/ticket", None, hub_ok.proof("settings"))[0] == 403      # a proof is for one purpose


def test_a_proof_works_once_and_only_near_the_stations_time(hub_ok):
    h = hub_ok.proof("ticket")
    assert hub_ok.json("POST", "/ticket", None, h)[0] == 200
    assert hub_ok.json("POST", "/ticket", None, h)[0] == 403                              # the same proof again: a replay
    old = {page.PROOF_HEADER: page.make_proof(hub_ok.key, "ticket", now=hub_ok.clock() - 120)}
    assert hub_ok.json("POST", "/ticket", None, old)[0] == 403
    future = {page.PROOF_HEADER: page.make_proof(hub_ok.key, "ticket", now=hub_ok.clock() + 120)}
    assert hub_ok.json("POST", "/ticket", None, future)[0] == 403


def test_an_unpaired_station_gives_no_ticket(tmp_path):
    s = Station(tmp_path, {"127.0.0.1"})
    s.key = None
    try:
        h = {page.PROOF_HEADER: page.make_proof(page.token_key(TOKEN), "ticket", now=s.clock())}
        assert s.json("POST", "/ticket", None, h)[0] == 403
    finally:
        s.close()


def test_a_ticket_opens_one_session_with_a_cookie_of_its_own(hub_ok, elsewhere):
    t = hub_ok.ticket()
    status, d, r = hub_ok.json("POST", "/session", {"ticket": t})
    cookie = r.getheader("Set-Cookie")
    assert status == 200 and d == {"ok": True}
    assert cookie.startswith(f"{page.COOKIE}=") and page.COOKIE == "fk_station_session"
    for flag in ("HttpOnly", "Secure", "SameSite=Strict", "Path=/", "Max-Age=3600"):
        assert flag in cookie
    assert t not in cookie
    assert hub_ok.json("POST", "/session", {"ticket": t})[0] == 403                       # a ticket works once


def test_a_ticket_is_good_for_sixty_seconds(hub_ok):
    t = hub_ok.ticket()
    hub_ok.clock.t += 61
    assert hub_ok.json("POST", "/session", {"ticket": t})[0] == 403
    t = hub_ok.ticket()
    hub_ok.clock.t += 59
    assert hub_ok.json("POST", "/session", {"ticket": t})[0] == 200


@pytest.mark.parametrize("ticket", [None, "", "short", 5, ["x"], "a" * 200, "../../etc", "has space in it and more than twenty"])
def test_a_made_up_ticket_gives_nothing(hub_ok, ticket):
    assert hub_ok.json("POST", "/session", {"ticket": ticket})[0] == 403
    assert hub_ok.json("POST", "/session", {})[0] == 403


def test_a_browser_not_on_the_list_needs_its_session(elsewhere):
    # the ticket is asked on the Hub's address; here the caller stands for the browser, so mint one the way the code does
    t = elsewhere.access.new_ticket()
    status, d, r = elsewhere.json("POST", "/session", {"ticket": t})
    assert status == 200
    cookie = {"Cookie": r.getheader("Set-Cookie").split(";")[0]}
    assert elsewhere.json("GET", "/state", None, cookie)[0] == 200
    assert elsewhere.json("GET", "/settings", None, cookie)[1]["settings"][0]["id"] == "beep"
    assert elsewhere.call("GET", "/photo", None, cookie)[0] == 200
    assert elsewhere.json("GET", "/state", None, {"Cookie": f"{page.COOKIE}=guess"})[0] == 403
    assert elsewhere.json("GET", "/state", None, {"Cookie": f"other={cookie['Cookie']}"})[0] == 403


def test_a_session_ends_after_sixty_minutes_and_a_restart(elsewhere):
    t = elsewhere.access.new_ticket()
    cookie = {"Cookie": elsewhere.json("POST", "/session", {"ticket": t})[2].getheader("Set-Cookie").split(";")[0]}
    elsewhere.clock.t += 3599
    assert elsewhere.json("GET", "/state", None, cookie)[0] == 200
    elsewhere.clock.t += 2
    assert elsewhere.json("GET", "/state", None, cookie)[0] == 403
    fresh = page.Access(lambda: None, clock=elsewhere.clock)                 # a restarted station remembers nothing
    assert fresh.session_ok(cookie["Cookie"]) is False


def test_few_tickets_wait_at_once(hub_ok):
    for _ in range(page.MAX_TICKETS):
        hub_ok.ticket()
    status, d, _ = hub_ok.json("POST", "/ticket", None, hub_ok.proof("ticket"))
    assert status == 429
    hub_ok.clock.t += 61                                        # the waiting ones lapse
    assert hub_ok.json("POST", "/ticket", None, hub_ok.proof("ticket"))[0] == 200


def test_sessions_are_few_and_the_oldest_gives_way(hub_ok):
    first = hub_ok.session()
    for _ in range(page.MAX_SESSIONS):
        hub_ok.clock.t += 1
        hub_ok.session()
    assert hub_ok.access.session_ok(first["Cookie"]) is False


def test_a_session_call_from_another_site_is_refused(hub_ok):
    t = hub_ok.ticket()
    status, d, _ = hub_ok.json("POST", "/session", {"ticket": t}, {"Origin": "https://evil.example"})
    assert status == 403
    assert hub_ok.json("POST", "/session", {"ticket": t}, {"Origin": f"https://127.0.0.1:{hub_ok.port}"})[0] == 200


# ---- settings

def test_the_hub_reads_and_changes_settings_with_the_proof(hub_ok):
    status, d, _ = hub_ok.json("GET", "/settings")
    ids = [s["id"] for s in d["settings"]]
    assert status == 200 and ids[:5] == ["beep", "speech", "voice", "volume", "panel_swap"] and "lamp.idle" in ids
    body = json.dumps({"volume": 75, "lamp.lookup": "red"}).encode()
    status, d, _ = hub_ok.json("POST", "/settings", {"volume": 75, "lamp.lookup": "red"}, hub_ok.proof("settings", body))
    assert status == 200 and {s["id"]: s["value"] for s in d["settings"]}["volume"] == 75
    assert hub_ok.target["speaker_volume"] == 75 and hub_ok.lamps == {"lookup": "red"}


def test_a_change_without_the_proof_or_with_one_for_another_body_is_refused(hub_ok):
    assert hub_ok.json("POST", "/settings", {"volume": 75})[0] == 403
    other = hub_ok.proof("settings", json.dumps({"volume": 10}).encode())
    assert hub_ok.json("POST", "/settings", {"volume": 75}, other)[0] == 403
    assert hub_ok.json("POST", "/settings", {"volume": 75}, hub_ok.proof("ticket", json.dumps({"volume": 75}).encode()))[0] == 403
    assert hub_ok.target["speaker_volume"] == 50


def test_the_hub_resets_to_the_installed_values(hub_ok):
    hub_ok.store.change({"volume": 90})
    body = json.dumps({"reset": True}).encode()
    status, d, _ = hub_ok.json("POST", "/settings", {"reset": True}, hub_ok.proof("settings", body))
    assert status == 200 and hub_ok.target["speaker_volume"] == 50 and hub_ok.store.changed is False


def test_the_page_saves_with_its_session_over_json_only(elsewhere):
    cookie = {"Cookie": elsewhere.json("POST", "/session", {"ticket": elsewhere.access.new_ticket()})[2]
              .getheader("Set-Cookie").split(";")[0]}
    assert elsewhere.json("POST", "/settings", {"volume": 5})[0] == 403                           # no session
    assert elsewhere.json("POST", "/settings", {"volume": 5}, {**cookie, "Origin": "https://evil.example"})[0] == 403
    status, _, _ = elsewhere.call("POST", "/settings", raw=b'{"volume": 5}', headers={**cookie, "Content-Type": "text/plain"})
    assert status == 403
    status, d, _ = elsewhere.json("POST", "/settings", {"volume": 5}, cookie)
    assert status == 200 and elsewhere.target["speaker_volume"] == 5
    assert elsewhere.json("POST", "/settings", {"reset": True}, cookie)[0] == 200 and elsewhere.target["speaker_volume"] == 50


def test_a_bad_value_is_a_400_with_the_reason_and_nothing_changes(elsewhere):
    cookie = {"Cookie": elsewhere.json("POST", "/session", {"ticket": elsewhere.access.new_ticket()})[2]
              .getheader("Set-Cookie").split(";")[0]}
    status, d, _ = elsewhere.json("POST", "/settings", {"volume": 500}, cookie)
    assert status == 400 and "volume" in d["message"] and elsewhere.target["speaker_volume"] == 50
    assert elsewhere.json("POST", "/settings", {"nope": 1}, cookie)[0] == 400
    assert elsewhere.call("POST", "/settings", raw=b"[1]", headers=cookie)[0] == 400
    assert elsewhere.call("POST", "/settings", raw=b"{", headers=cookie)[0] == 400


def test_a_body_that_is_too_big_or_has_no_length_is_refused(hub_ok):
    status, _, _ = hub_ok.call("POST", "/session", raw=b"x" * (page.MAX_BODY + 1))
    assert status == 413
    c = http.client.HTTPConnection("127.0.0.1", hub_ok.port, timeout=10)
    c.putrequest("POST", "/session")
    c.endheaders()
    assert c.getresponse().status == 400
    c.close()


def test_no_other_post_route_exists(hub_ok):
    for path in ("/state", "/photo", "/nope", "/"):
        assert hub_ok.json("POST", path, {})[0] == 404


# ---- prove-first item 4: the page works at its own origin

ABSOLUTE = [r"""(?:src|href|action|data)\s*=\s*["']\s*(?:/|[a-z][a-z0-9+.-]*:)""",
            r"""(?:fetch|open|sendBeacon|replaceState|pushState)\s*\(\s*["'`]\s*/""",
            r"""(?:fetch|open|sendBeacon)\s*\(\s*["'`]\s*[a-z][a-z0-9+.-]*:""",
            r"""\.(?:src|href|action)\s*=\s*["'`]\s*(?:/|[a-z][a-z0-9+.-]*:)""",
            r"""url\(\s*["']?\s*(?:/|[a-z][a-z0-9+.-]*:)""", r"""@import\s+["']?\s*(?:/|[a-z]+:)""",
            r"""https?://""", r"""//[a-z0-9.-]+\.[a-z]{2,}"""]


def static_texts():
    folder = Path(page.__file__).with_name("static")
    files = sorted(p for p in folder.iterdir() if p.is_file())
    assert {p.name for p in files} == {"index.html", "station.js", "station.css"}
    return {p.name: p.read_text(encoding="utf-8") for p in files}


def test_the_pages_files_use_relative_addresses_only():
    for name, text in static_texts().items():
        if name == "station.js":
            text = re.sub(r"//[^\n]*", "", text)                     # comments
        for pattern in ABSOLUTE:
            assert not re.search(pattern, text, re.I), (name, pattern, re.search(pattern, text, re.I).group(0))


def test_the_scan_would_catch_an_absolute_address():
    for bad in ('<script src="/static/x.js">', "fetch('/state')", 'a.href = "/x"', "url(/img.png)", "http://x.example/",
                '<link href="https://x.example/a.css">', "@import '/a.css'", 'fetch("//x.example/a")'):
        assert any(re.search(p, bad, re.I) for p in ABSOLUTE), bad


def test_the_page_served_has_the_same_clean_addresses(elsewhere):
    text = elsewhere.call("GET", "/")[2].decode()
    assert 'href="static/station.css"' in text and 'src="static/station.js"' in text
    for pattern in ABSOLUTE[:2] + ABSOLUTE[5:7]:
        assert not re.search(pattern, text, re.I)
    assert "state" in static_texts()["station.js"] and 'fetch("state?page=1"' in static_texts()["station.js"]


def test_the_script_puts_station_text_on_the_page_as_text():
    js = static_texts()["station.js"]
    for risky in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "new Function", "setTimeout(\""):
        assert risky not in js
    assert "ticket" in js and "replaceState" in js            # the ticket leaves the address at once
    assert "load(true); return; }" in js                      # no ticket: it asks the station, which refuses a browser with no session


def test_the_page_says_to_open_it_from_the_hub_and_every_text_is_a_constant():
    assert page.TEXTS["station_page_open_from_hub"] == OPEN_FROM_HUB
    assert "station_page_open_from_hub" in static_texts()["station.js"]
    used = set(re.findall(r'T\["(station_page_[a-z_]+)"\]', static_texts()["station.js"]))
    assert used <= set(page.TEXTS) and "station_page_open_from_hub" in used
    # the page's own texts, and the words for the station's step and scale ids that its status rows show
    assert all(k.startswith(("station_page_", "station_step_", "station_scale_")) and v for k, v in page.TEXTS.items())


def test_without_a_session_the_page_shows_that_sentence_and_none_of_the_data(hub_ok, elsewhere):
    for station in (hub_ok, elsewhere):
        html = station.call("GET", "/")[2].decode()
        # every section that holds data starts hidden; only the note (filled with the sentence) is visible
        assert re.search(r'<section id="status-section" hidden>', html) and re.search(r'<section id="settings-section" hidden>', html)
        assert 'id="photo" alt="" hidden' in html
        assert json.dumps(OPEN_FROM_HUB, ensure_ascii=True).replace("'", "\u0027")[1:-1] in html or OPEN_FROM_HUB in html
    for path in ("/state", "/settings", "/photo", "/last-tag"):     # an address off the list with no (or a wrong) session
        assert elsewhere.call("GET", path, None, {"Cookie": f"{page.COOKIE}=none"})[0] == 403
    js = static_texts()["station.js"]
    assert js.count('T["station_page_open_from_hub"]') >= 2    # a browser with no session (403 on state), and a refused ticket
    assert "bare" not in js
    assert not [k for k, v in page.TEXTS.items() if re.search(r"stations?", v, re.I) and "Weigh Station" not in v]
