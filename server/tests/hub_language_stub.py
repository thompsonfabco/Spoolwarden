"""Test Hub for the Hub page's Language and demo parts: the real test Hub of test_hub_page_audit.py behind a
wrapper that answers the routes the server steps (languages) and (demo) will add, and adds `demo` to the Hub's state answer.
What it assumes of those steps is the contract of the languages routes and nothing more:
  GET  languages               -> {hub, languages: [{code, name, reviewed, installed, complete}], available: [{code, name, reviewed, complete}], problem?}
  POST languages/{code}/install  (unlocked) -> {"ok": true}; the language is then installed (or 422 {"error"} when `install_error` is set: the pack failed its check)
  POST languages/{code}/remove   (unlocked) -> {"removed": true}
  POST settings/language {hub}   (unlocked) -> {"hub": code}
  POST demo {on}                 (unlocked) -> {"demo": bool}, or 409 {"error": <sentence>, "code": "not_empty"}
  GET  state                     -> the real answer with `demo: bool`
The browser steers it through POST /stub/set (a JSON patch; {"reset": true} first for a fresh state) and reads GET /stub/calls."""
from __future__ import annotations

import json
import time

import pytest

import hub_alerts_stub as base
from hub_alerts_stub import CODE, StubHub, drive, hub_app  # noqa: F401

NOT_EMPTY = "The demo is for an empty Hub. This Hub has real data."


def lang_state(**over):
    return {"hub": "en",
            "languages": [{"code": "en", "name": "English", "reviewed": True, "installed": True, "complete": 100},
                          {"code": "de", "name": "Deutsch", "reviewed": False, "installed": True, "complete": 62}],
            "available": [{"code": "de", "name": "Deutsch", "reviewed": False, "complete": 62},
                          {"code": "ru", "name": "Русский", "reviewed": False, "complete": 8}],
            "problem": None, "demo": False, "not_empty": False, "install_error": None, **over}


class LangStub(StubHub):
    """The page's Hub with the language and demo routes of the server steps that are not in this tree."""

    def __init__(self, app):
        super().__init__(app)
        self.state.update(lang_state())

    async def stub(self, path, receive, send):
        if path == "/stub/set":
            patch = await self.body(receive)
            if patch.pop("reset", False):
                self.state = base.fresh()
                self.state.update(lang_state())
            self.state.update(patch)
            return await self.reply(send, 200, {"ok": True})
        return await super().stub(path, receive, send)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/setup/api/"):
            return await super().__call__(scope, receive, send)
        name, method = scope["path"][len("/setup/api/"):], scope["method"]
        if name == "state" and method == "GET":
            if "state" in self.state["fail"]:
                return await self.reply(send, self.state["fail"]["state"], {"error": "the stub says no"})
            return await self.state_answer(scope, receive, send)
        if (name == "languages" and method == "GET") or (method == "POST" and (name in ("settings/language", "demo") or name.startswith("languages/"))):
            return await self.route2(name, method, scope, receive, send)
        return await super().__call__(scope, receive, send)

    async def state_answer(self, scope, receive, send):
        got = []

        async def catch(message):
            got.append(message)

        await self.app(scope, receive, catch)
        start = next(m for m in got if m["type"] == "http.response.start")
        data = json.loads(b"".join(m.get("body", b"") for m in got if m["type"] == "http.response.body"))
        if start["status"] == 200:
            data["demo"] = bool(self.state["demo"])
        return await self.reply(send, start["status"], data)

    async def route2(self, name, method, scope, receive, send):
        st = self.state
        body = await self.body(receive) if method == "POST" else None
        st["calls"].append({"name": name, "query": scope["query_string"].decode(), "body": body, "at": time.time()})
        if method == "POST" and body.get("code") != CODE:
            return await self.reply(send, 403, {"error": "wrong code", "field": "code"})
        if name == "languages":
            return await self.reply(send, 200, {k: st[k] for k in ("hub", "languages", "available")} | ({"problem": st["problem"]} if st["problem"] else {}))
        if name == "settings/language":
            st["hub"] = body["hub"]
            return await self.reply(send, 200, {"hub": st["hub"]})
        if name == "demo":
            if body["on"] and st["not_empty"]:
                return await self.reply(send, 409, {"error": NOT_EMPTY, "code": "not_empty"})
            st["demo"] = bool(body["on"])
            return await self.reply(send, 200, {"demo": st["demo"]})
        code, action = name.split("/")[1:3] if name.count("/") == 2 else (None, None)
        lang = next((x for x in st["languages"] + st["available"] if x["code"] == code), None) if code else None
        if action == "install" and lang:
            if st["install_error"]:
                return await self.reply(send, 422, {"error": st["install_error"]})
            if not any(x["code"] == code for x in st["languages"]):
                st["languages"].append({**lang, "installed": True})
            return await self.reply(send, 200, {"ok": True})
        if action == "remove" and lang:
            st["languages"] = [x for x in st["languages"] if x["code"] != code]
            return await self.reply(send, 200, {"removed": True})
        return await self.reply(send, 404, {"error": "no such route"})


@pytest.fixture
def lang_stub(hub_app):  # noqa: F811
    """(the stub Hub, the page's address): the real test Hub behind the language and demo routes."""
    from hub_alerts_stub import AsHttps, _serve
    app = LangStub(AsHttps(hub_app[0]))
    running = _serve(app, hub_app[1])
    try:
        yield app, running.url + "/setup"
    finally:
        running.stop()
