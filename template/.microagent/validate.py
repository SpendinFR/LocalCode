from __future__ import annotations

import argparse
import fnmatch
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from core import run_command
from taskmeta import TaskMetaError, load_task


def git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
    return proc.stdout


def matches(path: str, patterns: list[str]) -> bool:
    normalized = path.replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, pattern) for pattern in patterns)


def changed_files(repo: Path, base: str) -> list[str]:
    blobs = [
        git(repo, "diff", "--name-only", f"{base}...HEAD"),
        git(repo, "diff", "--name-only"),
        git(repo, "diff", "--cached", "--name-only"),
        git(repo, "ls-files", "--others", "--exclude-standard"),
    ]
    return sorted({line.strip() for blob in blobs for line in blob.splitlines() if line.strip()})


def validate(repo: Path, task: Path, base: str, include_docs: bool = True, run_full: bool = True):
    _, meta = load_task(task)
    checks: list[dict] = []
    changed = changed_files(repo, base)
    checks.append({"name": "diff_non_vide", "ok": bool(changed), "detail": changed})

    forbidden = [path for path in changed if matches(path, meta["forbidden_paths"])]
    checks.append({"name": "aucun_chemin_interdit", "ok": not forbidden, "detail": forbidden})

    if meta["require_test_changes"]:
        tests = [path for path in changed if matches(path, meta["test_file_globs"])]
        checks.append({"name": "tests_modifies", "ok": bool(tests), "detail": tests})

    if include_docs:
        for item in meta.get("documentation_updates", []):
            rel = str(item["path"])
            path = repo / rel
            exists = path.exists()
            text = path.read_text(encoding="utf-8", errors="replace") if exists else ""
            missing = [marker for marker in item.get("required_markers", []) if marker not in text]
            changed_ok = (not item.get("must_change", True)) or rel in changed
            ok = exists and not missing and changed_ok
            checks.append(
                {
                    "name": f"documentation:{rel}",
                    "ok": ok,
                    "detail": {
                        "exists": exists,
                        "missing_markers": missing,
                        "must_change": item.get("must_change", True),
                        "changed": rel in changed,
                    },
                }
            )

    commands = list(meta["validation_commands"])
    if run_full:
        commands += list(meta["full_suite_commands"])
    for command in commands:
        result = run_command(str(command), repo, int(meta["command_timeout_seconds"]))
        checks.append(
            {"name": f"command:{command}", "ok": result.ok, "detail": asdict(result)}
        )

    return {
        "ok": all(check["ok"] for check in checks),
        "task_id": meta["task_id"],
        "changed_files": changed,
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    parser.add_argument("--task", required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--no-docs", action="store_true")
    parser.add_argument("--targeted-only", action="store_true")
    parser.add_argument("--json-out")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    task = Path(args.task)
    task = task if task.is_absolute() else repo / task
    try:
        payload = validate(repo, task, args.base, not args.no_docs, not args.targeted_only)
    except (TaskMetaError, RuntimeError, OSError) as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        return 2
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        Path(args.json_out).write_text(text + "\n", encoding="utf-8")
    return 0 if payload["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
