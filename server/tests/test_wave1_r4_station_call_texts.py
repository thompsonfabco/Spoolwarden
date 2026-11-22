"""R596 (D13): the sentences of a failed call to the Weigh Station (station_client.StationCallError), which the Hub page shows, are
catalogue texts, not English literals in the code."""
import ast
import json
from pathlib import Path

from filament_kit import station_client

ROOT = Path(__file__).resolve().parents[2]


def test_no_station_call_error_carries_a_literal_sentence():
    tree = ast.parse((ROOT / "server" / "filament_kit" / "station_client.py").read_text(encoding="utf-8"))
    literal = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "StationCallError" and len(node.args) > 1:
            msg = node.args[1]
            if isinstance(msg, ast.JoinedStr):                  # an f-string may only join the TEXT_* catalogue constants
                parts = [v.value for v in msg.values if isinstance(v, ast.FormattedValue)]
                if not parts or not all(isinstance(p, ast.Name) and p.id.startswith("TEXT_") for p in parts):
                    literal.append(node.lineno)
            elif isinstance(msg, ast.Constant) and isinstance(msg.value, str):
                literal.append(node.lineno)
    assert literal == [], literal


def test_the_sentences_come_from_the_catalogue():
    keys = json.loads((ROOT / "strings" / "en.json").read_text(encoding="utf-8"))
    want = {k: v for k, v in keys.items() if k.startswith("station_call_")}
    assert len(want) == 11
    for key, text in want.items():
        assert station_client._say(key, kind="OSError") == text.replace("{kind}", "OSError")
    assert station_client._say("station_call_no_answer", kind="OSError") == "the Weigh Station does not answer (OSError)"
