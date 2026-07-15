from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from test_end_to_end_fake_qwen import assert_final, prepare_repo, write_fake_qwen, write_task


def wait_for_state(repo: Path, predicate, timeout: float = 30.0) -> tuple[Path, dict]:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        paths = sorted((repo / ".agent-runs").glob("*/state.json"))
        if paths:
            path = paths[-1]
            try:
                state = json.loads(path.read_text(encoding="utf-8"))
                last = state
                if predicate(state):
                    return path, state
            except (OSError, json.JSONDecodeError):
                pass
        time.sleep(0.1)
    raise AssertionError(f"État attendu non atteint. Dernier état: {last}")


def start_agent(repo: Path, task: Path, fake: Path) -> subprocess.Popen[str]:
    env = os.environ.copy()
    env["PATH"] = str(fake.parent) + os.pathsep + env["PATH"]
    return subprocess.Popen(
        [sys.executable, str(repo / ".microagent" / "orchestrator.py"), str(task)],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )


def control(repo: Path, action: str, message: str = "", target: str = "current") -> subprocess.CompletedProcess[str]:
    cmd = [
        sys.executable,
        str(repo / ".microagent" / "control.py"),
        action,
    ]
    if message:
        cmd.append(message)
    cmd.extend(["--repo", str(repo), "--target", target])
    return subprocess.run(cmd, cwd=repo, text=True, capture_output=True)


def wait_for_coder(repo: Path) -> tuple[Path, dict]:
    return wait_for_state(
        repo,
        lambda state: state.get("active_microtask") == "S1"
        and str(state.get("active_operation", "")).startswith("qwen:coder-S1"),
    )


def finish(proc: subprocess.Popen[str], timeout: float = 90.0) -> tuple[str, str]:
    stdout, stderr = proc.communicate(timeout=timeout)
    assert proc.returncode == 0, stdout + "\n" + stderr
    return stdout, stderr


def test_control_cli_writes_atomic_intervention(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_dir = repo / ".agent-runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / "state.json").write_text(
        json.dumps({"phase": "worktree", "active_scope": "microtask", "active_microtask": "M2"}),
        encoding="utf-8",
    )
    source = Path(__file__).resolve().parents[1] / "template" / ".microagent" / "control.py"
    target = repo / ".microagent" / "control.py"
    target.parent.mkdir()
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    proc = control(repo, "note", "Vérifie aussi le cas stop/go")
    assert proc.returncode == 0, proc.stderr
    files = list((run_dir / "control" / "inbox").glob("*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text(encoding="utf-8"))
    assert payload["action"] == "note"
    assert payload["target"] == "current"
    assert payload["message"] == "Vérifie aussi le cas stop/go"


def test_pause_note_resume_preserves_flow(tmp_path):
    kit = Path(__file__).resolve().parents[1]
    repo = prepare_repo(tmp_path, kit)
    task = write_task(repo)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "qwen"
    write_fake_qwen(fake, delay_seconds=6.0)
    proc = start_agent(repo, task, fake)
    wait_for_coder(repo)

    paused = control(repo, "pause")
    assert paused.returncode == 0, paused.stderr
    wait_for_state(repo, lambda state: state.get("human_control", {}).get("status") == "paused")
    noted = control(repo, "note", "Conserve un test direct de value() pendant la reprise")
    assert noted.returncode == 0, noted.stderr
    resumed = control(repo, "resume", "Reprends avec la note humaine")
    assert resumed.returncode == 0, resumed.stderr

    finish(proc)
    assert_final(repo)
    state_path = next((repo / ".agent-runs").glob("*/state.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    kinds = [item["kind"] for item in state["history"]]
    assert "human_pause" in kinds
    assert "human_resume" in kinds
    prompts = "\n".join(p.read_text(encoding="utf-8") for p in (state_path.parent / "qwen").glob("*.prompt.txt"))
    assert "Conserve un test direct de value()" in prompts


def test_human_revision_rolls_back_and_replays_microtask(tmp_path):
    kit = Path(__file__).resolve().parents[1]
    repo = prepare_repo(tmp_path, kit)
    task = write_task(repo)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "qwen"
    write_fake_qwen(fake, delay_seconds=6.0)
    proc = start_agent(repo, task, fake)
    wait_for_coder(repo)

    sent = control(repo, "revise", "Reprends M1 en vérifiant explicitement le test ciblé")
    assert sent.returncode == 0, sent.stderr
    finish(proc)
    assert_final(repo)
    state_path = next((repo / ".agent-runs").glob("*/state.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["human_control"]["revisions_by_target"]["S1"] == 1
    assert any(item["kind"] == "human_revision_scheduled" for item in state["history"])
    snapshots = list((state_path.parent / "context-mirror" / "human" / "snapshots").glob("*.patch"))
    assert snapshots


def test_human_replan_is_bounded_expansion_of_active_parent(tmp_path):
    kit = Path(__file__).resolve().parents[1]
    repo = prepare_repo(tmp_path, kit)
    task = write_task(repo)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "qwen"
    write_fake_qwen(fake, delay_seconds=6.0)
    proc = start_agent(repo, task, fake)
    wait_for_coder(repo)

    sent = control(repo, "replan", "Décompose seulement S1 car une étape de preuve séparée est nécessaire")
    assert sent.returncode == 0, sent.stderr
    finish(proc)
    assert_final(repo)
    state_path = next((repo / ".agent-runs").glob("*/state.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["human_control"]["human_replans"] == 1
    assert state["microtask_status"]["S1"] == "expanded"
    assert state["microtask_status"]["S1a"] == "done"
    assert state["expansions"]["S1"]["children"] == ["S1a"]


def test_human_review_forces_an_extra_reviewer(tmp_path):
    kit = Path(__file__).resolve().parents[1]
    repo = prepare_repo(tmp_path, kit)
    task = write_task(repo)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "qwen"
    write_fake_qwen(fake, delay_seconds=6.0)
    proc = start_agent(repo, task, fake)
    wait_for_coder(repo)

    sent = control(repo, "review", "Vérifie explicitement qu'aucun appelant ne dépend encore de la valeur 1")
    assert sent.returncode == 0, sent.stderr
    finish(proc)
    assert_final(repo)
    state_path = next((repo / ".agent-runs").glob("*/state.json"))
    state = json.loads(state_path.read_text(encoding="utf-8"))
    reviews = state["human_control"]["mandatory_reviews"]
    assert reviews and reviews[0]["consumed"] is True
    prompt_names = [p.name for p in (state_path.parent / "qwen").glob("*.prompt.txt")]
    assert any("human-review" in name for name in prompt_names)


def test_human_abort_preserves_worktree_and_marks_run(tmp_path):
    kit = Path(__file__).resolve().parents[1]
    repo = prepare_repo(tmp_path, kit)
    task = write_task(repo)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "qwen"
    write_fake_qwen(fake, delay_seconds=6.0)
    proc = start_agent(repo, task, fake)
    state_path, state = wait_for_coder(repo)

    sent = control(repo, "abort")
    assert sent.returncode == 0, sent.stderr
    stdout, stderr = proc.communicate(timeout=60)
    assert proc.returncode == 2, stdout + "\n" + stderr
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["phase"] == "aborted"
    assert Path(state["worktree"]).exists()
    assert any(item["kind"] == "human_abort" for item in state["history"])
