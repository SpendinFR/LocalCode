from __future__ import annotations

import importlib.util
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


def test_windows_existing_quoted_executable_bypasses_cmd(monkeypatch, tmp_path: Path):
    core = load_module("localcode_core_v95_direct", MICRO / "core.py")
    executable = tmp_path / "python with spaces.exe"
    executable.write_text("placeholder", encoding="utf-8")
    command = f'"{executable}" -c "print(123)"'
    monkeypatch.setattr(core.os, "name", "nt")
    argv, family = core.shell_command(command)
    assert family == "direct"
    assert argv == [str(executable), "-c", "print(123)"]


def test_windows_nonexistent_quoted_executable_still_uses_cmd(monkeypatch):
    core = load_module("localcode_core_v95_cmd", MICRO / "core.py")
    monkeypatch.setattr(core.os, "name", "nt")
    monkeypatch.setenv("COMSPEC", "cmd.exe")
    command = r'"C:\Missing Folder\tool.exe" --version'
    argv, family = core.shell_command(command)
    assert family == "cmd"
    assert argv == ["cmd.exe", "/d", "/s", "/c", command]


def test_agent_powershell_streams_python_output():
    source = (ROOT / "template/agent.ps1").read_text(encoding="utf-8-sig")
    assert "$script:LastPythonExitCode = $LASTEXITCODE" in source
    assert "$Code = Invoke-Python" not in source
    assert "Invoke-Python @(\"$Repo\\.microagent\\doctor.py\"" in source
