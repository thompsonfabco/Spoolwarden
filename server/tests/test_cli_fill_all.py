"""No catalogue text is filled with `.format(command=...)`: a text with another brace raises there, so the program name
goes in with output.fill (which replaces only the named {tokens}). Source scan; the allow-list is empty."""
import ast
from pathlib import Path

PKG = Path(__file__).resolve().parent.parent / "filament_kit"


def _format_command_calls(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("format", "format_map")
                and any(k.arg == "command" for k in node.keywords)):
            yield f"{path.relative_to(PKG.parent)}:{node.lineno}"


def test_no_format_fills_the_program_name():
    left = [hit for path in sorted(PKG.rglob("*.py")) for hit in _format_command_calls(path)]
    assert left == [], "use output.fill(text, command=...) instead of .format(command=...): " + ", ".join(left)


def test_the_scan_sees_the_cli_modules():
    names = {p.name for p in PKG.rglob("*.py")}
    assert {"address_cli.py", "klipper_cli.py", "spools_cli.py", "cli.py", "status.py"} <= names
