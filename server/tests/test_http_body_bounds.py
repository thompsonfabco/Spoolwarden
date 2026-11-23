"""Prove F1 shared bounded body reader refuses oversized bodies without reading them whole and allows <= cap."""
from __future__ import annotations

import json
import anyio
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from filament_kit import api, http_body, pairing, reader_tokens, setup_page
from filament_kit.setup_console import voice_tab
from filament_kit.web import intake_app, reader as web_reader


@pytest.fixture
def env(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt", "backups"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    monkeypatch.setenv("FK_NO_SYSTEMD", "1")
    return tmp_path


class FakeSpoolman:
    def get(self, path):
        return []


def _asgi_call(app, method: str, path: str, *, cap: int, chunk_size: int = 4096, headers: dict[str, str] | None = None, body: bytes | None = None):
    """Invoke a Starlette ASGI app directly with a chunked receive() that fails if consumed past cap + one chunk."""
    raw_headers = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "headers": raw_headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    if body is not None:
        chunks = [body[i : i + chunk_size] for i in range(0, len(body), chunk_size)] or [b""]
    else:
        chunks = None

    sent_total = 0
    idx = 0
    done = False
    status_out = 0
    body_out = bytearray()

    async def receive():
        nonlocal sent_total, idx, done
        if done:
            return {"type": "http.disconnect"}
        if chunks is not None:
            chunk = chunks[idx]
            idx += 1
            more = idx < len(chunks)
            if not more:
                done = True
            return {"type": "http.request", "body": chunk, "more_body": more}
        if sent_total == 0:
            chunk = b'{"code":"123456","pad":"'
            sent_total += len(chunk)
            return {"type": "http.request", "body": chunk, "more_body": True}
        limit = cap + chunk_size
        if sent_total <= cap:
            chunk = b"a" * chunk_size
            sent_total += len(chunk)
            if sent_total > limit + chunk_size:
                raise AssertionError(f"Route consumed {sent_total} bytes past cap={cap} + one chunk")
            return {"type": "http.request", "body": chunk, "more_body": True}
        raise AssertionError(f"Route kept consuming stream after cap={cap} (sent_total={sent_total})")

    async def send(msg):
        nonlocal status_out
        if msg["type"] == "http.response.start":
            status_out = msg["status"]
        elif msg["type"] == "http.response.body":
            body_out.extend(msg.get("body", b""))

    async def run():
        await app(scope, receive, send)

    anyio.run(run)
    return status_out, json.loads(body_out.decode("utf-8"))


def test_http_body_stream_stops_at_cap_and_allows_under_cap():
    async def echo(request: Request):
        try:
            body = await http_body.read_json_object(request, 128)
            return JSONResponse({"ok": True, "len": len(json.dumps(body))})
        except http_body.BodyTooLarge:
            return JSONResponse({"error": "too_large", "message": http_body.too_large_text()}, 413)

    app = Starlette(routes=[Route("/echo", echo, methods=["POST"])])
    st_ok, js_ok = _asgi_call(app, "POST", "/echo", cap=128, body=b'{"x":"' + b"a" * 100 + b'"}', headers={"content-type": "application/json"})
    assert st_ok == 200 and js_ok["ok"] is True

    st_big, js_big = _asgi_call(app, "POST", "/echo", cap=128, chunk_size=64, headers={"content-type": "application/json"})
    assert st_big == 413
    assert js_big == {"error": "too_large", "message": "Request too large."}


def test_kit_api_routes_enforce_caps_and_stop_early(env):
    pairing.make_cert("fkhost", [], env / "etc")
    token = pairing.redeem_code(pairing.issue("Phone").code)
    auth = {"authorization": f"Bearer {token}", "content-type": "application/json"}
    app = api.create_app(FakeSpoolman())

    # 1. Unauthenticated POST /api/pair (DEFAULT_JSON_CAP = 64 KiB)
    st_big, js_big = _asgi_call(app, "POST", "/api/pair", cap=http_body.DEFAULT_JSON_CAP, headers={"content-type": "application/json"})
    assert st_big == 413 and js_big["error"] == "too_large"
    st_ok, _ = _asgi_call(app, "POST", "/api/pair", cap=http_body.DEFAULT_JSON_CAP, body=b'{"code":"000000","pad":"' + b"x" * (http_body.DEFAULT_JSON_CAP - 100) + b'"}', headers={"content-type": "application/json"})
    assert st_ok != 413

    # 2. Authenticated POST /api/alerts/test (SMALL_JSON_CAP = 4 KiB)
    st_abig, js_abig = _asgi_call(app, "POST", "/api/alerts/test", cap=http_body.SMALL_JSON_CAP, chunk_size=1024, headers=auth)
    assert st_abig == 413 and js_abig["error"] == "too_large"
    st_aok, _ = _asgi_call(app, "POST", "/api/alerts/test", cap=http_body.SMALL_JSON_CAP, body=b'{"client":"' + b"c" * 80 + b'"}', headers=auth)
    assert st_aok == 200

    # 3. Multipart /api/intake/read caps max_files and max_fields
    with TestClient(app) as c:
        files = [("photos", (f"p{i}.jpg", b"\xff\xd8\xff\xe0", "image/jpeg")) for i in range(12)]
        r_mp = c.post("/api/intake/read", files=files, headers={"authorization": f"Bearer {token}"})
        assert r_mp.status_code == 400


def test_setup_page_and_voice_and_reader_and_intake_app_caps(env, monkeypatch):
    # Setup page (64 KiB)
    monkeypatch.setattr(setup_page.SetupPage, "_lan", lambda self, req: True)
    sp = setup_page.SetupPage(hosts=lambda: {"testserver"})
    sp_app = Starlette(routes=sp.routes())
    st_sp, _ = _asgi_call(sp_app, "POST", "/setup/api/code/check", cap=setup_page.MAX_BODY, headers={"content-type": "application/json", "origin": "https://testserver", "host": "testserver"})
    assert st_sp == 413
    st_sp_ok, _ = _asgi_call(sp_app, "POST", "/setup/api/code/check", cap=setup_page.MAX_BODY, body=b'{"code":"000000"}', headers={"content-type": "application/json", "origin": "https://testserver", "host": "testserver"})
    assert st_sp_ok != 413

    # Reader /reader/scan (SMALL_JSON_CAP = 4 KiB)
    _, tok = reader_tokens.TokenStore().issue("R1")
    monkeypatch.setattr(web_reader.Reader, "_lan", lambda self, req: True)
    fake_tools = type("FakeTools", (), {"kit_bridge": lambda self: None, "cfg": lambda self: {}})()
    ra_app = Starlette(routes=web_reader.Reader(fake_tools, FakeSpoolman()).routes())
    st_ra, js_ra = _asgi_call(ra_app, "POST", "/reader/scan", cap=http_body.SMALL_JSON_CAP, chunk_size=1024, headers={"x-auth-token": tok, "content-type": "application/json"})
    assert st_ra == 413 and js_ra["status"] == "error"

    # Voice PATCH /api/voice (VOICE_JSON_CAP = 16 KiB)
    v_app = Starlette(routes=voice_tab.kit_routes(lambda fn: fn))
    st_v, js_v = _asgi_call(v_app, "PATCH", "/api/voice", cap=http_body.VOICE_JSON_CAP, chunk_size=2048, headers={"content-type": "application/json"})
    assert st_v == 413 and js_v["error"] == "too_large"

    # Web Twin IntakeApp._body (DEFAULT_JSON_CAP = 64 KiB)
    ia = intake_app.IntakeApp(fake_tools, public_url=None, lan_cidrs=(), kit_api=FakeSpoolman(), host_ok=None)
    monkeypatch.setattr(ia, "_origin_ok", lambda req: True)
    ia_app = Starlette(routes=ia.routes())
    st_ia, js_ia = _asgi_call(ia_app, "POST", "/intake/api/add", cap=http_body.DEFAULT_JSON_CAP, headers={"content-type": "application/json", "origin": "https://testserver"})
    assert st_ia == 413 and js_ia["error"] == "too_large"
