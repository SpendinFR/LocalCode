from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def test_qwen_lsp_is_enabled():
    config = json.loads((ROOT / "template/.microagent/config.json").read_text(encoding="utf-8"))
    orchestrator = (ROOT / "template/.microagent/orchestrator.py").read_text(encoding="utf-8")
    agents = (ROOT / "AGENTS_BLOCK.md").read_text(encoding="utf-8")
    assert config["enable_lsp"] is True
    assert "--experimental-lsp" in orchestrator
    assert "outil `lsp`" in agents

def test_python_repo_gets_lsp_config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo, check=True, capture_output=True)
    result = subprocess.run([sys.executable, str(ROOT / "install_into_repo.py"), str(repo)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    config = json.loads((repo / ".lsp.json").read_text(encoding="utf-8"))
    assert config["python"]["command"] == sys.executable
    assert config["python"]["args"] == ["-m", "pylsp"]
    assert config["python"]["trustRequired"] is False
