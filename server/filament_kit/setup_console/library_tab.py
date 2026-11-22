"""The filament library on the Settings tab. Update and rollback are the jobs `library_update` and `library_rollback`.

    GET  library                          version, source, local additions, materials, last check (redacted)
    POST library/add {code, entry}        legacy raw JSON local addition
    GET  library/local                    the Hub's custom filament entries
    POST library/local                    add one custom filament entry from the catalog form
    POST library/local/{id}/remove        remove one custom filament entry by id
"""
from __future__ import annotations

import time

import anyio
from starlette.responses import JSONResponse

from .. import library, redact
from ..profiles import builds


def _view() -> dict:
    st = library.status()
    known = redact.known_values()
    lc = st.get("last_check")
    if isinstance(lc, dict):
        at = lc.get("at")
        lc = {"at": time.strftime("%Y-%m-%d %H:%M", time.localtime(at)) if isinstance(at, (int, float)) else None,
              "ok": bool(lc.get("ok")),
              "error": redact.text(str(lc["error"]), known) if lc.get("error") else None}
    skipped = st.get("skipped")
    return {"version": st.get("version"), "source": st.get("source"), "local": st.get("local"),
            "materials": library.load().materials(),
            "url": redact.text(str(st.get("url") or ""), known), "last_check": lc,
            "skipped": redact.text(str(skipped), known) if skipped else None}


def routes(page) -> list[tuple]:
    from ..setup_page import Refused

    async def read(request, body):
        return await anyio.to_thread.run_sync(_view)

    async def add(request, body):
        text = body.get("entry")
        if not isinstance(text, str) or not text.strip():
            raise Refused(400, "paste the entry's JSON")
        data = text.encode("utf-8")
        if len(data) > library.LOCAL_LIMIT:
            raise Refused(400, f"entry not added: larger than {library.LOCAL_LIMIT // 1024} KiB")

        def run() -> tuple[dict, str]:
            try:
                entry, line = library.add_local(data)
            except ValueError as e:
                raise Refused(400, f"entry not added: {e}") from None
            builds.request_rebuild(f"local library addition {line}")
            return entry, line
        entry, line = await anyio.to_thread.run_sync(run)
        page.audit(request, f"library entry added (line {line})")
        return {"ok": True, "vendor": entry["vendor"], "product": entry["product"], "line": line}

    async def local_list(request, body):
        res = await anyio.to_thread.run_sync(library.local_list)
        return res if isinstance(res, dict) else {"entries": res}

    async def add_form(request, body):
        def run() -> dict:
            try:
                row = library.add_local_form(body)
            except library.FormError as e:
                raise Refused(400, str(e) or "check the highlighted fields",
                              code="invalid_entry", problems=e.problems) from None
            except ValueError as e:
                raise Refused(400, f"entry not added: {e}") from None
            builds.request_rebuild(f"local library addition {row.get('id') or row.get('product', '')}")
            return row

        row = await anyio.to_thread.run_sync(run)
        page.audit(request, f"local library entry added ({row.get('vendor', '')} {row.get('product', '')})")
        return JSONResponse({"ok": True, "entry": row}, status_code=201)

    async def remove_form(request, body):
        entry_id = request.path_params.get("id", "")
        if not isinstance(entry_id, str) or not entry_id.strip():
            raise Refused(400, "missing entry id")

        def run() -> bool:
            removed = library.remove_local(entry_id)
            if not removed:
                raise Refused(404, "no such custom catalog entry")
            builds.request_rebuild(f"local library entry removed ({entry_id})")
            return removed

        await anyio.to_thread.run_sync(run)
        page.audit(request, f"local library entry removed ({entry_id})")
        return {"removed": True}

    return [("/library", page.guard(read), "GET"),
            ("/library/add", page.guard(add, write=True), "POST"),
            ("/library/local", page.guard(local_list), "GET"),
            ("/library/local", page.guard(add_form, write=True), "POST"),
            ("/library/local/{id}/remove", page.guard(remove_form, write=True), "POST")]
