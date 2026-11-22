"""The Hub process never imports the mcp package (server/pyproject.toml: the [web] extra is the web unit's): the Hub app
mounts the reader routes (api._intake_web_app, web.reader) with `import mcp` made to fail. No importorskip here: this
must run where mcp is missing too."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1]

CHILD = r"""
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name == "mcp" or name.startswith("mcp."):
            raise ModuleNotFoundError("No module named " + repr(name))
sys.meta_path.insert(0, Block())
sys.path.insert(0, sys.argv[1])
from filament_kit import api, config
from filament_kit.spoolman import Spoolman
cfg = config.load()
routes = api._intake_web_app(cfg, Spoolman(cfg["spoolman_url"]), None)
paths = {r.path for r in routes}
assert "/reader/healthz" in paths and "/reader/scan" in paths and "/reader/printers" in paths, sorted(paths)
assert not [m for m in sys.modules if m == "mcp" or m.startswith("mcp.")], "mcp was imported"
assert "filament_kit.web.auth" not in sys.modules
print("OK")
"""


def test_the_hub_app_mounts_the_reader_routes_with_mcp_blocked(tmp_path, monkeypatch):
    for name in ("etc", "state", "opt"):
        (tmp_path / name).mkdir()
        monkeypatch.setenv(f"FK_{name.upper()}", str(tmp_path / name))
    r = subprocess.run([sys.executable, "-c", CHILD, str(SERVER)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and r.stdout.strip() == "OK", r.stderr[-1500:]
