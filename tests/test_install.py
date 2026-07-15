from pathlib import Path
import json, subprocess, sys

def test_install_into_temp_repo(tmp_path):
    repo=tmp_path/"repo"; repo.mkdir(); subprocess.run(["git","init"],cwd=repo,check=True,capture_output=True)
    subprocess.run(["git","config","user.email","test@example.com"],cwd=repo,check=True); subprocess.run(["git","config","user.name","Test"],cwd=repo,check=True)
    (repo/"README.md").write_text("x\n"); subprocess.run(["git","add","."],cwd=repo,check=True); subprocess.run(["git","commit","-m","init"],cwd=repo,check=True,capture_output=True)
    script=Path(__file__).resolve().parents[1]/"install_into_repo.py"
    p=subprocess.run([sys.executable,str(script),str(repo)],text=True,capture_output=True)
    assert p.returncode==0, p.stderr
    assert (repo/".microagent/orchestrator.py").exists()
    assert (repo/".microagent/context_guard.py").exists()
    settings_path = repo/".qwen/settings.json"
    assert settings_path.exists()
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    assert isinstance(settings["modelProviders"]["openai"], list)
    assert "write_file" in settings["hooks"]["PreToolUse"][0]["matcher"]
    assert "edit" in settings["hooks"]["PreToolUse"][0]["matcher"]
    assert (repo/"agent.ps1").exists() and (repo/"agent.cmd").exists() and (repo/"agent.sh").exists()
    assert (repo/"agent-control.ps1").exists() and (repo/"agent-control.cmd").exists() and (repo/"agent-control.sh").exists()
    assert (repo/".microagent/control.py").exists()
    assert "LOCAL_MICROAGENT_V7_BEGIN" in (repo/"AGENTS.md").read_text()
