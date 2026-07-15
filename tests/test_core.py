from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1] / "template" / ".microagent"
sys.path.insert(0, str(ROOT))

from core import (apply_expansion_fragment, classify_failure, ready_tasks, run_command, shell_command, validate_expansion_fragment, validate_plan)
from taskmeta import load_task


def good_plan():
    return {
        "mission_summary": "x",
        "verified_assumptions": [],
        "risks": [],
        "microtasks": [
            {
                "id": "S1",
                "title": "a",
                "kind": "implementation",
                "goal": "g",
                "depends_on": [],
                "likely_files": ["src/a.py"],
                "symbols": ["A"],
                "invariants": [],
                "acceptance": ["ok"],
                "test_commands": ["python -m pytest -q"],
                "forbidden_changes": [],
            },
            {
                "id": "S2",
                "title": "b",
                "kind": "tests",
                "goal": "g2",
                "depends_on": ["S1"],
                "likely_files": ["tests/test_a.py"],
                "symbols": [],
                "invariants": [],
                "acceptance": ["ok2"],
                "test_commands": ["python -m pytest -q"],
                "forbidden_changes": [],
            },
        ],
    }


def test_plan_valid_and_ready():
    plan = good_plan()
    assert validate_plan(plan, 20) == []
    state = {"microtask_status": {}}
    assert [task["id"] for task in ready_tasks(plan, state)] == ["S1"]
    state["microtask_status"]["S1"] = "done"
    assert [task["id"] for task in ready_tasks(plan, state)] == ["S2"]


def test_plan_rejects_cycle_and_path():
    plan = good_plan()
    plan["microtasks"][0]["depends_on"] = ["S2"]
    plan["microtasks"][0]["likely_files"] = ["../x"]
    errors = validate_plan(plan, 20)
    assert any("cycle" in error for error in errors)
    assert any("dangereux" in error for error in errors)


def test_task_meta_and_plan_check(tmp_path):
    task = tmp_path / "TASK.md"
    task.write_text(
        '''<!-- AGENT_TASK_META
{"task_id":"T-1","validation_commands":["python -V"],"full_suite_commands":[]}
AGENT_TASK_META -->
# T''',
        encoding="utf-8",
    )
    _, meta = load_task(task)
    assert meta["task_id"] == "T-1"
    assert meta["context_files"] == []
    assert meta["documentation_updates"] == []


def test_command_runner_and_classifier(tmp_path):
    result = run_command(f'"{sys.executable}" -c "print(123)"', tmp_path, 10)
    assert result.ok and "123" in result.output
    argv, family = shell_command("echo ok")
    assert family in {"cmd", "bash"} and argv
    assert classify_failure(1, "foo: command not found") == "COMMAND"
    assert classify_failure(1, "AssertionError") == "CODE"
    assert classify_failure(1, "No module named pytest") == "ENVIRONMENT"


def test_repeated_finding_triggers_replan_gate():
    from orchestrator import Orchestrator

    orch = object.__new__(Orchestrator)
    orch.state = {"finding_history": {}}
    orch.cfg = {"max_same_finding_recurrence": 1}
    orch.save_state = lambda: None
    judgment = {"accepted": [{"finding_id": "F-abc"}]}
    exceeded, repeated = orch.finding_recurrence_exceeded("S1", judgment)
    assert not exceeded and repeated == []
    exceeded, repeated = orch.finding_recurrence_exceeded("S1", judgment)
    assert exceeded and repeated == ["F-abc"]


def test_expansion_only_decomposes_parent_and_reconnects_downstream():
    plan = good_plan()
    parent = plan["microtasks"][0]
    fragment = {
        "parent_id": "S1",
        "replan_reason": "type and behavior need separate proof",
        "children": [
            {
                "id": "S1a",
                "parent_id": "S1",
                "expansion_depth": 1,
                "necessity_for_parent": "define the value contract before behavior",
                "parent_acceptance_covered": ["ok"],
                "title": "contract",
                "kind": "implementation",
                "goal": "contract",
                "depends_on": [],
                "likely_files": ["src/a.py"],
                "symbols": ["A"],
                "invariants": [],
                "acceptance": ["contract exists"],
                "test_commands": ["python -m pytest -q"],
                "forbidden_changes": [],
            }
        ],
    }
    assert validate_expansion_fragment(plan, "S1", fragment, 4, 2) == []
    merged, leaves = apply_expansion_fragment(plan, "S1", fragment)
    assert leaves == ["S1a"]
    ids = [task["id"] for task in merged["microtasks"]]
    assert ids == ["S1a", "S2"]
    assert merged["microtasks"][1]["depends_on"] == ["S1a"]
    assert parent["id"] == "S1"


def test_expansion_rejects_uncovered_parent_acceptance_and_unrelated_dependency():
    plan = good_plan()
    fragment = {
        "parent_id": "S1",
        "replan_reason": "x",
        "children": [{
            "id": "S1a", "parent_id": "S1", "expansion_depth": 1,
            "necessity_for_parent": "x", "parent_acceptance_covered": [],
            "title": "x", "kind": "implementation", "goal": "x",
            "depends_on": ["S2"], "likely_files": ["src/a.py"], "symbols": [],
            "invariants": [], "acceptance": ["x"],
            "test_commands": ["python -m pytest -q"], "forbidden_changes": []
        }]
    }
    errors = validate_expansion_fragment(plan, "S1", fragment, 4, 2)
    assert any("aucun critère parent" in error for error in errors)
    assert any("hors fragment" in error for error in errors)
