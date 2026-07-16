from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_models_are_split_by_role():
    cfg = json.loads((ROOT / "template/.microagent/config.json").read_text(encoding="utf-8"))
    assert cfg["planner_model"] == "qwen35-9b"
    assert cfg["scout_model"] == "qwen35-9b"
    assert cfg["coder_model"] == "qwen3coder30-iq2"
    assert cfg["architecture_reviewer_model"] == "qwen25coder14-q3"
    assert cfg["execution_reviewer_model"] == "qwen25coder14-q3"
    assert cfg["judge_model"] == "qwen35-9b"
    assert cfg["coder_model"] != cfg["architecture_reviewer_model"]
    assert cfg["unload_ollama_between_model_switches"] is False


def test_settings_use_llama_router():
    settings = json.loads((ROOT / "template/.qwen/settings.json").read_text(encoding="utf-8"))
    providers = settings["modelProviders"]["openai"]
    assert {item["id"] for item in providers} == {
        "qwen35-9b", "qwen3coder30-iq2", "qwen25coder14-q3"
    }
    assert all(item["baseUrl"] == "http://127.0.0.1:8080/v1" for item in providers)
