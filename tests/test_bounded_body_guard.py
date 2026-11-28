"""Guard test: no unbounded `request.json()`, `request.body()`, `request.form()`, or `request.stream()` outside `http_body.py`."""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PKG = ROOT / "server" / "filament_kit"

# Dated exception list (2026-11-23): only bounded multipart form() calls with explicit max_files/max_fields
# or the login form (web/auth.py). No request.json() or request.body() is allowed outside http_body.py.
BOUNDED_FORM_FILES = {
    "server/filament_kit/api.py",            # request.form(max_files=intake.MAX_PHOTOS, max_fields=10)
    "server/filament_kit/web/intake_app.py", # request.form(max_files=intake.MAX_PHOTOS, max_fields=10)
}
AUTH_FORM_FILE = "server/filament_kit/web/auth.py"  # login form; Starlette default multipart cap

# Known bounded request.stream() consumers outside http_body.py.
STREAM_EXCEPTIONS = {
    ("server/filament_kit/api.py", "_import_csv"),
    ("server/filament_kit/profiles_api.py", "_read_capped"),
    ("server/filament_kit/profiles_api.py", "_StreamReader.__init__"),
    ("server/filament_kit/setup_console/backup_tab.py", "upload_zip"),
    ("server/filament_kit/setup_console/history_tab.py", "_import_csv"),
}


class _BodyReadVisitor(ast.NodeVisitor):
    def __init__(self, rel: str):
        self.rel = rel
        self.scope: list[tuple[str, str]] = []
        self.bad: list[str] = []

    def _current_fn(self) -> str:
        if len(self.scope) >= 2 and self.scope[-2][0] == "class" and self.scope[-1][0] == "func":
            return f"{self.scope[-2][1]}.{self.scope[-1][1]}"
        for kind, name in reversed(self.scope):
            if kind == "func":
                return name
        return "<module>"

    def visit_ClassDef(self, node: ast.ClassDef):
        self.scope.append(("class", node.name))
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef):
        self.scope.append(("func", node.name))
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        self.scope.append(("func", node.name))
        self.generic_visit(node)
        self.scope.pop()

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Attribute) and node.func.attr in ("json", "body", "form", "stream"):
            target = node.func.value
            name = (
                target.id if isinstance(target, ast.Name)
                else target.attr if isinstance(target, ast.Attribute)
                else None
            )
            if name in ("request", "req"):
                attr = node.func.attr
                if attr == "form":
                    if self.rel == AUTH_FORM_FILE:
                        pass
                    elif self.rel in BOUNDED_FORM_FILES:
                        kw_names = {kw.arg for kw in node.keywords if kw.arg is not None}
                        if not {"max_files", "max_fields"}.issubset(kw_names):
                            self.bad.append(
                                f"{self.rel}:{node.lineno}: {name}.form() missing max_files/max_fields"
                            )
                    else:
                        self.bad.append(f"{self.rel}:{node.lineno}: {name}.form()")
                elif attr == "stream":
                    fn = self._current_fn()
                    if (self.rel, fn) not in STREAM_EXCEPTIONS:
                        self.bad.append(f"{self.rel}:{node.lineno}: {name}.stream() in {fn}")
                else:
                    self.bad.append(f"{self.rel}:{node.lineno}: {name}.{attr}()")
        self.generic_visit(node)


def test_no_unbounded_request_body_reads_outside_http_body():
    bad = []
    for p in sorted(PKG.rglob("*.py")):
        if "_vendor" in p.parts or p.name == "http_body.py":
            continue
        rel = p.relative_to(ROOT).as_posix()
        tree = ast.parse(p.read_text(encoding="utf-8"), filename=rel)
        visitor = _BodyReadVisitor(rel)
        visitor.visit(tree)
        bad.extend(visitor.bad)
    assert not bad, "Unbounded request body reads outside http_body.py:\n" + "\n".join(bad)
