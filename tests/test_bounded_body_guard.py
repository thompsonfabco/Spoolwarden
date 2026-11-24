"""Guard test: no unbounded `request.json()`, `request.body()`, or `request.form()` outside `http_body.py`."""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "server" / "filament_kit"

# Dated exception list (2026-11-23): only bounded multipart form() calls with explicit max_files/max_fields
# or the login form (web/auth.py). No request.json() or request.body() is allowed outside http_body.py.
FORM_EXCEPTIONS = {
    "server/filament_kit/api.py",            # request.form(max_files=intake.MAX_PHOTOS, max_fields=10)
    "server/filament_kit/web/intake_app.py", # request.form(max_files=intake.MAX_PHOTOS, max_fields=10)
    "server/filament_kit/web/auth.py",       # login form; Starlette default multipart cap
}


def test_no_unbounded_request_body_reads_outside_http_body():
    bad = []
    for p in sorted(PKG.rglob("*.py")):
        if "_vendor" in p.parts or p.name == "http_body.py":
            continue
        rel = p.relative_to(ROOT).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=rel)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("json", "body", "form")
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "request"):
                if node.func.attr == "form" and rel in FORM_EXCEPTIONS:
                    continue
                bad.append(f"{rel}:{node.lineno}: request.{node.func.attr}()")
    assert not bad, "Unbounded request body reads outside http_body.py:\n" + "\n".join(bad)
