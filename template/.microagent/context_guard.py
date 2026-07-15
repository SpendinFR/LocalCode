#!/usr/bin/env python3
"""Qwen Code PreToolUse hook: keeps local-model context narrow and targeted."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any


def load_cfg(cwd: Path) -> dict[str, Any]:
    path = cwd / ".microagent" / "config.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def output(decision: str, reason: str, updated: dict[str, Any] | None = None) -> None:
    specific: dict[str, Any] = {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }
    if updated is not None:
        specific["updatedInput"] = updated
    print(json.dumps({"hookSpecificOutput": specific}, ensure_ascii=False))




def active_task_commands(cwd: Path, full_cfg: dict[str, Any]) -> set[str]:
    commands: set[str] = set()
    context_parent = str(full_cfg.get("context_parent", ".agent-context"))
    states = sorted((cwd / context_parent).glob("*/state.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not states:
        return commands
    try:
        state = json.loads(states[0].read_text(encoding="utf-8"))
        task_file = str(state.get("task_file", ""))
        task_path = cwd / task_file
        text = task_path.read_text(encoding="utf-8")
        match = re.search(r"<!--\s*AGENT_TASK_META\s*(\{.*?\})\s*AGENT_TASK_META\s*-->", text, re.DOTALL)
        if match:
            meta = json.loads(match.group(1))
            commands.update(str(value).strip() for value in meta.get("validation_commands", []) if str(value).strip())
            commands.update(str(value).strip() for value in meta.get("full_suite_commands", []) if str(value).strip())
    except Exception:
        return commands
    return commands

def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        print(f"context_guard: invalid JSON: {exc}", file=sys.stderr)
        return 1

    cwd = Path(payload.get("cwd") or os.getcwd()).resolve()
    full_cfg = load_cfg(cwd)
    cfg = full_cfg.get("context_guard", {})
    max_lines = int(cfg.get("max_lines_per_read", 240))
    max_matches = int(cfg.get("max_grep_matches", 40))
    deny_broad = bool(cfg.get("deny_broad_globs", True))
    max_new_file_chars = int(cfg.get("max_new_file_chars", 30000))
    max_edit_chars = int(cfg.get("max_edit_chars", 16000))
    deny_replace_all = bool(cfg.get("deny_replace_all", True))
    tool = str(payload.get("tool_name", ""))
    original = payload.get("tool_input") or {}
    if not isinstance(original, dict):
        output("allow", "input non structuré")
        return 0
    updated = dict(original)

    if tool == "read_file":
        path = str(updated.get("file_path", ""))
        # Media/PDF reads use pages and should not receive text offsets.
        if "pages" not in updated and not path.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".pdf", ".mp3", ".wav", ".mp4")):
            try:
                limit = int(updated.get("limit", max_lines))
            except (TypeError, ValueError):
                limit = max_lines
            updated["limit"] = min(max(1, limit), max_lines)
            if "offset" in updated:
                try:
                    updated["offset"] = max(0, int(updated["offset"]))
                except (TypeError, ValueError):
                    updated["offset"] = 0
            output("allow", f"lecture bornée à {updated['limit']} lignes", updated)
            return 0

    if tool == "grep_search":
        try:
            limit = int(updated.get("limit", max_matches))
        except (TypeError, ValueError):
            limit = max_matches
        updated["limit"] = min(max(1, limit), max_matches)
        output("allow", f"grep borné à {updated['limit']} résultats", updated)
        return 0

    if tool == "glob" and deny_broad:
        pattern = str(updated.get("pattern", "")).strip().replace("\\", "/")
        path = str(updated.get("path", ".")).strip().replace("\\", "/")
        if pattern in {"*", "**", "**/*", "./**/*"} and path in {"", ".", "./", str(cwd).replace("\\", "/")}:
            output("deny", "Glob trop large. Utilise le repo map, grep_search ou un glob ciblé par dossier/extension.")
            return 0

    if tool in {"read_many_files", "read_multiple_files"}:
        output("deny", "Lecture massive interdite. Cherche d'abord les symboles puis lis des plages ciblées.")
        return 0


    if tool == "write_file":
        raw_path = str(updated.get("file_path", "")).strip()
        target = Path(raw_path)
        if not target.is_absolute():
            target = cwd / target
        if target.exists():
            output(
                "deny",
                "Écrasement complet d'un fichier existant interdit. Lis la zone cible puis utilise edit avec un old_string unique.",
            )
            return 0
        content = str(updated.get("content", ""))
        if len(content) > max_new_file_chars:
            output("deny", f"Nouveau fichier trop volumineux ({len(content)} caractères). Découpe la micro-tâche.")
            return 0
        output("allow", "création d'un nouveau fichier autorisée")
        return 0

    if tool == "edit":
        raw_path = str(updated.get("file_path", "")).strip()
        target = Path(raw_path)
        if not target.is_absolute():
            target = cwd / target
        if not target.exists():
            output("deny", "Fichier absent. Utilise write_file uniquement pour créer un nouveau fichier.")
            return 0
        old_string = str(updated.get("old_string", ""))
        new_string = str(updated.get("new_string", ""))
        if not old_string:
            output("deny", "old_string vide interdit sur un fichier existant. Relis la zone et cible un bloc unique.")
            return 0
        if deny_replace_all and bool(updated.get("replace_all", False)):
            output("deny", "replace_all interdit dans une micro-tâche. Applique des edits ciblés et vérifiables.")
            return 0
        if len(old_string) + len(new_string) > max_edit_chars:
            output("deny", "Patch trop volumineux. Réduis l'édition au symbole ou bloc directement concerné.")
            return 0
        output("allow", "edit ciblé autorisé; l'outil vérifiera l'unicité de old_string")
        return 0

    if tool == "run_shell_command":
        command = str(updated.get("command", "")).strip()
        lower = command.lower()
        if bool(updated.get("is_background", False)):
            output("deny", "Les processus en arrière-plan sont interdits dans une micro-tâche autonome.")
            return 0

        # Repository exploration must go through bounded Qwen tools so outputs cannot
        # silently flood the small model's context. Tests/build commands remain allowed.
        direct_file_read = re.match(r"^\s*(cat|type|more)\s+", lower)
        powershell_file_read = "get-content" in lower
        shell_search = re.match(r"^\s*(rg|grep|git\s+grep)\b", lower)
        broad_listing = any(pattern in lower for pattern in (
            "rg --files", "find . -type f", "find ./ -type f", "ls -r", "ls -R",
            "dir /s", "tree /f", "get-childitem -recurse", "gci -recurse",
        ))
        bulk_concat = any(pattern in lower for pattern in (
            "xargs cat", "-exec cat", "foreach-object { get-content", "| get-content",
        ))
        if direct_file_read or powershell_file_read or shell_search or broad_listing or bulk_concat:
            output(
                "deny",
                "Lecture/recherche shell refusée pour protéger le contexte. Utilise grep_search, glob puis read_file avec offset/limit.",
            )
            return 0

        exact_commands = active_task_commands(cwd, full_cfg)
        prefixes = [str(value).strip() for value in full_cfg.get("allowed_command_prefixes", []) if str(value).strip()]
        allowed = command in exact_commands or any(
            command == prefix or command.startswith(prefix + " ") for prefix in prefixes
        )
        if not allowed:
            output(
                "deny",
                "Commande shell hors contrat. Utilise une commande de validation déclarée dans TASK.md ou ajoute un préfixe sûr à .microagent/config.json.",
            )
            return 0
        output("allow", "commande de test/build autorisée")
        return 0

    output("allow", "autorisé")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
