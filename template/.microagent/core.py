from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass
class CommandResult:
    command: str
    ok: bool
    returncode: int
    duration_s: float
    output: str
    failure_class: str
    shell_family: str


def resolve_cli(name: str) -> list[str] | None:
    """Résout un CLI, y compris un shim Python sans extension sous Windows/CI."""
    resolved = shutil.which(name)
    if resolved:
        return [resolved]
    for raw_directory in os.environ.get("PATH", "").split(os.pathsep):
        directory = raw_directory.strip().strip('"')
        if not directory:
            continue
        candidate = Path(directory) / name
        if not candidate.is_file():
            continue
        try:
            first_line = candidate.open("r", encoding="utf-8", errors="replace").readline()
        except OSError:
            continue
        if first_line.startswith("#!") and "python" in first_line.lower():
            return [sys.executable, "-S", str(candidate)]
    return None


def cli_command(prefix: list[str], arguments: list[str]) -> list[str]:
    """Construit argv sans repasser le prompt par une couche shell."""
    return [*prefix, *arguments]


def _strip_windows_argument_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _windows_direct_command(command: str) -> list[str] | None:
    """Évite cmd.exe pour un exécutable absolu entre guillemets.

    Python transmet autrement les guillemets à cmd.exe sous une forme échappée que
    certaines versions de Windows interprètent comme faisant partie du nom du programme.
    Les commandes contenant des opérateurs shell restent confiées à cmd.exe.
    """
    match = re.match(r'^\s*"([^"]+)"(?:\s+(.*?))?\s*$', command, re.DOTALL)
    if not match:
        return None
    executable = match.group(1)
    tail = (match.group(2) or "").strip()
    if any(operator in tail for operator in ("&&", "||", "|", ">", "<")):
        return None
    if not os.path.isfile(executable):
        return None
    try:
        arguments = shlex.split(tail, posix=False) if tail else []
    except ValueError:
        return None
    return [executable, *(_strip_windows_argument_quotes(item) for item in arguments)]


def shell_command(command: str) -> tuple[list[str], str]:
    """Utilise un argv direct quand c'est sûr, cmd.exe pour le reste sous Windows."""
    if os.name == "nt":
        direct = _windows_direct_command(command)
        if direct is not None:
            return direct, "direct"
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        return [comspec, "/d", "/s", "/c", command], "cmd"
    return ["bash", "-lc", command], "bash"


def classify_failure(returncode: int, output: str) -> str:
    if returncode == 0:
        return "NONE"
    text = output.lower()
    command_markers = (
        "is not recognized as an internal or external command",
        "command not found",
        "syntax error near unexpected token",
        "the syntax of the command is incorrect",
        "unexpected at this time",
        "unknown option",
        "unrecognized option",
        "no such file or directory",  # often bad runner/path; triage may override
    )
    environment_markers = (
        "modulenotfounderror",
        "cannot find module",
        "could not resolve host",
        "connection refused",
        "authentication failed",
        "permission denied",
        "missing dependency",
        "not installed",
        "no module named",
        "sdk not found",
    )
    code_markers = (
        "assertionerror",
        "tests failed",
        "test failed",
        "failed,",
        "compilation failed",
        "type error",
        "syntaxerror",
        "traceback (most recent call last)",
    )
    if any(marker in text for marker in command_markers):
        return "COMMAND"
    if any(marker in text for marker in environment_markers):
        return "ENVIRONMENT"
    if any(marker in text for marker in code_markers):
        return "CODE"
    return "UNKNOWN"


def run_command(command: str, cwd: Path, timeout: int) -> CommandResult:
    started = time.monotonic()
    argv, shell_family = shell_command(command)
    try:
        proc = subprocess.run(
            argv,
            cwd=cwd,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=os.environ.copy(),
        )
        output = ((proc.stdout or "") + "\n" + (proc.stderr or "")).strip()
        return CommandResult(
            command=command,
            ok=proc.returncode == 0,
            returncode=proc.returncode,
            duration_s=round(time.monotonic() - started, 3),
            output=output[-24000:],
            failure_class=classify_failure(proc.returncode, output),
            shell_family=shell_family,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode(errors="replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode(errors="replace")
        output = f"TIMEOUT\n{stdout}\n{stderr}".strip()
        return CommandResult(
            command=command,
            ok=False,
            returncode=124,
            duration_s=round(time.monotonic() - started, 3),
            output=output[-24000:],
            failure_class="ENVIRONMENT",
            shell_family=shell_family,
        )


def result_dict(result: CommandResult) -> dict[str, Any]:
    return asdict(result)


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def slug(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._") or "task"


def validate_plan(plan: dict[str, Any], max_microtasks: int) -> list[str]:
    errors: list[str] = []
    tasks = plan.get("microtasks")
    if not isinstance(tasks, list) or not tasks:
        return ["microtasks doit être une liste non vide"]
    if len(tasks) > max_microtasks:
        errors.append(f"trop de micro-tâches: {len(tasks)} > {max_microtasks}")

    ids = [str(task.get("id", "")) for task in tasks]
    if any(not task_id for task_id in ids):
        errors.append("chaque micro-tâche doit avoir un id")
    if len(set(ids)) != len(ids):
        errors.append("ids de micro-tâches dupliqués")

    known = set(ids)
    graph: dict[str, list[str]] = {}
    for task in tasks:
        task_id = str(task.get("id", ""))
        deps = task.get("depends_on", [])
        if not isinstance(deps, list):
            errors.append(f"{task_id}: depends_on doit être une liste")
            deps = []
        unknown = [dep for dep in deps if dep not in known]
        if unknown:
            errors.append(f"{task_id}: dépendances inconnues {unknown}")
        graph[task_id] = list(deps)
        for key in ("title", "goal", "acceptance", "test_commands"):
            if not task.get(key):
                errors.append(f"{task_id}: {key} vide")
        for raw_path in [*task.get("likely_files", []), *task.get("forbidden_changes", [])]:
            path = Path(str(raw_path))
            if path.is_absolute() or ".." in path.parts:
                errors.append(f"{task_id}: chemin dangereux {raw_path}")

    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node: str) -> None:
        if node in visiting:
            errors.append(f"cycle de dépendances autour de {node}")
            return
        if node in visited:
            return
        visiting.add(node)
        for dep in graph.get(node, []):
            dfs(dep)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        dfs(node)
    return errors


def ready_tasks(plan: dict[str, Any], state: dict[str, Any]) -> list[dict[str, Any]]:
    statuses = state.get("microtask_status", {})
    done = {task_id for task_id, status in statuses.items() if status == "done"}
    return [
        task
        for task in plan["microtasks"]
        if statuses.get(task["id"], "pending") == "pending"
        and set(task.get("depends_on", [])).issubset(done)
    ]


def plan_task_map(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(task["id"]): task for task in plan.get("microtasks", [])}


def validate_expansion_fragment(
    plan: dict[str, Any],
    parent_id: str,
    fragment: dict[str, Any],
    max_children: int,
    max_depth: int,
) -> list[str]:
    """Validate that a replan only decomposes one failed parent into necessary children."""
    errors: list[str] = []
    task_map = plan_task_map(plan)
    parent = task_map.get(parent_id)
    if parent is None:
        return [f"parent absent du plan: {parent_id}"]
    if str(fragment.get("parent_id", "")) != parent_id:
        errors.append("le fragment ne cible pas exactement la micro-tâche échouée")
    reason = str(fragment.get("replan_reason", "")).strip()
    if not reason:
        errors.append("replan_reason vide")
    children = fragment.get("children")
    if not isinstance(children, list) or not children:
        return [*errors, "children doit être une liste non vide"]
    if len(children) > max_children:
        errors.append(f"trop d'enfants: {len(children)} > {max_children}")

    next_depth = int(parent.get("expansion_depth", 0)) + 1
    if next_depth > max_depth:
        errors.append(f"profondeur maximale dépassée: {next_depth} > {max_depth}")

    existing_ids = set(task_map) - {parent_id}
    child_ids = [str(child.get("id", "")) for child in children]
    if any(not child_id for child_id in child_ids):
        errors.append("chaque enfant doit avoir un id")
    if len(set(child_ids)) != len(child_ids):
        errors.append("ids d'enfants dupliqués")
    collisions = sorted(set(child_ids) & existing_ids)
    if collisions:
        errors.append(f"ids déjà utilisés: {collisions}")

    parent_acceptance = [str(item) for item in parent.get("acceptance", [])]
    covered: set[str] = set()
    allowed_deps = set(parent.get("depends_on", [])) | set(child_ids)
    for child in children:
        child_id = str(child.get("id", ""))
        if not child_id.startswith(parent_id) or child_id == parent_id:
            errors.append(f"{child_id or '?'}: l'id doit dériver de {parent_id} (ex. {parent_id}a)")
        if str(child.get("parent_id", "")) != parent_id:
            errors.append(f"{child_id}: parent_id incorrect")
        if int(child.get("expansion_depth", -1)) != next_depth:
            errors.append(f"{child_id}: expansion_depth doit valoir {next_depth}")
        if not str(child.get("necessity_for_parent", "")).strip():
            errors.append(f"{child_id}: nécessité pour le parent non expliquée")
        child_coverage = [str(item) for item in child.get("parent_acceptance_covered", [])]
        if not child_coverage:
            errors.append(f"{child_id}: aucun critère parent couvert")
        unknown_coverage = [item for item in child_coverage if item not in parent_acceptance]
        if unknown_coverage:
            errors.append(f"{child_id}: critères parent inventés {unknown_coverage}")
        covered.update(child_coverage)
        deps = [str(item) for item in child.get("depends_on", [])]
        forbidden_deps = [dep for dep in deps if dep not in allowed_deps or dep == parent_id or dep == child_id]
        if forbidden_deps:
            errors.append(f"{child_id}: dépendances hors fragment {forbidden_deps}")

    missing_coverage = [item for item in parent_acceptance if item not in covered]
    if missing_coverage:
        errors.append(f"critères de {parent_id} non couverts: {missing_coverage}")

    # Validate the child dependency graph independently.
    child_graph = {
        str(child.get("id", "")): [
            str(dep) for dep in child.get("depends_on", []) if str(dep) in set(child_ids)
        ]
        for child in children
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node: str) -> None:
        if node in visiting:
            errors.append(f"cycle dans l'expansion autour de {node}")
            return
        if node in visited:
            return
        visiting.add(node)
        for dep in child_graph.get(node, []):
            dfs(dep)
        visiting.remove(node)
        visited.add(node)

    for node in child_graph:
        dfs(node)
    return errors


def apply_expansion_fragment(
    plan: dict[str, Any], parent_id: str, fragment: dict[str, Any]
) -> tuple[dict[str, Any], list[str]]:
    """Replace one parent by children and reconnect downstream tasks to fragment leaves."""
    task_map = plan_task_map(plan)
    parent = task_map[parent_id]
    parent_deps = [str(dep) for dep in parent.get("depends_on", [])]
    children = [dict(child) for child in fragment["children"]]
    child_ids = [str(child["id"]) for child in children]
    referenced = {
        str(dep)
        for child in children
        for dep in child.get("depends_on", [])
        if str(dep) in set(child_ids)
    }
    leaves = [child_id for child_id in child_ids if child_id not in referenced]
    if not leaves:
        leaves = child_ids[-1:]

    normalized_children: list[dict[str, Any]] = []
    for child in children:
        deps = [str(dep) for dep in child.get("depends_on", [])]
        child["depends_on"] = list(dict.fromkeys([*parent_deps, *deps]))
        normalized_children.append(child)

    merged_tasks: list[dict[str, Any]] = []
    for task in plan.get("microtasks", []):
        task_id = str(task["id"])
        if task_id == parent_id:
            merged_tasks.extend(normalized_children)
            continue
        copied = dict(task)
        deps = [str(dep) for dep in copied.get("depends_on", [])]
        if parent_id in deps:
            copied["depends_on"] = list(
                dict.fromkeys([dep for dep in deps if dep != parent_id] + leaves)
            )
        merged_tasks.append(copied)

    merged = dict(plan)
    merged["microtasks"] = merged_tasks
    merged["risks"] = list(plan.get("risks", [])) + [
        f"{parent_id} décomposé: {fragment.get('replan_reason', '')}"
    ]
    return merged, leaves
