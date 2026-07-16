#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

ACTIONS = {
    "note", "constraint", "pause", "resume", "review", "revise", "replan", "abort",
    "status", "stats", "ask", "answer", "approve", "deny", "approval",
}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def latest_run(repo: Path, requested: str) -> Path:
    runs = repo / ".agent-runs"
    if requested != "latest":
        candidate = Path(requested)
        candidate = candidate if candidate.is_absolute() else runs / candidate
        if not candidate.exists():
            raise RuntimeError(f"Run introuvable: {candidate}")
        return candidate.resolve()
    if not runs.exists():
        raise RuntimeError("Aucun run dans .agent-runs")
    candidates = sorted(
        (path for path in runs.iterdir() if path.is_dir() and (path / "state.json").exists()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise RuntimeError("Aucun run avec state.json")
    active = []
    for path in candidates:
        try:
            phase = str(load_json(path / "state.json").get("phase", ""))
        except Exception:
            phase = ""
        if phase not in {"done", "aborted"}:
            active.append(path)
    return (active or candidates)[0].resolve()


def resolve_target(state: dict[str, Any], raw: str) -> str:
    value = str(raw or "current").strip()
    if value != "current":
        return value
    return str(state.get("active_microtask") or state.get("active_scope") or "global")


def validate_context_files(repo: Path, run_dir: Path, values: list[str]) -> list[str]:
    state = load_json(run_dir / "state.json")
    worktree = Path(str(state.get("worktree", "")))
    result: list[str] = []
    for raw in values:
        path = Path(raw)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"Chemin de contexte refusé: {raw}")
        normalized = path.as_posix()
        if not (worktree / path).exists() and not (repo / path).exists():
            raise RuntimeError(f"Fichier de contexte absent: {normalized}")
        result.append(normalized)
    return list(dict.fromkeys(result))


def pending_approvals(run_dir: Path) -> list[dict[str, Any]]:
    root = run_dir / "control" / "approvals" / "pending"
    return [load_json(path) for path in sorted(root.glob("*.json"))] if root.exists() else []


def print_status(run_dir: Path) -> None:
    state = load_json(run_dir / "state.json")
    control = state.get("human_control", {})
    print(f"Run: {run_dir.name}")
    print(f"Phase: {state.get('phase')}")
    print(f"Scope actif: {state.get('active_scope')}")
    print(f"Micro-tâche active: {state.get('active_microtask')}")
    print(f"Opération active: {state.get('active_operation')}")
    print(f"Contrôle: {control.get('status', 'running')}")
    print(f"Interventions traitées: {control.get('processed_count', 0)}")
    print(f"Worktree: {state.get('worktree')}")
    approvals = pending_approvals(run_dir)
    print(f"Autorisations en attente: {len(approvals)}")
    for item in approvals[:5]:
        print(f"  {item.get('id')} {item.get('kind')} cible={item.get('target')}")
    questions = [item for item in state.get("questions", []) if not item.get("answered")]
    print(f"Questions en attente: {len(questions)}")
    for item in questions[:5]:
        print(f"  {item.get('id')} {item.get('question')}")


def decide_approval(run_dir: Path, request_id: str, decision: str, message: str, scope: str) -> None:
    if not request_id:
        raise RuntimeError("Identifiant d'autorisation obligatoire")
    pending = run_dir / "control" / "approvals" / "pending" / f"{request_id}.json"
    if not pending.exists():
        raise RuntimeError(f"Autorisation introuvable: {request_id}")
    request = load_json(pending)
    payload = {
        "id": request_id,
        "decision": decision,
        "message": message,
        "scope": scope,
        "decided_at": datetime.now().isoformat(timespec="seconds"),
        "author": os.environ.get("USERNAME") or os.environ.get("USER") or "human",
    }
    save_json(run_dir / "control" / "approvals" / "decisions" / f"{request_id}.json", payload)
    if decision == "approve" and scope == "run":
        settings_path = run_dir / "control" / "settings.json"
        settings = load_json(settings_path) if settings_path.exists() else {}
        grants = settings.setdefault("grants", [])
        grant = {"kind": request.get("kind"), "payload": request.get("payload")}
        if grant not in grants:
            grants.append(grant)
        save_json(settings_path, settings)
    print(f"Autorisation {request_id}: {decision}")


def set_approval_mode(run_dir: Path, mode: str) -> None:
    if mode not in {"auto", "commands", "all"}:
        raise RuntimeError("Mode attendu: auto, commands ou all")
    path = run_dir / "control" / "settings.json"
    current = load_json(path) if path.exists() else {}
    current["approval_mode"] = mode
    current["updated_at"] = datetime.now().isoformat(timespec="seconds")
    save_json(path, current)
    print(f"Mode d'autorisation: {mode}")


def print_stats(run_dir: Path) -> None:
    state = load_json(run_dir / "state.json")
    operations = list(state.get("operations", []))
    by_model: dict[str, dict[str, float]] = {}
    for item in operations:
        model = str(item.get("model", "unknown"))
        row = by_model.setdefault(model, {"calls": 0, "failed": 0, "seconds": 0.0, "input_tokens": 0, "output_tokens": 0, "switches": 0})
        row["calls"] += 1
        row["failed"] += int(item.get("status") == "failed")
        row["seconds"] += float(item.get("duration_s", 0.0) or 0.0)
        row["input_tokens"] += int(item.get("prompt_tokens_estimated", 0) or 0)
        row["output_tokens"] += int(item.get("output_tokens_estimated", 0) or 0)
        row["switches"] += int(bool(item.get("model_switch")))
    print(f"Run: {run_dir.name}")
    print(f"Opérations Qwen enregistrées: {len(operations)}")
    for model, row in sorted(by_model.items()):
        print(
            f"  {model}: appels={int(row['calls'])} échecs={int(row['failed'])} "
            f"durée={row['seconds']:.1f}s entrées≈{int(row['input_tokens'])} "
            f"sorties≈{int(row['output_tokens'])} changements={int(row['switches'])}"
        )
    history = state.get("history", [])
    retries = sum(1 for item in history if item.get("kind") == "qwen_retry")
    compacted = sum(1 for item in history if item.get("kind") == "context_compacted")
    questions = len(state.get("questions", []))
    print(f"Retries: {retries}")
    print(f"Compactions: {compacted}")
    print(f"Questions humaines: {questions}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Piloter un run LocalCode sans perdre son état")
    parser.add_argument("action", choices=sorted(ACTIONS))
    parser.add_argument("message", nargs="?", default="")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--run", default="latest")
    parser.add_argument("--target", default="current")
    parser.add_argument("--file", action="append", default=[])
    parser.add_argument("--request-id", default="")
    parser.add_argument("--scope", default="once", choices=["once", "run"])
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    run_dir = latest_run(repo, args.run)
    if args.action == "status":
        print_status(run_dir)
        return 0
    if args.action == "stats":
        print_stats(run_dir)
        return 0
    if args.action in {"approve", "deny"}:
        decide_approval(run_dir, args.request_id, "approve" if args.action == "approve" else "deny", args.message, args.scope)
        return 0
    if args.action == "approval":
        set_approval_mode(run_dir, args.message.strip().lower())
        return 0

    state = load_json(run_dir / "state.json")
    if str(state.get("phase", "")) in {"done", "aborted"}:
        raise RuntimeError(f"Le run {run_dir.name} est terminé ({state.get('phase')})")
    message = str(args.message).strip()
    if args.action in {"note", "constraint", "review", "revise", "replan", "ask", "answer"} and not message:
        raise RuntimeError(f"Un message est obligatoire pour {args.action}")
    if args.action == "answer" and not args.request_id:
        raise RuntimeError("/answer exige l'identifiant Q-...")
    if len(message) > 4000:
        raise RuntimeError("Message trop long: maximum 4000 caractères")

    raw_target = str(args.target).strip() or "current"
    resolved_target = resolve_target(state, raw_target)
    payload = {
        "id": uuid.uuid4().hex,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "action": args.action,
        "target": raw_target,
        "resolved_target": resolved_target,
        "message": message,
        "request_id": args.request_id,
        "context_files": validate_context_files(repo, run_dir, list(args.file)),
        "author": os.environ.get("USERNAME") or os.environ.get("USER") or "human",
    }
    inbox = run_dir / "control" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    save_json(inbox / f"{stamp}-{payload['id']}.json", payload)
    print(f"Intervention envoyée: {args.action}")
    print(f"Run: {run_dir.name}")
    print(f"Cible demandée: {raw_target}")
    print(f"Cible figée à l'envoi: {resolved_target}")
    if args.request_id:
        print(f"Référence: {args.request_id}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        raise SystemExit(2)
