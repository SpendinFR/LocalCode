from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_context_and_output_budgets_fit_8k():
    config = json.loads((ROOT / "template/.microagent/config.json").read_text(encoding="utf-8"))
    for budget in config["model_budgets"].values():
        assert budget["output"] + budget["tool_reserve"] + budget["safety"] < budget["context"]
        assert budget["context"] == 8192


def test_default_shell_policy_only_prompts_for_new_commands():
    config = json.loads((ROOT / "template/.microagent/config.json").read_text(encoding="utf-8"))
    assert config["human_approval_mode"] == "commands"
    assert config["max_qwen_retries"] == 2
    assert config["max_human_prompt_records"] <= 5
    assert config["max_human_prompt_chars"] <= 4000


def test_qwen_hook_uses_waiting_tool_guard():
    settings = json.loads((ROOT / "template/.qwen/settings.json").read_text(encoding="utf-8"))
    hook = settings["hooks"]["PreToolUse"][0]["hooks"][0]
    assert hook["command"].endswith("tool_guard.py")
    assert hook["timeout"] >= 900000
    providers = {item["id"]: item for item in settings["modelProviders"]["openai"]}
    assert providers["qwen3coder30-iq2"]["generationConfig"]["samplingParams"]["max_tokens"] <= 2048


def test_router_start_is_locked_on_both_platforms():
    ps1 = (ROOT / "template/start-model-router.ps1").read_text(encoding="utf-8")
    sh = (ROOT / "template/start-model-router.sh").read_text(encoding="utf-8")
    assert "start.lock" in ps1
    assert "start.lock" in sh


def test_stats_command_is_exposed():
    control = (ROOT / "template/.microagent/control.py").read_text(encoding="utf-8")
    interactive = (ROOT / "template/.microagent/interactive.py").read_text(encoding="utf-8")
    assert '"stats"' in control
    assert "/stats" in interactive
