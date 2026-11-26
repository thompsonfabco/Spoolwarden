"""Languages on the Hub page and kit API: list installed and available packs, set the Hub's default language,
install or remove a language pack, and serve the compiled `strings/<code >.js` bundle.

Hub console routes (under /setup/api):
    GET  languages                    current Hub language, installed packs, available packs (no code)
    POST settings/language {hub}      set the Hub's default language (setup code)
    POST languages/{code}/install     install a language pack (setup code)
    POST languages/{code}/remove      remove an installed language pack (setup code)

Kit API routes (for api.py, consumed by web/intake_app.py):
    GET  /api/languages               same status payload, token-authenticated
    GET  /api/strings/{name}          <code >.js bundle for an installed language, token-authenticated
"""
from __future__ import annotations

import anyio
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from .. import demo, languages

_STRINGS_HEADERS = {"Cache-Control": "private, max-age=60", "X-Content-Type-Options": "nosniff"}


def _parse_js_name(name: str) -> str | None:
    if not name.endswith(".js"):
        return None
    code = name.removesuffix(".js")
    return code or None


def kit_routes(authed) -> list:
    """`GET /api/languages` and `GET /api/strings/{name}` as Starlette routes, wrapped in api.py's `_authed`."""
    async def get_languages(request):
        return JSONResponse(await anyio.to_thread.run_sync(languages.status))

    async def get_strings(request):
        code = _parse_js_name(request.path_params["name"])
        if not code:
            return JSONResponse({"error": "not_found", "message": "Language not found."}, status_code=404)
        try:
            data = await anyio.to_thread.run_sync(languages.strings_js, code)
        except languages.LanguageError as e:
            return JSONResponse({"error": e.code, "message": e.message}, status_code=e.status)
        return Response(data, media_type="text/javascript; charset=utf-8", headers=_STRINGS_HEADERS)

    return [
        Route("/api/languages", authed(get_languages)),
        Route("/api/strings/{name}", authed(get_strings)),
    ]


def routes(page) -> list[tuple]:
    from ..setup_page import Refused

    def refuse(e: languages.LanguageError) -> Refused:
        return Refused(e.status, e.message, code=e.code)

    async def view(request, body):
        return await anyio.to_thread.run_sync(languages.status)

    async def set_hub(request, body):
        if demo.is_on():
            raise Refused(409, demo.NOT_IN_DEMO, code=demo.NOT_IN_DEMO_CODE)
        try:
            return await anyio.to_thread.run_sync(languages.set_hub_language, (body or {}).get("hub"))
        except languages.LanguageError as e:
            raise refuse(e) from None

    async def install(request, body):
        if demo.is_on():
            raise Refused(409, demo.NOT_IN_DEMO, code=demo.NOT_IN_DEMO_CODE)
        try:
            return await anyio.to_thread.run_sync(languages.install_language, request.path_params["code"])
        except languages.LanguageError as e:
            raise refuse(e) from None

    async def remove(request, body):
        if demo.is_on():
            raise Refused(409, demo.NOT_IN_DEMO, code=demo.NOT_IN_DEMO_CODE)
        try:
            return await anyio.to_thread.run_sync(languages.remove_language, request.path_params["code"])
        except languages.LanguageError as e:
            raise refuse(e) from None

    return [
        ("/languages", page.guard(view), "GET"),
        ("/settings/language", page.guard(set_hub, write=True), "POST"),
        ("/languages/{code}/install", page.guard(install, write=True), "POST"),
        ("/languages/{code}/remove", page.guard(remove, write=True), "POST"),
    ]
