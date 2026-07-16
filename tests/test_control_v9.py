from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "template/.microagent/control.py"
SPEC = importlib.util.spec_from_file_location("localcode_control_v9", PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_current_target_is_frozen_to_active_microtask():
    state = {"active_scope": "microtask", "active_microtask": "M2"}
    assert MODULE.resolve_target(state, "current") == "M2"
    assert MODULE.resolve_target(state, "M3") == "M3"


def test_run_approval_creates_exact_grant(tmp_path: Path):
    run = tmp_path / "run"
    pending = run / "control/approvals/pending/A-1.json"
    pending.parent.mkdir(parents=True)
    pending.write_text(json.dumps({"id": "A-1", "kind": "shell", "payload": {"command": "pytest -q"}}), encoding="utf-8")
    MODULE.decide_approval(run, "A-1", "approve", "", "run")
    settings = json.loads((run / "control/settings.json").read_text(encoding="utf-8"))
    assert settings["grants"] == [{"kind": "shell", "payload": {"command": "pytest -q"}}]
