from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MICRO = ROOT / "template/.microagent"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_windows_shell_preserves_quoted_executable(monkeypatch):
    core = load_module("localcode_core_v91", MICRO / "core.py")
    monkeypatch.setattr(core.os, "name", "nt")
    monkeypatch.setenv("COMSPEC", "cmd.exe")
    command = '"C:\\Program Files\\Python\\python.exe" -c "print(123)"'
    argv, family = core.shell_command(command)
    assert family == "cmd"
    assert argv[:4] == ["cmd.exe", "/d", "/s", "/c"]
    assert argv[-1] == command
    assert argv[-1] != f'"{command}"'


def test_control_keeps_raw_target_and_freezes_resolved_target(tmp_path: Path):
    repo = tmp_path / "repo"
    run = repo / ".agent-runs/run-1"
    run.mkdir(parents=True)
    (run / "state.json").write_text(json.dumps({
        "phase": "worktree", "active_scope": "microtask", "active_microtask": "M2"
    }), encoding="utf-8")
    result = subprocess.run([
        sys.executable, str(MICRO / "control.py"), "note", "vérifie",
        "--repo", str(repo), "--run", "run-1", "--target", "current",
    ], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    item = json.loads(next((run / "control/inbox").glob("*.json")).read_text(encoding="utf-8"))
    assert item["target"] == "current"
    assert item["resolved_target"] == "M2"


def test_doctor_can_skip_router_probe(monkeypatch, tmp_path: Path):
    doctor = load_module("localcode_doctor_v91", MICRO / "doctor.py")
    task = tmp_path / "TASK.md"
    task.write_text("x", encoding="utf-8")
    (tmp_path / ".microagent").mkdir()
    (tmp_path / ".microagent/config.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MICROAGENT_SKIP_ROUTER_CHECK", "1")
    monkeypatch.setattr(doctor.shutil, "which", lambda name: name)
    monkeypatch.setattr(doctor, "resolve_cli", lambda name: [name] if name == "qwen" else None)
    def fake_run(cmd, cwd):
        if cmd[:2] == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(cmd, 0, "true\n", "")
        if cmd[:2] == ["qwen", "--help"]:
            flags = "--json-schema --max-tool-calls --max-wall-time --approval-mode --experimental-lsp"
            return subprocess.CompletedProcess(cmd, 0, flags, "")
        return subprocess.CompletedProcess(cmd, 0, "", "")
    monkeypatch.setattr(doctor, "run", fake_run)
    monkeypatch.setattr(doctor, "router_models", lambda: (_ for _ in ()).throw(AssertionError("router called")))
    monkeypatch.setattr(sys, "argv", ["doctor.py", str(task), "--repo", str(tmp_path)])
    assert doctor.main() == 0


def test_resilient_ingest_prefers_pre_resolved_target():
    source = (MICRO / "resilient_orchestrator.py").read_text(encoding="utf-8")
    assert 'payload.get("resolved_target")' in source
    assert 'or self.resolve_intervention_target' in source
