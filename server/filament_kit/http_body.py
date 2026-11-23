"""Shared bounded HTTP request-body readers so no JSON/body route buffers an unbounded payload."""
from __future__ import annotations

import json
from pathlib import Path
from starlette.requests import Request

DEFAULT_JSON_CAP = 65536     # 64 KiB: covers all Kit API, Setup Page, and Web App JSON payloads
VOICE_JSON_CAP = 16384       # 16 KiB: voice settings PATCH / test payloads
SMALL_JSON_CAP = 4096        # 4 KiB: reader /reader/scan and /api/alerts/test tiny control payloads

_STRINGS_PATH = Path(__file__).resolve().parents[2] / "strings" / "en.json"
_TOO_LARGE_CACHE: str | None = None


class BodyTooLarge(Exception):
    """Raised before reading past `cap` bytes."""

    def __init__(self, cap: int):
        self.cap = cap
        super().__init__(too_large_text())


class BadBody(Exception):
    """Raised when the body is not valid JSON or not the expected JSON shape."""


def too_large_text() -> str:
    """Catalogue sentence for HTTP 413 (`common_request_too_large`)."""
    global _TOO_LARGE_CACHE
    if _TOO_LARGE_CACHE is None:
        try:
            _TOO_LARGE_CACHE = json.loads(_STRINGS_PATH.read_text(encoding="utf-8")).get(
                "common_request_too_large", "Request too large."
            )
        except OSError:
            _TOO_LARGE_CACHE = "Request too large."
    return _TOO_LARGE_CACHE


async def read_bytes(request: Request, cap: int) -> bytes:
    """Stream `request` up to `cap` bytes; raise `BodyTooLarge` as soon as `cap` is exceeded."""
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > cap:
                raise BodyTooLarge(cap)
        except ValueError:
            pass
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise BodyTooLarge(cap)
        chunks.append(chunk)
    return b"".join(chunks)


async def read_json_object(request: Request, cap: int = DEFAULT_JSON_CAP, *, allow_empty: bool = False) -> dict:
    """Stream a JSON object up to `cap` bytes; raise `BodyTooLarge` or `BadBody`."""
    raw = await read_bytes(request, cap)
    if not raw and allow_empty:
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        raise BadBody("the request body must be JSON") from None
    if not isinstance(body, dict):
        raise BadBody("the request body must be a JSON object")
    return body


async def read_json_any(request: Request, cap: int = DEFAULT_JSON_CAP, *, default=None):
    """Stream JSON up to `cap` bytes, returning `default` if invalid JSON (for callers with custom validation)."""
    raw = await read_bytes(request, cap)
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default
