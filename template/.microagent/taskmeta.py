from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

META_RE = re.compile(r"<!--\s*AGENT_TASK_META\s*(\{.*?\})\s*AGENT_TASK_META\s*-->", re.DOTALL)


class TaskMetaError(ValueError):
    pass


def _safe_rel_path(raw: Any, field: str) -> str:
    value = str(raw or "").strip().replace("\\", "/")
    path = Path(value)
    if not value or path.is_absolute() or ".." in path.parts:
        raise TaskMetaError(f"{field}: chemin relatif sûr requis, reçu {raw!r}")
    return value


def load_task(path: Path) -> tuple[str, dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    match = META_RE.search(text)
    if not match:
        raise TaskMetaError(f"Bloc AGENT_TASK_META absent dans {path}")
    try:
        meta = json.loads(match.group(1))
    except json.JSONDecodeError as exc:
        raise TaskMetaError(f"JSON AGENT_TASK_META invalide: {exc}") from exc

    for key in ("task_id", "validation_commands", "full_suite_commands"):
        if key not in meta:
            raise TaskMetaError(f"Champ obligatoire absent: {key}")

    defaults = {
        "context_files": [],
        "documentation_updates": [],
        "require_test_changes": True,
        "test_file_globs": [
            "tests/**",
            "test/**",
            "**/tests/**",
            "**/*.test.*",
            "**/*.spec.*",
            "**/test_*.py",
            "**/*_test.py",
        ],
        "forbidden_paths": [
            ".env",
            ".env.*",
            "**/secrets/**",
            "**/*.pem",
            "**/*.key",
            ".microagent/**",
            ".qwen/**",
        ],
        "command_timeout_seconds": 1800,
        "commit_message": None,
    }
    for key, value in defaults.items():
        meta.setdefault(key, value)

    list_fields = (
        "validation_commands",
        "full_suite_commands",
        "forbidden_paths",
        "test_file_globs",
        "context_files",
        "documentation_updates",
    )
    for key in list_fields:
        if not isinstance(meta[key], list):
            raise TaskMetaError(f"{key} doit être une liste")

    placeholders = [
        command
        for command in [*meta["validation_commands"], *meta["full_suite_commands"]]
        if "REMPLACER_" in str(command)
    ]
    if placeholders:
        raise TaskMetaError("Des placeholders restent dans les commandes")

    meta["context_files"] = [
        _safe_rel_path(value, "context_files") for value in meta["context_files"]
    ]

    normalized_updates: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for index, raw in enumerate(meta["documentation_updates"]):
        if not isinstance(raw, dict):
            raise TaskMetaError(f"documentation_updates[{index}] doit être un objet")
        doc_path = _safe_rel_path(raw.get("path"), f"documentation_updates[{index}].path")
        if doc_path in seen_paths:
            raise TaskMetaError(f"documentation_updates: chemin dupliqué {doc_path}")
        seen_paths.add(doc_path)
        instruction = str(raw.get("instruction", "")).strip()
        if not instruction:
            raise TaskMetaError(f"documentation_updates[{index}].instruction est vide")
        markers = raw.get("required_markers", [])
        if not isinstance(markers, list) or any(not str(item).strip() for item in markers):
            raise TaskMetaError(
                f"documentation_updates[{index}].required_markers doit être une liste de chaînes non vides"
            )
        normalized_updates.append(
            {
                "path": doc_path,
                "instruction": instruction,
                "required_markers": [str(item) for item in markers],
                "must_change": bool(raw.get("must_change", True)),
                "allow_create": bool(raw.get("allow_create", False)),
            }
        )
    meta["documentation_updates"] = normalized_updates

    return text, meta
