"""The demo's routes as a LIST for RC9 to mount (R85 6.1, 6.2, section 11 item 1); nothing here is mounted.

Each `Route` has a plain handler `fn(body: dict) -> (status, answer)`; the answer is the JSON object, a refusal is
`{"error": code, "message": sentence}`; the page route's refusal has the page's shape `{"error": sentence, "code": code}`
(R86 01:28). `who` says who may call it: "page" (the Hub page, unlocked), "paired" (any paired
phone or PC), "web" (the web app's twin: a web token). `web_token` says whether the web unit's token may call the kit
route (`api.WEB_ROUTES` gets `("POST", "/api/demo/leave")`). The kit routes `GET /api/info` and `GET /api/hub` gain
`demo: bool` from `hub_state()`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .. import demo
from . import DemoError, NotInDemo, is_on, leave, refusal, start, state

BAD_ON = "on must be true or false"


@dataclass(frozen=True)
class Route:
    method: str
    path: str
    fn: Callable[[dict], tuple[int, dict]]
    who: str
    web_token: bool = False


def hub_state() -> dict:
    """`{"demo": bool}`: added to the answers of `GET /api/info`, `GET /api/hub` and the page's state."""
    return state()


def post_demo(body: dict) -> tuple[int, dict]:
    """`POST /setup/api/demo` `{on: true|false}` -> `{demo: bool}`; 409 `not_empty` when the Hub has real data. A refusal
    is the page's shape: `{"error": sentence, "code": code}`."""
    on = (body or {}).get("on")
    if not isinstance(on, bool):
        return 400, {"error": BAD_ON, "code": "bad_request"}
    try:
        return 200, (start() if on else (leave() if is_on() else {"demo": False}))
    except DemoError as e:
        return 409, {"error": e.message, "code": e.code}


def post_leave(body: dict) -> tuple[int, dict]:
    """`POST /api/demo/leave` (and the twin `POST /intake/api/demo/leave`) -> `{demo: false}`; 409 `not_in_demo`."""
    try:
        return 200, leave()
    except DemoError as e:
        return 409, refusal(e)


def outside(_body: dict | None = None) -> tuple[int, dict]:
    """The answer of a route that would reach outside the Hub while the demo is on: 409 "Not in the demo."."""
    return 409, refusal(NotInDemo())


ROUTES: list[Route] = [
    Route("POST", "/setup/api/demo", post_demo, "page"),
    Route("POST", "/api/demo/leave", post_leave, "paired", web_token=True),
    Route("POST", "/intake/api/demo/leave", post_leave, "web"),
]


def mountable() -> list[Route]:
    """What RC9 mounts: `ROUTES` once `demo.MOUNTABLE` is True (the seal's leak lists are empty), nothing before."""
    return list(ROUTES) if demo.MOUNTABLE else []
