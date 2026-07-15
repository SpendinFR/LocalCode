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

ACTIONS = {"note", "pause", "resume", "review", "revise", "replan", "abort", "status"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_run(repo: Path, requested: str) -> Path:
    runs = repo / ".agent-runs"
    if requested != "latest":
        candidate = Path(requested)
        if not candidate.is_absolute():
            candidate = runs / candidate
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
        if phase not in {"done", "aborted", "failed"}:
            active.append(path)
    return (active or candidates)[0].resolve()


def validate_context_files(repo: Path, run_dir: Path, values: list[str]) -> list[str]:
    result: list[str] = []
    for raw in values:
        path = Path(raw)
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"Chemin de contexte refusé: {raw}")
        normalized = path.as_posix()
        state = load_json(run_dir / "state.json")
        worktree_raw = str(state.get("worktree", "")).strip()
        worktree = Path(worktree_raw) if worktree_raw else None
        exists_in_worktree = bool(worktree and (worktree / path).exists())
        if not exists_in_worktree and not (repo / path).exists():
            raise RuntimeError(f"Fichier de contexte absent du dépôt/worktree: {normalized}")
        result.append(normalized)
    return list(dict.fromkeys(result))


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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Piloter un run Local Codex en cours sans perdre son état"
    )
    parser.add_argument("action", choices=sorted(ACTIONS))
    parser.add_argument("message", nargs="?", default="")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--run", default="latest")
    parser.add_argument("--target", default="current")
    parser.add_argument("--file", action="append", default=[])
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    run_dir = latest_run(repo, args.run)
    if args.action == "status":
        print_status(run_dir)
        return 0
    state = load_json(run_dir / "state.json")
    if str(state.get("phase", "")) in {"done", "aborted", "failed"}:
        raise RuntimeError(f"Le run {run_dir.name} est terminé ({state.get('phase')})")

    message = str(args.message).strip()
    if args.action in {"note", "review", "revise", "replan"} and not message:
        raise RuntimeError(f"Un message est obligatoire pour {args.action}")
    if len(message) > 4000:
        raise RuntimeError("Message trop long: maximum 4000 caractères")

    payload = {
        "id": uuid.uuid4().hex,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "action": args.action,
        "target": str(args.target).strip() or "current",
        "message": message,
        "context_files": validate_context_files(repo, run_dir, list(args.file)),
        "author": os.environ.get("USERNAME") or os.environ.get("USER") or "human",
    }
    inbox = run_dir / "control" / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    final_path = inbox / f"{stamp}-{payload['id']}.json"
    temp_path = final_path.with_suffix(".tmp")
    temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp_path.replace(final_path)
    print(f"Intervention envoyée: {args.action}")
    print(f"Run: {run_dir.name}")
    print(f"Cible: {payload['target']}")
    if payload["context_files"]:
        print("Contexte: " + ", ".join(payload["context_files"]))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        raise SystemExit(2)
