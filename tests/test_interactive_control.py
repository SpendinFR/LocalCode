from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "template/.microagent/interactive.py"
SPEC = importlib.util.spec_from_file_location("localcode_interactive", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_plain_text_is_a_note():
    assert MODULE.parse_line("Vérifie le cas stop/go") == ("note", "Vérifie le cas stop/go")


def test_slash_commands():
    assert MODULE.parse_line("/pause") == ("pause", "")
    assert MODULE.parse_line("/review vérifier Windows") == ("review", "vérifier Windows")
    assert MODULE.parse_line("/target M3") == ("target", "M3")
