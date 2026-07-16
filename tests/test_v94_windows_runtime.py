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


def test_resolve_cli_accepts_extensionless_python_shim(monkeypatch, tmp_path: Path):
    core = load_module("localcode_core_v94_resolution", MICRO / "core.py")
    fake = tmp_path / "qwen"
    fake.write_text("#!/usr/bin/python3 -S\nprint('ok')\n", encoding="utf-8")
    monkeypatch.setattr(core.shutil, "which", lambda name: None)
    monkeypatch.setenv("PATH", str(tmp_path))
    assert core.resolve_cli("qwen") == [sys.executable, "-S", str(fake)]


def test_windows_shell_does_not_double_wrap(monkeypatch):
    core = load_module("localcode_core_v94_shell", MICRO / "core.py")
    monkeypatch.setattr(core.os, "name", "nt")
    monkeypatch.setenv("COMSPEC", "cmd.exe")
    command = r'"C:\Program Files\Python\python.exe" -c "print(123)"'
    argv, family = core.shell_command(command)
    assert family == "cmd"
    assert argv == ["cmd.exe", "/d", "/s", "/c", command]


def test_all_qwen_launchers_use_resolved_prefix():
    orchestrator = (MICRO / "orchestrator.py").read_text(encoding="utf-8")
    resilient = (MICRO / "resilient_orchestrator.py").read_text(encoding="utf-8")
    doctor = (MICRO / "doctor.py").read_text(encoding="utf-8")
    assert 'self.qwen_prefix = resolve_cli("qwen")' in orchestrator
    assert "cli_command(self.qwen_prefix" in orchestrator
    assert "cli_command(self.qwen_prefix" in resilient
    assert 'qwen_prefix = resolve_cli("qwen")' in doctor
