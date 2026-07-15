#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Vérifie les prérequis du micro-agent local")
    ap.add_argument("task")
    ap.add_argument("--repo", default=".")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    task = Path(args.task)
    task = task if task.is_absolute() else repo / task
    errors: list[str] = []

    for binary in ("git", "qwen"):
        if not shutil.which(binary):
            errors.append(f"{binary} introuvable dans PATH")
    if not task.exists():
        errors.append(f"mission introuvable: {task}")
    cfg_path = repo / ".microagent" / "config.json"
    if not cfg_path.exists():
        errors.append(".microagent/config.json absent: lance d'abord l'installateur")
        cfg = {}
    else:
        try:
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append(f"config invalide: {exc}")
            cfg = {}

    if shutil.which("git"):
        p = run(["git", "rev-parse", "--is-inside-work-tree"], repo)
        if p.returncode != 0 or p.stdout.strip() != "true":
            errors.append(f"{repo} n'est pas un dépôt Git")

    if shutil.which("qwen"):
        p = run(["qwen", "--help"], repo)
        text = (p.stdout or "") + (p.stderr or "")
        for flag in ("--json-schema", "--max-tool-calls", "--max-wall-time", "--approval-mode"):
            if flag not in text:
                errors.append(f"Qwen Code trop ancien: option absente {flag}")

    skip_ollama = os.environ.get("MICROAGENT_SKIP_OLLAMA_CHECK") == "1"
    if not skip_ollama:
        if not shutil.which("ollama"):
            errors.append("ollama introuvable (ou définis MICROAGENT_SKIP_OLLAMA_CHECK=1 pour un autre serveur)")
        else:
            p = run(["ollama", "list"], repo)
            if p.returncode != 0:
                errors.append("Ollama ne répond pas: démarre le service")
            else:
                available = {
                    line.split()[0] for line in p.stdout.splitlines()[1:] if line.strip()
                }
                models = {
                    str(cfg.get(key, ""))
                    for key in (
                        "planner_model", "scout_model", "coder_model",
                        "architecture_reviewer_model", "execution_reviewer_model", "judge_model"
                    )
                    if cfg.get(key)
                }
                missing = [
                    m for m in sorted(models)
                    if m not in available and f"{m}:latest" not in available
                ]
                if missing:
                    errors.append("modèles Ollama absents: " + ", ".join(missing))

    if errors:
        print("PRÉREQUIS NON SATISFAITS", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2
    print("Préflight OK — lancement autonome jusqu'au commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
