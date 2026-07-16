from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "template/.microagent/resilience.py"
SPEC = importlib.util.spec_from_file_location("localcode_resilience", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_extract_json_from_fence_and_noise():
    assert MODULE.extract_json('progress\n```json\n{"ok": true}\n```') == {"ok": True}


def test_extract_json_closes_truncated_containers():
    assert MODULE.extract_json('{"status":"DONE","items":[1,2') == {
        "status": "DONE",
        "items": [1, 2],
    }


def test_compaction_respects_budget_and_keeps_contract_terms():
    source = "Rôle: codeur\n" + ("bruit\n" * 8000) + "Invariant: préserver API\nÉtat le plus récent: tests rouges"
    compacted, changed = MODULE.compact_prompt(source, 800)
    assert changed is True
    assert MODULE.estimate_tokens(compacted) <= 900
    assert "Invariant" in compacted
    assert "tests rouges" in compacted


def test_failure_classification():
    assert MODULE.classify_qwen_failure(1, "", "connection refused") == "SERVER"
    assert MODULE.classify_qwen_failure(1, "", "context window exceeded") == "CONTEXT"
    assert MODULE.classify_qwen_failure(1, "", "CUDA out of memory") == "MEMORY"


def test_dangerous_commands_are_never_approvable():
    assert MODULE.command_is_dangerous("git reset --hard HEAD") is True
    assert MODULE.command_is_dangerous("python -m pytest -q") is False


def test_repaired_json_must_still_match_schema(tmp_path: Path):
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({
        "type": "object",
        "additionalProperties": False,
        "properties": {"status": {"type": "string", "enum": ["DONE"]}},
        "required": ["status"],
    }), encoding="utf-8")
    assert MODULE.payload_matches_schema({"status": "DONE"}, schema) is True
    assert MODULE.payload_matches_schema({}, schema) is False
