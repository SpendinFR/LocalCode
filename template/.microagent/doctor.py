#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path


def resolve_cli(name: str) -> list[str] | None:
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
    return [*prefix, *arguments]


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

    git_executable = shutil.which("git")
    qwen_prefix = resolve_cli("qwen")
    if not git_executable:
        errors.append("git introuvable dans PATH")
    if not qwen_prefix:
        errors.append("qwen introuvable dans PATH")
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

    if git_executable:
        proc = run([git_executable, "rev-parse", "--is-inside-work-tree"], repo)
        if proc.returncode != 0 or proc.stdout.strip() != "true":
            errors.append(f"{repo} n'est pas un dépôt Git")

    if qwen_prefix:
        proc = run(cli_command(qwen_prefix, ["--help"]), repo)
        text = (proc.stdout or "") + (proc.stderr or "")
        for flag in (
            "--json-schema", "--max-tool-calls", "--max-wall-time",
            "--approval-mode", "--experimental-lsp",
        ):
            if flag not in text:
                errors.append(f"Qwen Code trop ancien: option absente {flag}")

    skip_router = (
        os.environ.get("MICROAGENT_SKIP_ROUTER_CHECK") == "1"
        or os.environ.get("MICROAGENT_SKIP_OLLAMA_CHECK") == "1"
    )
    if not skip_router:
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
