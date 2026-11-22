"""EV3 request guard (station/guard.py, station/http.py): each route for each caller class, the
429 of wrong tickets and its reset, the tickets, a cookie of another station, the shape of every refusal."""
from __future__ import annotations

import base64
import json
import socket

import pytest

from filament_kit.station import guard, page
from test_ev3_station_page import OPEN_FROM_HUB, Station  # noqa: F401  (the same real listener and clock)


@pytest.fixture
def stations(tmp_path):
    made = []

    def make(allow, sub="s"):
        d = tmp_path / sub
        d.mkdir(exist_ok=True)
        s = Station(d, allow)
        made.append(s)
        return s
    yield make
    for s in made:
        s.close()


def session_of(station):
    """A session cookie obtained the way the page does: a ticket (made here, as the Hub's call would) and POST /session."""
    status, d, r = station.json("POST", "/session", {"ticket": station.access.new_ticket()})
    assert status == 200
    return {"Cookie": r.getheader("Set-Cookie").split(";")[0]}


def refusal_ok(d):
    assert set(d) == {"error", "message"} and d["error"] and d["message"], d      # never {error, text}, never a bare word


# ---- the table of the ruling: route x caller

PAGE_FILES = ["/", "/static/station.js", "/static/station.css"]
SESSION_READS = ["/state", "/settings", "/photo"]


def test_the_page_files_are_for_the_allow_list_and_every_home_address_and_for_no_one_else(stations, monkeypatch):
    listed = stations({"127.0.0.1"}, "l")
    home = stations(set(), "h")
    outside = stations(set(), "o")
    cookie = session_of(outside)
    for path in PAGE_FILES:
        assert listed.call("GET", path)[0] == 200
        assert home.call("GET", path)[0] == 200
        assert home.call("GET", path, None, session_of(home))[0] == 200
    monkeypatch.setattr(guard, "is_home", lambda a, networks: False)
    for path in PAGE_FILES:
        status, d, _ = outside.json("GET", path, None, cookie)
        assert status == 403
        refusal_ok(d)
    assert listed.call("GET", "/")[0] == 200            # the allow-list is not a range: it still gets the page


@pytest.mark.parametrize("path", SESSION_READS)
def test_state_settings_and_photo_need_the_allow_list_or_a_session(stations, monkeypatch, path):
    listed, home, with_session = stations({"127.0.0.1"}, "l"), stations(set(), "h"), stations(set(), "s")
    cookie = session_of(with_session)
    outside = stations(set(), "o")
    out_cookie = session_of(outside)
    assert listed.call("GET", path)[0] == 200                                  # the allow-list: as today
    status, d, _ = home.json("GET", path)                                      # a home address with no session: 403
    assert status == 403
    refusal_ok(d)
    assert home.json("GET", path, None, {"Cookie": f"{page.COOKIE}=forged"})[0] == 403
    assert with_session.call("GET", path, None, cookie)[0] == 200              # with its session: the data
    monkeypatch.setattr(guard, "is_home", lambda a, networks: False)
    status, d, _ = outside.json("GET", path, None, out_cookie)                 # outside the ranges: nothing, whatever the cookie
    assert status == 403
    refusal_ok(d)


def test_last_tag_and_ticket_are_never_given_to_a_session(stations, monkeypatch):
    listed, with_session = stations({"127.0.0.1"}, "l"), stations(set(), "s")
    cookie = session_of(with_session)
    assert listed.call("GET", "/last-tag")[0] == 200
    status, d, _ = with_session.json("GET", "/last-tag", None, cookie)
    assert status == 403
    refusal_ok(d)
    assert with_session.json("GET", "/last-tag")[0] == 403
    proof = with_session.proof("ticket")                      # even with the Hub's right proof, from a session caller
    status, d, _ = with_session.json("POST", "/ticket", None, {**cookie, **proof})
    assert status == 403
    refusal_ok(d)
    assert listed.json("POST", "/ticket", None, listed.proof("ticket"))[0] == 200


def test_session_post_and_settings_for_each_caller(stations, monkeypatch):
    listed, home, with_session = stations({"127.0.0.1"}, "l"), stations(set(), "h"), stations(set(), "s")
    cookie = session_of(with_session)
    body = json.dumps({"volume": 40}).encode()
    assert listed.json("POST", "/settings", {"volume": 40}, listed.proof("settings", body))[0] == 200
    assert listed.json("POST", "/settings", {"volume": 40})[0] == 403         # the allow-list still needs the proof or a session
    status, d, _ = home.json("POST", "/settings", {"volume": 40})
    assert status == 403
    refusal_ok(d)
    assert home.json("POST", "/settings", {"volume": 40}, home.proof("settings", body))[0] == 403    # a proof is the Hub's only
    assert with_session.json("POST", "/settings", {"volume": 40}, cookie)[0] == 200
    assert with_session.target["speaker_volume"] == 40
    for station in (listed, home):                                              # /session: any home address (and the list)
        assert station.json("POST", "/session", {"ticket": station.access.new_ticket()})[0] == 200
    outside = stations(set(), "o")
    ticket = outside.access.new_ticket()
    monkeypatch.setattr(guard, "is_home", lambda a, networks: False)
    status, d, _ = outside.json("POST", "/session", {"ticket": ticket})
    assert status == 403
    refusal_ok(d)
    assert outside.access.open_session(ticket) is not None                    # (the ticket was not spent by the refusal)


def test_anything_else_is_403_for_a_home_address_and_404_for_the_allow_list(stations):
    listed, home, with_session = stations({"127.0.0.1"}, "l"), stations(set(), "h"), stations(set(), "s")
    cookie = session_of(with_session)
    for method in ("GET", "POST"):
        for path in ("/nope", "/ticket/", "/state/x", "/static/other.js", "/last-tags"):
            assert listed.json(method, path, {} if method == "POST" else None)[0] == 404
            for station, headers in ((home, None), (with_session, cookie)):
                status, d, _ = station.json(method, path, {} if method == "POST" else None, headers)
                assert status == 403, (method, path)
                refusal_ok(d)
    status, d, _ = with_session.json("POST", "/state", {}, cookie)               # a read route takes no POST from a session
    assert status == 403
    refusal_ok(d)


def test_every_error_the_station_gives_has_error_and_message(stations):
    listed = stations({"127.0.0.1"}, "l")
    seen = []
    for method, path, body, headers in (("GET", "/nope", None, None), ("POST", "/nope", {}, None), ("POST", "/ticket", {}, None),
                                        ("POST", "/session", {"ticket": "x"}, None), ("POST", "/settings", {"volume": 1}, None),
                                        ("POST", "/settings", {"volume": 999}, listed.proof("settings", b'{"volume": 999}'))):
        status, d, _ = listed.json(method, path, body, headers)
        assert status >= 400, (method, path)
        refusal_ok(d)
        seen.append(status)
    assert {400, 403, 404} <= set(seen)
    assert listed.call("POST", "/session", raw=b"x" * (page.MAX_BODY + 1))[0] == 413
    refusal_ok(json.loads(listed.call("POST", "/session", raw=b"x" * (page.MAX_BODY + 1))[2]))


def test_the_allow_lists_own_behaviour_is_unchanged(stations):
    listed = stations({"127.0.0.1"}, "l")
    for path in ("/state", "/last-tag", "/photo", "/settings"):
        assert listed.call("GET", path)[0] == 200
    assert listed.json("GET", "/state")[1]["tag"]["nfc_id"] == "04:aa"
    assert listed.json("POST", "/ticket", None, listed.proof("ticket"))[0] == 200
    assert listed.json("POST", "/ticket")[0] == 403                           # still needs the proof


# ---- the wrong tickets: 10 a minute per address, then 429

def wrong(station, n=1):
    return [station.json("POST", "/session", {"ticket": "not-a-ticket-" + "x" * 30})[0] for _ in range(n)]


def test_ten_wrong_tickets_are_403_then_429_for_the_rest_of_the_minute(stations):
    s = stations(set(), "h")
    assert wrong(s, 10) == [403] * 10
    status, d, _ = s.json("POST", "/session", {"ticket": "another-wrong-one-" + "y" * 30})
    assert status == 429
    refusal_ok(d)
    assert d["error"] == "too_many_wrong_tickets"
    good = s.access.new_ticket()
    assert s.json("POST", "/session", {"ticket": good})[0] == 429             # a right one is refused too, until the minute is over
    s.clock.t += 30
    assert wrong(s) == [429]
    s.clock.t += 31                                                          # the minute is over: the count starts again
    good = s.access.new_ticket()
    assert s.json("POST", "/session", {"ticket": good})[0] == 200
    assert wrong(s, 10) == [403] * 10 and wrong(s) == [429]


def test_a_used_an_expired_and_a_forged_ticket_each_count_as_wrong_and_a_right_one_does_not(stations):
    s = stations(set(), "h")
    old = s.access.new_ticket()
    s.clock.t += 61
    t = s.access.new_ticket()
    assert s.json("POST", "/session", {"ticket": t})[0] == 200
    assert s.json("POST", "/session", {"ticket": t})[0] == 403               # used: 1 wrong
    assert s.json("POST", "/session", {"ticket": old})[0] == 403             # expired: 2 wrong
    assert s.json("POST", "/session", {"ticket": "A" * 43})[0] == 403        # forged, well-formed: 3
    assert s.json("POST", "/session", {"ticket": None})[0] == 403            # 4
    assert s.wrong._seen["127.0.0.1"][1] == 4
    for _ in range(12):                                                      # right tickets are never counted
        assert s.json("POST", "/session", {"ticket": s.access.new_ticket()})[0] == 200
    assert s.wrong._seen["127.0.0.1"][1] == 4


def test_the_table_is_per_address_and_cannot_grow_without_limit():
    t = [1000.0]
    table = guard.WrongTickets(lambda: t[0])
    for _ in range(guard.WRONG_PER_MINUTE):
        table.wrong("192.168.1.5")
    assert table.refused("192.168.1.5") and not table.refused("192.168.1.6")
    for i in range(5000):
        table.wrong(f"10.{i // 250}.{i % 250}.1")
    assert len(table._seen) <= guard.MAX_ADDRESSES
    t[0] += guard.WINDOW_SECONDS
    assert not table.refused("192.168.1.5")
    for i in range(5000):
        t[0] += 0.001
        table.wrong(f"10.9.{i // 250}.{i % 250}")
    assert len(table._seen) <= guard.MAX_ADDRESSES


# ---- tickets and sessions

def test_a_ticket_has_at_least_128_bits_is_one_use_and_lives_60_seconds(stations):
    s = stations(set(), "h")
    tickets = [s.access.new_ticket() for _ in range(page.MAX_TICKETS)]
    assert len(set(tickets)) == page.MAX_TICKETS
    for t in tickets:
        raw = base64.urlsafe_b64decode(t + "=" * (-len(t) % 4))
        assert len(raw) * 8 >= 128
    assert page.TICKET_SECONDS == 60 and page.SESSION_SECONDS == 3600
    first, second = tickets[:2]
    assert s.access.open_session(first) is not None and s.access.open_session(first) is None      # once
    s.clock.t += 59.9
    assert s.access.open_session(second) is not None                                              # still good at 59.9 s
    third = s.access.new_ticket()
    s.clock.t += 60
    assert s.access.open_session(third) is None                                                   # gone at 60 s


def test_a_session_cookie_from_another_stations_origin_is_useless(stations):
    a, b = stations(set(), "a"), stations(set(), "b")
    cookie_a = session_of(a)
    assert a.call("GET", "/state", None, cookie_a)[0] == 200
    assert b.json("GET", "/state", None, cookie_a)[0] == 403
    assert b.json("POST", "/settings", {"volume": 1}, cookie_a)[0] == 403
    assert page.cookie_header("x").count("Domain") == 0                      # a host-only cookie: the browser keeps it to its origin


# ---- the ranges

@pytest.mark.parametrize("address,home", [
    ("127.0.0.1", True), ("127.5.5.5", True), ("::1", True), ("10.0.0.1", True), ("10.255.255.255", True),
    ("172.16.0.1", True), ("172.31.255.255", True), ("172.32.0.1", False), ("172.15.255.255", False),
    ("192.168.0.9", True), ("192.169.0.1", False), ("169.254.1.1", True), ("169.253.1.1", False),
    ("fc00::1", True), ("fdff::1", True), ("fe00::1", False), ("fe80::1", True), ("fe80::1%eth0", True),
    ("::ffff:192.168.1.5", True), ("::ffff:8.8.8.8", False), ("8.8.8.8", False), ("100.64.0.1", False),
    ("2001:db8::1", False), ("", False), ("not an address", False), ("0.0.0.0", False)])
def test_which_addresses_count_as_home(address, home):
    assert guard.is_home(address) is home


def test_the_allow_list_outranks_the_ranges():
    assert guard.caller_class("203.0.113.9", {"203.0.113.9"}) == guard.LISTED       # a VPN address on the list
    assert guard.caller_class("192.168.1.9", {"203.0.113.9"}) == guard.HOME
    assert guard.caller_class("203.0.113.9", set()) == guard.OUTSIDE


# ---- the page on an address of the allow-list: data only while its session lasts

def test_the_page_on_an_allow_listed_address_is_refused_once_its_session_has_ended(stations):
    s = stations({"127.0.0.1"}, "l")
    for path in ("/state", "/settings", "/photo"):
        assert s.call("GET", path)[0] == 200                                  # the Hub and other plain callers: as today
        assert s.call("GET", path + "?x=1&page=1")[0] == 403                  # the page's own call, no session: refused
    cookie = session_of(s)
    for path in ("/state", "/settings", "/photo"):
        assert s.call("GET", path + "?page=1", None, cookie)[0] == 200
    s.clock.t += 3601                                                         # the session ends
    status, d, _ = s.json("GET", "/state?page=1", None, cookie)
    assert status == 403
    refusal_ok(d)
    assert s.call("GET", "/state")[0] == 200                                  # not the Hub's call
    js = (page.STATIC / "station.js").read_text(encoding="utf-8")
    assert 'fetch("state?page=1"' in js and 'fetch("settings?page=1"' in js and 'photo?page=1' in js
    assert '$("status").textContent = ""' in js and 'removeAttribute("src")' in js and "clearInterval(timer)" in js


def test_the_configured_page_networks_change_page_access_without_granting_hub_rights():
    ranges = guard.page_networks("100.64.10.0/24 2001:db8:1::/64")
    for address in ("100.64.10.21", "::ffff:100.64.10.21", "2001:db8:1::6"):
        assert guard.caller_class(address, (), ranges) == guard.HOME
        assert guard.caller_class(address, {guard.peer_address(address)}, ranges) == guard.LISTED
        assert not guard.allowed(guard.HOME, "GET", "/last-tag", True)
        assert guard.allowed(guard.HOME, "POST", "/session", False)
    assert guard.caller_class("100.64.11.21", (), ranges) == guard.OUTSIDE
    assert guard.caller_class("192.168.0.21", (), ranges) == guard.OUTSIDE


def test_empty_or_invalid_page_networks_allow_only_listed_peers():
    for value in ("", "192.168.0.0/16 invalid"):
        ranges = guard.page_networks(value)
        assert guard.caller_class("192.168.0.21", (), ranges) == guard.OUTSIDE
        assert guard.caller_class("::ffff:127.0.0.1", {"127.0.0.1"}, ranges) == guard.LISTED


def test_installed_home_and_subnets_include_loopback():
    from filament_kit import apply

    for mode in ("home", "subnets"):
        _, installed = apply.page_ranges({"station": {"page_from": mode}}, lambda: ["192.0.2.0/24"])
        ranges = guard.page_networks(" ".join(installed))
        assert guard.caller_class("::1", (), ranges) == guard.HOME
        assert guard.caller_class("127.0.0.2", (), ranges) == guard.HOME


@pytest.mark.skipif(not socket.has_dualstack_ipv6(), reason="the host has no dual-stack IPv6 listener")
def test_the_station_listener_accepts_both_ipv4_and_ipv6(monkeypatch):
    from http.server import BaseHTTPRequestHandler
    from filament_kit.station import __main__ as station_main

    def no_cert():
        raise OSError("test certificate unavailable")

    monkeypatch.setattr(station_main.st, "ensure_cert", no_cert)
    server, fp, plain = station_main.make_server(0, BaseHTTPRequestHandler)
    try:
        assert fp is None and plain
        for host in ("127.0.0.1", "::1"):
            with socket.create_connection((host, server.server_port), timeout=1):
                pass
    finally:
        server.server_close()
