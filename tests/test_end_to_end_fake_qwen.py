from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def run(cmd, cwd, env=None):
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True, env=env)


def prepare_repo(tmp_path: Path, kit: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    assert run(["git", "init"], repo).returncode == 0
    run(["git", "config", "user.email", "test@example.com"], repo)
    run(["git", "config", "user.name", "Test"], repo)
    (repo / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")
    (repo / "UNDECLARED_DOC.md").write_text("# Must stay untouched\n", encoding="utf-8")
    (repo / "NOTES.md").write_text("# Notes\n", encoding="utf-8")
    assert run(["git", "add", "."], repo).returncode == 0
    assert run(["git", "commit", "-m", "initial"], repo).returncode == 0
    installed = run([sys.executable, str(kit / "install_into_repo.py"), str(repo)], repo)
    assert installed.returncode == 0, installed.stderr
    config_path = repo / ".microagent" / "config.json"
    config = __import__("json").loads(config_path.read_text(encoding="utf-8"))
    config["unload_ollama_between_model_switches"] = False
    config_path.write_text(__import__("json").dumps(config, indent=2) + "\n", encoding="utf-8")
    assert run(["git", "add", "."], repo).returncode == 0
    assert run(["git", "commit", "-m", "install agent"], repo).returncode == 0
    return repo


def write_task(repo: Path, documentation: bool = False) -> Path:
    task = repo / ".tasks" / "TASK-T-1.md"
    docs = (
        ',\n  "documentation_updates": [{"path":"NOTES.md","instruction":"Add T-1 result","required_markers":["T-1 COMPLETE"],"must_change":true,"allow_create":false}]'
        if documentation else ""
    )
    task.write_text(
        f'''<!-- AGENT_TASK_META
{{
  "task_id": "T-1",
  "validation_commands": ["python test_value.py"],
  "full_suite_commands": ["python test_value.py"],
  "test_file_globs": ["test_*.py"],
  "commit_message": "feat: implement T-1"{docs}
}}
AGENT_TASK_META -->
# T-1
Change value() from 1 to 2 and test it.
''',
        encoding="utf-8",
    )
    return task


def write_fake_qwen(
    path: Path,
    replan: bool = False,
    context_request: bool = False,
    review_finding: bool = False,
    delay_seconds: float = 0.0,
) -> None:
    path.write_text(
        f'''#!/usr/bin/python3 -S
import json, sys, time
from pathlib import Path
args=sys.argv[1:]
if '--help' in args:
    print('--json-schema --max-session-turns --max-tool-calls --max-wall-time --exclude-tools --approval-mode --model --output-format --sandbox')
    raise SystemExit(0)
prompt=args[args.index('-p')+1]
schema=Path(args[args.index('--json-schema')+1][1:]).name
cwd=Path.cwd()
REPLAN={str(replan)}
CONTEXT_REQUEST={str(context_request)}
REVIEW_FINDING={str(review_finding)}
DELAY_SECONDS={float(delay_seconds)!r}
STATE=Path(str(cwd)+'.fake-qwen-state')
DELAY_STATE=Path(str(cwd)+'.fake-qwen-delay-state')

def micro(task_id):
    return {{"id":task_id,"title":"change value","kind":"implementation","goal":"value returns 2","depends_on":[],"likely_files":["app.py","test_value.py"],"symbols":["value"],"invariants":[],"acceptance":["value() == 2"],"test_commands":["python test_value.py"],"forbidden_changes":[]}}

if schema == 'plan.schema.json':
    payload={{"mission_summary":"change value","verified_assumptions":["app.py exists"],"risks":[],"microtasks":[micro('S1')]}}
elif schema == 'expansion.schema.json':
    child=micro('S1a')
    child.update({{"parent_id":"S1","expansion_depth":1,"necessity_for_parent":"separate the necessary implementation proof","parent_acceptance_covered":["value() == 2"]}})
    payload={{"parent_id":"S1","replan_reason":"initial parent needs a smaller necessary step","children":[child]}}
elif schema == 'scout.schema.json':
    payload={{"status":"READY","summary":"targeted","files":[{{"path":"app.py","why":"defines value","symbols":["value"],"ranges":[{{"start":1,"end":2,"reason":"definition"}}]}}],"relations":[],"tests":["test_value.py"],"facts":["value returns 1"],"unknowns":[]}}
elif schema == 'plan_review.schema.json':
    payload={{"verdict":"PASS","issues":[]}}
elif schema == 'plan_judge.schema.json':
    payload={{"verdict":"PASS","merged_issues":[],"rejected_issues":[]}}
elif schema == 'review.schema.json':
    if REVIEW_FINDING and 'logic-review' in prompt and not STATE.exists():
        payload={{"verdict":"FINDINGS","findings":[{{"severity":"major","category":"regression","file":"app.py","symbol":"value","mission_anchor":"T-1 objective","scenario":"call value","expected":"focused regression proof","observed":"proof missing","evidence":"reviewed diff","suggested_test":"add focused value test"}}]}}
    else:
        payload={{"verdict":"PASS","findings":[]}}
elif schema == 'judge.schema.json':
    if REVIEW_FINDING and not STATE.exists():
        payload={{"accepted":[{{"source":"logic","severity":"major","file":"app.py","symbol":"value","mission_anchor":"T-1 objective","scenario":"call value","expected":"focused regression proof","observed":"proof missing","problem":"missing focused regression proof","evidence":"logic review and diff","required_test":"add focused value test"}}],"rejected":[]}}
    else:
        payload={{"accepted":[],"rejected":[]}}
elif schema == 'repair_task.schema.json':
    payload={{"title":"repair","goal":"repair","likely_files":["app.py","test_value.py"],"symbols":["value"],"invariants":[],"acceptance":["tests pass"],"test_commands":["python test_value.py"],"forbidden_changes":[]}}
elif schema == 'failure_triage.schema.json':
    payload={{"decision":"EXPAND_MICROTASK","rationale":"parent needs a necessary child","corrected_commands":[],"repair_task":None}}
elif schema == 'coder_result.schema.json':
    if 'finaliseur documentaire' in prompt:
        updates = list(cwd.glob('.agent-context/**/final/documentation-updates.json'))
        if updates:
            items = json.loads(updates[-1].read_text(encoding='utf-8'))
            for item in items:
                target = cwd / item['path']
                previous = target.read_text(encoding='utf-8') if target.exists() else ''
                markers = '\\n'.join(item.get('required_markers', []))
                target.write_text(previous + '\\n' + markers + '\\n', encoding='utf-8')
        payload={{"status":"DONE","summary":"docs","command_corrections":[],"validated_commands":[],"escalation":"NONE","unresolved":[],"research_requests":[],"addressed_finding_ids":[],"unresolved_finding_ids":[]}}
    else:
        is_s1 = 'S1.json' in prompt
        is_review_repair = 'repair_ticket' in prompt or 'repairs/' in prompt
        if DELAY_SECONDS and is_s1 and not DELAY_STATE.exists():
            DELAY_STATE.write_text('started', encoding='utf-8')
            time.sleep(DELAY_SECONDS)
        addressed = []
        if is_review_repair:
            tickets = list(cwd.glob('.agent-context/**/repairs/*.json'))
            if tickets:
                ticket = json.loads(tickets[-1].read_text(encoding='utf-8'))
                addressed = [x['finding_id'] for x in ticket.get('accepted_findings', [])]
        needs_context = CONTEXT_REQUEST and is_s1 and 'scouts/S1-1.json' not in prompt
        if needs_context:
            payload={{"status":"BLOCKED","summary":"need caller information","command_corrections":[],"validated_commands":[],"escalation":"CONTEXT","unresolved":["caller relation"],"research_requests":[{{"question":"Find callers of value","symbols":["value"],"suspected_paths":["app.py"],"reason":"need impact"}}],"addressed_finding_ids":[],"unresolved_finding_ids":[]}}
        else:
            value = 1 if (REPLAN and is_s1) else 2
            (cwd/'app.py').write_text(f'def value():\\n    return {{value}}\\n',encoding='utf-8')
            (cwd/'test_value.py').write_text('from app import value\\nassert value() == 2\\n',encoding='utf-8')
            if is_review_repair:
                (cwd/'test_value_regression.py').write_text('from app import value\\nassert value() == 2\\n',encoding='utf-8')
                STATE.write_text('repaired', encoding='utf-8')
            status = 'BLOCKED' if (REPLAN and is_s1) else 'DONE'
            escalation = 'PLAN' if (REPLAN and is_s1) else 'NONE'
            payload={{"status":status,"summary":"implemented","command_corrections":[],"validated_commands":["python test_value.py"],"escalation":escalation,"unresolved":[],"research_requests":[],"addressed_finding_ids":addressed,"unresolved_finding_ids":[]}}
else:
    raise SystemExit('unknown schema '+schema)
print(json.dumps(payload))
''',
        encoding="utf-8",
    )
    path.chmod(0o755)


def execute(repo: Path, task: Path, fake: Path):
    env = os.environ.copy()
    env["PATH"] = str(fake.parent) + os.pathsep + env["PATH"]
    return run(
        [sys.executable, str(repo / ".microagent" / "orchestrator.py"), str(task)],
        repo,
        env,
    )


def assert_final(repo: Path):
    branches = run(["git", "branch", "--list", "agent/*"], repo)
    assert "agent/" in branches.stdout
    branch = branches.stdout.strip().lstrip("*+ ")
    show = run(["git", "show", f"{branch}:app.py"], repo)
    assert "return 2" in show.stdout
    log = run(["git", "rev-list", "--count", f"HEAD..{branch}"], repo)
    assert log.stdout.strip() == "1"
    return branch


def test_end_to_end_with_fake_qwen(tmp_path):
    kit = Path(__file__).resolve().parents[1]
    repo = prepare_repo(tmp_path, kit)
    task = write_task(repo)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "qwen"
    write_fake_qwen(fake, replan=False)
    proc = execute(repo, task, fake)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert_final(repo)


def test_dynamic_replan_with_fake_qwen(tmp_path):
    kit = Path(__file__).resolve().parents[1]
    repo = prepare_repo(tmp_path, kit)
    task = write_task(repo)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "qwen"
    write_fake_qwen(fake, replan=True)
    proc = execute(repo, task, fake)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    branch = assert_final(repo)
    state_files = list((repo / ".agent-runs").glob("*/state.json"))
    assert state_files
    state = __import__('json').loads(state_files[0].read_text())
    assert state["dynamic_replans"] == 1
    assert state["microtask_status"]["S1"] == "expanded"
    assert state["microtask_status"]["S1a"] == "done"
    assert state["expansions"]["S1"]["children"] == ["S1a"]


def test_context_request_spawns_targeted_scout(tmp_path):
    kit = Path(__file__).resolve().parents[1]
    repo = prepare_repo(tmp_path, kit)
    task = write_task(repo)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "qwen"
    write_fake_qwen(fake, context_request=True)
    proc = execute(repo, task, fake)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert_final(repo)
    state_files = list((repo / ".agent-runs").glob("*/state.json"))
    state = __import__('json').loads(state_files[0].read_text())
    assert state["context_requests"]["S1"] == 1
    assert len(state["scout_packs"]["S1"]) == 2


def test_one_command_launcher_reaches_commit(tmp_path):
    kit = Path(__file__).resolve().parents[1]
    repo = prepare_repo(tmp_path, kit)
    task = write_task(repo)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "qwen"
    write_fake_qwen(fake)
    env = os.environ.copy()
    env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
    env["MICROAGENT_SKIP_OLLAMA_CHECK"] = "1"
    proc = run([str(repo / "agent.sh"), str(task.relative_to(repo))], repo, env)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert "Préflight OK" in proc.stdout
    assert_final(repo)


def test_review_finding_becomes_direct_repair_ticket(tmp_path):
    kit = Path(__file__).resolve().parents[1]
    repo = prepare_repo(tmp_path, kit)
    task = write_task(repo)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "qwen"
    write_fake_qwen(fake, review_finding=True)
    proc = execute(repo, task, fake)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    assert_final(repo)
    state_path = next((repo / ".agent-runs").glob("*/state.json"))
    state = __import__("json").loads(state_path.read_text())
    assert state["finding_history"]["S1"]
    run_dir = state_path.parent
    tickets = list((run_dir / "context-mirror" / "repairs").glob("*.json"))
    assert tickets
    ticket = __import__("json").loads(tickets[0].read_text())
    assert ticket["accepted_findings"][0]["finding_id"].startswith("F-")
    assert ticket["source_artifacts"]["reviews"]
    prompts = "\n".join(p.read_text() for p in (run_dir / "qwen").glob("*.prompt.txt"))
    assert "repairs/" in prompts
    assert len(state["scout_packs"]["S1"]) >= 2


def test_documentation_is_only_driven_by_task(tmp_path):
    kit = Path(__file__).resolve().parents[1]
    repo = prepare_repo(tmp_path, kit)
    task = write_task(repo, documentation=True)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake = bindir / "qwen"
    write_fake_qwen(fake)
    proc = execute(repo, task, fake)
    assert proc.returncode == 0, proc.stdout + "\n" + proc.stderr
    branch = assert_final(repo)
    notes = run(["git", "show", f"{branch}:NOTES.md"], repo)
    assert "T-1 COMPLETE" in notes.stdout
    undeclared = run(["git", "show", f"{branch}:UNDECLARED_DOC.md"], repo)
    assert "Must stay untouched" in undeclared.stdout
