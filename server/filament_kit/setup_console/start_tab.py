"""The Getting started card shown on Overview until every step is done or hidden.

    GET  start        the current step states, counts and any restore notice
    POST start        {hide: bool} or {keep: "name"|"storage"|"features"}
"""
from __future__ import annotations

import anyio

from .. import firstrun, getting_started


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
        has_hide = "hide" in body
        has_keep = "keep" in body
        if has_hide == has_keep:
            raise Refused(400, "give either hide or keep")

        if has_hide:
            hide = body["hide"]
            if not isinstance(hide, bool):
                raise Refused(400, "hide must be true or false")

            def run() -> dict:
                if not firstrun.set_hidden(hide):
                    raise Refused(500, "could not save the checklist state")
                return _view(page)

            view = await anyio.to_thread.run_sync(run)
            page.audit(request, "getting started list hidden" if hide else "getting started list shown")
            return view

        keep = body["keep"]
        if not isinstance(keep, str) or keep not in firstrun.MARKS:
            raise Refused(400, "keep must be name, storage, or features")

        def run() -> dict:
            if not firstrun.mark(keep):
                raise Refused(500, "could not save the checklist state")
            return _view(page)

        view = await anyio.to_thread.run_sync(run)
        page.audit(request, f"getting started step kept ({keep})")
        return view

    return [("/start", page.guard(read), "GET"),
            ("/start", page.guard(write, write=True), "POST")]
