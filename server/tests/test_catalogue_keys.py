"""The string catalogue holds every text the code asks it for: each `catalogue_text(key, fallback)` call of the server and the
Slicer Sync (also under its aliases `_catalogue_text` and `_t`) names a key that is in strings/en.json, and the English
fallback written beside it is that key's text. The fallback is only for a standalone station that ships without the
catalogue; if it differs from the catalogue, the two say different things. The modules are read with `ast`, nothing is imported.

A key or a fallback that is not a literal is named in ALLOWED_DYNAMIC with the reason; a name or an f-string made of a module's
own constants is resolved to its text."""
from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CATALOGUE = json.loads((ROOT / "strings" / "en.json").read_text(encoding="utf-8"))
SOURCES = sorted([*(ROOT / "server" / "filament_kit").rglob("*.py"), *(ROOT / "pc-sync" / "fksync").rglob("*.py")])
CALLS = ("catalogue_text", "_catalogue_text", "_t")

# (path relative to the repository, line text of the call) -> why its key or fallback is not a literal
ALLOWED_DYNAMIC = {
    ("server/filament_kit/station/page.py", "return catalogue_text(key, raw, **{k: str(v) for k, v in values.items()})"):
        "say(): the key is the caller's; it is looked up in the catalogue itself, so it cannot be missing",
    ("server/filament_kit/station/page.py", "TEXTS = {k: catalogue_text(k, v) for k, v in _PAGE_DEFAULTS.items()}"):
        "the page's keys are _PAGE_DEFAULTS', checked by test_the_page_defaults_are_the_catalogue_texts",
    ("server/filament_kit/station/panel_setup.py", "return catalogue_text(key, text)"):
        "_t(): panel_setup's own wrapper; every call of _t in the module is read as a call with its literals",
}


def _constants(tree: ast.Module) -> dict[str, object]:
    """The module's own `NAME = <string or whole number>` lines, for a fallback written as a name or an f-string."""
    out: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = _text(node.value, out)
            if value is not None:
                out[node.targets[0].id] = value
            elif isinstance(node.value, ast.Constant) and isinstance(node.value.value, int):
                out[node.targets[0].id] = node.value.value
    return out


def _text(node: ast.AST, consts: dict[str, object]) -> str | None:
    """The string an expression is, or None when it is not known without running the code."""
    if isinstance(node, ast.Constant):
        return node.value if isinstance(node.value, str) else None
    if isinstance(node, ast.Name):
        v = consts.get(node.id)
        return v if isinstance(v, str) else None
    if isinstance(node, ast.JoinedStr):
        parts = []
        for p in node.values:
            if isinstance(p, ast.Constant):
                parts.append(str(p.value))
            elif isinstance(p, ast.FormattedValue) and isinstance(p.value, ast.Name) and p.value.id in consts:
                parts.append(str(consts[p.value.id]))
            else:
                return None
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        a, b = _text(node.left, consts), _text(node.right, consts)
        return a + b if a is not None and b is not None else None
    return None


def _calls():
    """(relative path, line, key node's text or None, fallback text or None, source line) of every call."""
    for path in SOURCES:
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        consts, lines = _constants(tree), src.splitlines()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            f = node.func
            name = f.id if isinstance(f, ast.Name) else f.attr if isinstance(f, ast.Attribute) else ""
            if name not in CALLS or not node.args:
                continue
            key = _text(node.args[0], consts)
            fallback = _text(node.args[1], consts) if len(node.args) > 1 else None
            yield (path.relative_to(ROOT).as_posix(), node.lineno, key, fallback, len(node.args) > 1,
                   lines[node.lineno - 1].strip())


def _allowed(rel: str, line: str) -> bool:
    return (rel, line) in ALLOWED_DYNAMIC


def test_every_catalogue_key_in_the_code_is_in_the_catalogue():
    found = list(_calls())
    assert len(found) >= 80, "the scan found too few calls: it no longer reads the code"
    unnamed = [f"{rel}:{n}" for rel, n, key, _, _, line in found if key is None and not _allowed(rel, line)]
    assert not unnamed, f"a key that is not a literal and not in ALLOWED_DYNAMIC: {unnamed}"
    missing = sorted({(key, f"{rel}:{n}") for rel, n, key, _, _, _ in found if key is not None and key not in CATALOGUE})
    assert not missing, f"keys the code asks for that strings/en.json lacks: {missing}"


def test_every_catalogue_fallback_is_the_catalogue_text():
    found = list(_calls())
    unnamed = [f"{rel}:{n}" for rel, n, _, fb, has, line in found if has and fb is None and not _allowed(rel, line)]
    assert not unnamed, f"a fallback that is not a literal and not in ALLOWED_DYNAMIC: {unnamed}"
    drift = sorted((key, f"{rel}:{n}", fb, CATALOGUE[key]) for rel, n, key, fb, _, _ in found
                   if key is not None and fb is not None and CATALOGUE.get(key) != fb)
    assert not drift, f"a fallback that strings/en.json lacks or says differently (key, where, fallback, catalogue): {drift}"


def test_the_page_defaults_are_the_catalogue_texts():
    """station/page.py's _PAGE_DEFAULTS is a table of keys and their English fallbacks."""
    from filament_kit.station import page
    wrong = {k: (v, CATALOGUE.get(k)) for k, v in page._PAGE_DEFAULTS.items() if CATALOGUE.get(k) != v}
    assert not wrong, wrong
