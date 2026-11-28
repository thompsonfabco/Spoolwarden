"""The station's request guard. The systemd unit lets localhost, the private and link-local
ranges and the allow-list reach the program; the program decides, request by request, by the caller's address and cookie:

  caller                                        may ask
  on the allow-list (the Hub)                   everything, plus POST /ticket, /voice, /voice/test, /ota/update, /restart (each with its proof) and GET /ota/status
  other home address, no session                GET /, GET /static/*, POST /session
  other home address, with a session            also GET /state, GET /settings, POST /settings, POST /flow, GET /photo, GET /photo/*
  an address outside those ranges               nothing (the unit drops it first; this is the second line)

Everything else answers 403 `{"error": code, "message": sentence}`; /last-tag and /ticket are never given to a session.
`POST /session` takes at most WRONG_PER_MINUTE wrong tickets a minute from one address, then 429 for the rest of that minute.
"""
from __future__ import annotations

import ipaddress
import threading
import time

from . import page as _station_page

HOME_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    "127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "fc00::/7", "fe80::/10"))
WRONG_PER_MINUTE = 10
WINDOW_SECONDS = 60
MAX_ADDRESSES = 256          # the table of callers: it cannot grow without limit

LISTED, HOME, OUTSIDE = "listed", "home", "outside"
SESSION_GET = ("/state", "/settings", "/photo")           # what a session may read
TEXTS = {
    "forbidden": _station_page.catalogue_text("station_refusal_forbidden", "This address may not ask for that."),
    "not_found": _station_page.catalogue_text("station_refusal_not_found", "There is nothing here."),
    "too_many_wrong_tickets": _station_page.catalogue_text(
        "station_refusal_too_many_wrong_tickets", "Too many wrong tickets from this address. Try again in a minute."),
    "bad_request": _station_page.catalogue_text("station_refusal_bad_request", "The request is not valid."),
    "too_big": _station_page.catalogue_text("station_refusal_too_big", "The request is too big."),
    "too_many_waiting_tickets": _station_page.catalogue_text(
        "station_refusal_too_many_waiting_tickets", "Too many tickets are waiting. Try again in a minute."),
    "not_json": _station_page.catalogue_text("station_refusal_not_json", "The request is not a JSON object."),
    "ticket_not_valid": _station_page.catalogue_text("station_refusal_ticket_not_valid", "That ticket is not valid."),
}


def _ip(address: str):
    """Treat IPv4 peers on the dual-stack listener as IPv4, and discard IPv6 zone IDs."""
    try:
        ip = ipaddress.ip_address(str(address).split("%")[0])
    except ValueError:
        return None
    return ip.ipv4_mapped or ip if ip.version == 6 else ip


def peer_address(address: str) -> str:
    return str(_ip(address) or address)


def page_networks(value: str | None) -> tuple:
    """The connected ranges systemd admitted. An old drop-in keeps its previous home policy."""
    if value is None:
        return HOME_NETWORKS
    try:
        return tuple(ipaddress.ip_network(n) for n in value.split())
    except ValueError:
        return ()       # an invalid drop-in must not open more addresses


def is_home(address: str, networks=HOME_NETWORKS) -> bool:
    ip = _ip(address)
    return ip is not None and any(ip in n for n in networks if n.version == ip.version)


def caller_class(address: str, allow, networks=HOME_NETWORKS) -> str:
    return LISTED if peer_address(address) in allow else HOME if is_home(address, networks) else OUTSIDE


def allowed(caller: str, method: str, path: str, has_session: bool, from_page: bool = False) -> bool:
    """May this caller make this request? (The page's own files are tested by the handler: they are the same for every
    caller that is not OUTSIDE.) `path` has no query."""
    if caller == OUTSIDE:
        return False
    if caller == LISTED:
        # the station's own page (it adds `page=1` to what it asks) shows data only while it holds a session, even on an
        # address of the allow-list: when the session ends the page is refused and goes back to its sentence
        return has_session or not (from_page and method == "GET" and path in SESSION_GET)
    if method == "POST":
        return path == "/session" or (has_session and path in ("/settings", "/flow"))
    return has_session and (path in SESSION_GET or path.startswith("/photo/"))


class WrongTickets:
    """Wrong tickets per source address, per fixed minute: `refused(address)` is True once WRONG_PER_MINUTE were counted in
    the minute that began at the address's first wrong ticket; the count starts again after WINDOW_SECONDS. At most
    MAX_ADDRESSES addresses are kept (expired ones go first, then the oldest)."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self._lock = threading.Lock()
        self._seen: dict[str, list] = {}      # address -> [window start, wrong count]

    def _live(self, address: str, now: float):
        entry = self._seen.get(address)
        if entry and now - entry[0] >= WINDOW_SECONDS:
            del self._seen[address]
            return None
        return entry

    def refused(self, address: str) -> bool:
        with self._lock:
            entry = self._live(address, self.clock())
            return bool(entry and entry[1] >= WRONG_PER_MINUTE)

    def wrong(self, address: str) -> None:
        now = self.clock()
        with self._lock:
            entry = self._live(address, now)
            if entry:
                entry[1] += 1
                return
            if len(self._seen) >= MAX_ADDRESSES:
                self._seen = {a: e for a, e in self._seen.items() if now - e[0] < WINDOW_SECONDS}
                while len(self._seen) >= MAX_ADDRESSES:
                    del self._seen[min(self._seen, key=lambda a: self._seen[a][0])]
            self._seen[address] = [now, 1]
