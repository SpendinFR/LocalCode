from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def call_guard(tmp_path: Path, tool: str, tool_input: dict):
    agent = tmp_path / ".microagent"
    agent.mkdir(exist_ok=True)
    (agent / "config.json").write_text(json.dumps({
        "allowed_command_prefixes": ["python -m pytest"],
        "context_guard": {"max_lines_per_read": 120, "max_grep_matches": 12, "deny_broad_globs": True}
    }), encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "template" / ".microagent" / "context_guard.py"
    proc = subprocess.run(
        [sys.executable, str(script)],
        input=json.dumps({"cwd": str(tmp_path), "tool_name": tool, "tool_input": tool_input}),
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["hookSpecificOutput"]


def test_read_is_bounded(tmp_path):
    out = call_guard(tmp_path, "read_file", {"file_path": "a.py", "limit": 900})
    assert out["permissionDecision"] == "allow"
    assert out["updatedInput"]["limit"] == 120


def test_grep_is_bounded(tmp_path):
    out = call_guard(tmp_path, "grep_search", {"pattern": "foo"})
    assert out["updatedInput"]["limit"] == 12


def test_broad_glob_is_denied(tmp_path):
    out = call_guard(tmp_path, "glob", {"pattern": "**/*", "path": "."})
    assert out["permissionDecision"] == "deny"


def test_shell_file_read_is_denied(tmp_path):
    out = call_guard(
        tmp_path,
        "run_shell_command",
        {"command": "Get-Content src\\service.ts", "is_background": False},
    )
    assert out["permissionDecision"] == "deny"


def test_shell_search_is_denied(tmp_path):
    out = call_guard(
        tmp_path,
        "run_shell_command",
        {"command": "rg UserService src", "is_background": False},
    )
    assert out["permissionDecision"] == "deny"


def test_shell_test_command_is_allowed(tmp_path):
    out = call_guard(
        tmp_path,
        "run_shell_command",
        {"command": "python -m pytest tests/test_service.py", "is_background": False},
    )
    assert out["permissionDecision"] == "allow"


def test_write_existing_file_is_denied(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    out = call_guard(tmp_path, "write_file", {"file_path": "a.py", "content": "x = 2\n"})
    assert out["permissionDecision"] == "deny"


def test_write_new_file_is_allowed(tmp_path):
    out = call_guard(tmp_path, "write_file", {"file_path": "new.py", "content": "x = 1\n"})
    assert out["permissionDecision"] == "allow"


def test_edit_replace_all_is_denied(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    out = call_guard(
        tmp_path,
        "edit",
        {"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2", "replace_all": True},
    )
    assert out["permissionDecision"] == "deny"


def test_targeted_edit_is_allowed(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    out = call_guard(
        tmp_path,
        "edit",
        {"file_path": "a.py", "old_string": "x = 1", "new_string": "x = 2"},
    )
    assert out["permissionDecision"] == "allow"


def test_shell_write_command_is_denied(tmp_path):
    out = call_guard(
        tmp_path,
        "run_shell_command",
        {"command": "python -c \"open('a.py','w').write('x')\"", "is_background": False},
    )
    assert out["permissionDecision"] == "deny"
