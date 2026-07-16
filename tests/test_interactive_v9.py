from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "template/.microagent/interactive.py"
SPEC = importlib.util.spec_from_file_location("localcode_interactive_v9", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_plain_text_is_a_note():
    assert MODULE.parse_line("Ne modifie pas ce contrat") == ("note", "Ne modifie pas ce contrat")


def test_new_commands_are_parsed():
    assert MODULE.parse_line("/constraint préserver API") == ("constraint", "préserver API")
    assert MODULE.parse_line("/approve A-123 run") == ("approve", "A-123 run")
    assert MODULE.parse_line("/answer Q-123 utilise le test") == ("answer", "Q-123 utilise le test")
