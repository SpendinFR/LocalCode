#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


def run(cmd: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)


def router_models() -> set[str]:
    request = urllib.request.Request(
        "http://127.0.0.1:8080/models?reload=1",
        headers={"Authorization": "Bearer local"},
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return {
        str(item.get("id", ""))
        for item in payload.get("data", [])
        if item.get("id")
    }


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
        proc = run(["git", "rev-parse", "--is-inside-work-tree"], repo)
        if proc.returncode != 0 or proc.stdout.strip() != "true":
            errors.append(f"{repo} n'est pas un dépôt Git")

    if shutil.which("qwen"):
        proc = run(["qwen", "--help"], repo)
        text = (proc.stdout or "") + (proc.stderr or "")
        for flag in (
            "--json-schema", "--max-tool-calls", "--max-wall-time",
            "--approval-mode", "--experimental-lsp",
        ):
            if flag not in text:
                errors.append(f"Qwen Code trop ancien: option absente {flag}")

    try:
        available = router_models()
        required = {
            str(cfg.get(key, ""))
            for key in (
                "planner_model", "scout_model", "coder_model",
                "architecture_reviewer_model", "execution_reviewer_model", "judge_model",
            )
            if cfg.get(key)
        }
        missing = sorted(required - available)
        if missing:
            errors.append("modèles absents du routeur llama.cpp: " + ", ".join(missing))
    except (OSError, ValueError, json.JSONDecodeError, urllib.error.URLError) as exc:
        errors.append(
            "routeur llama.cpp indisponible sur http://127.0.0.1:8080; "
            f"lance start-model-router.ps1 ({exc})"
        )

    if errors:
        print("PRÉREQUIS NON SATISFAITS", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 2
    print("Préflight OK — lancement autonome jusqu'au commit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
