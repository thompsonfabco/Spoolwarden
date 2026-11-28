"""The Getting started card shown on Overview until every step is done or hidden.

    GET  start        the current step states, counts and any restore notice
    POST start        {hide: bool}, {keep: "name"|"storage"|"features"}, or {done: <step id>}
"""
from __future__ import annotations

import anyio

from .. import demo, firstrun, getting_started
from .texts import say


def _view(page) -> dict:
    v = getting_started.view(backup_done=bool(page._last_backup()))
    if v.get("show") and v.get("total", 0) > 0 and v.get("done", 0) >= v["total"]:
        firstrun.finish()
    return v


def routes(page) -> list[tuple]:
    from ..setup_page import Refused

    async def read(request, body):
        return await anyio.to_thread.run_sync(_view, page)

    async def write(request, body):
        if demo.is_on():
            raise Refused(409, demo.NOT_IN_DEMO, code=demo.NOT_IN_DEMO_CODE)
        keys = [k for k in ("hide", "keep", "done") if k in body]
        if len(keys) != 1:
            raise Refused(400, say("hub_start_hide_or_keep"))

        which = keys[0]
        if which == "hide":
            hide = body["hide"]
            if not isinstance(hide, bool):
                raise Refused(400, say("hub_start_hide_bool"))

            def run() -> dict:
                if not firstrun.set_hidden(hide):
                    raise Refused(500, say("hub_start_not_saved"))
                return _view(page)

            view = await anyio.to_thread.run_sync(run)
            page.audit(request, "getting started list hidden" if hide else "getting started list shown")
            return view

        if which == "keep":
            keep = body["keep"]
            if not isinstance(keep, str) or keep not in firstrun.MARKS:
                raise Refused(400, say("hub_start_keep_which"))

            def run() -> dict:
                if not firstrun.mark(keep):
                    raise Refused(500, say("hub_start_not_saved"))
                return _view(page)

            view = await anyio.to_thread.run_sync(run)
            page.audit(request, f"getting started step kept ({keep})")
            return view

        done = body["done"]
        if not isinstance(done, str) or done not in firstrun.DONE_STEPS:
            raise Refused(400, say("hub_start_done_which"))

        def run() -> dict:
            if done not in firstrun.mark_done(done).done:
                raise Refused(500, say("hub_start_not_saved"))
            return _view(page)

        view = await anyio.to_thread.run_sync(run)
        page.audit(request, f"getting started step marked done ({done})")
        return view

    return [("/start", page.guard(read), "GET"),
            ("/start", page.guard(write, write=True), "POST")]
