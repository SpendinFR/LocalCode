from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "template/.microagent/tool_guard.py"


def write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_unknown_shell_waits_for_human_approval(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    config = {
        "context_parent": ".agent-context",
        "human_approval_mode": "commands",
        "approval_timeout_seconds": 5,
        "allowed_command_prefixes": ["pytest"],
        "context_guard": {},
    }
    write_json(repo / ".microagent/config.json", config)
    run_dir = repo / ".agent-runs/R1"
    state = {
        "controller_reports": str(run_dir),
        "active_scope": "microtask",
        "active_microtask": "M2",
        "active_operation": "qwen:coder",
        "task_file": ".tasks/TASK.md",
    }
    write_json(repo / ".agent-context/R1/state.json", state)
    (repo / ".tasks").mkdir()
    (repo / ".tasks/TASK.md").write_text(
        '<!-- AGENT_TASK_META {"validation_commands":[],"full_suite_commands":[]} AGENT_TASK_META -->',
        encoding="utf-8",
    )
    payload = {
        "cwd": str(repo),
        "tool_name": "run_shell_command",
        "tool_input": {"command": "python custom_check.py"},
    }
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "template/.microagent")
    proc = subprocess.Popen(
        [sys.executable, str(SCRIPT)],
        cwd=repo,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdin is not None
    proc.stdin.write(json.dumps(payload))
    proc.stdin.close()
    proc.stdin = None

    pending_root = run_dir / "control/approvals/pending"
    deadline = time.monotonic() + 3
    request = None
    while time.monotonic() < deadline:
        files = list(pending_root.glob("*.json")) if pending_root.exists() else []
        if files:
            request = json.loads(files[0].read_text(encoding="utf-8"))
            break
        time.sleep(0.05)
    assert request is not None
    decision = run_dir / "control/approvals/decisions" / f"{request['id']}.json"
    write_json(decision, {"decision": "approve", "scope": "once", "message": "ok"})
    stdout, stderr = proc.communicate(timeout=5)
    assert proc.returncode == 0, stderr
    response = json.loads(stdout)
    assert response["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert not decision.exists()
