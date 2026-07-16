#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from resilience import command_has_complex_shell, command_is_dangerous, stable_id


def emit(decision: str, reason: str, updated: dict[str, Any] | None = None) -> None:
    specific: dict[str, Any] = {
        "hookEventName": "PreToolUse",
        "permissionDecision": decision,
        "permissionDecisionReason": reason,
    }
    if updated is not None:
        specific["updatedInput"] = updated
    print(json.dumps({"hookSpecificOutput": specific}, ensure_ascii=False))


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def latest_state(cwd: Path, cfg: dict[str, Any]) -> tuple[Path | None, dict[str, Any]]:
    parent = str(cfg.get("context_parent", ".agent-context"))
    states = sorted((cwd / parent).glob("*/state.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    if not states:
        return None, {}
    return states[0], load_json(states[0], {})


def task_commands(cwd: Path, state: dict[str, Any]) -> set[str]:
    task_file = str(state.get("task_file", "")).strip()
    if not task_file:
        return set()
    path = cwd / task_file
    try:
        text = path.read_text(encoding="utf-8")
        match = re.search(r"<!--\s*AGENT_TASK_META\s*(\{.*?\})\s*AGENT_TASK_META\s*-->", text, re.DOTALL)
        if not match:
            return set()
        meta = json.loads(match.group(1))
        return {
            str(value).strip()
            for value in [*meta.get("validation_commands", []), *meta.get("full_suite_commands", [])]
            if str(value).strip()
        }
    except Exception:
        return set()


def control_root(state: dict[str, Any]) -> Path | None:
    raw = str(state.get("controller_reports", "")).strip()
    return Path(raw) / "control" if raw else None


def approval_mode(root: Path | None, cfg: dict[str, Any]) -> str:
    configured = str(cfg.get("human_approval_mode", "commands")).lower()
    if root is None:
        return configured
    settings = load_json(root / "settings.json", {})
    return str(settings.get("approval_mode", configured)).lower()


def request_approval(
    root: Path,
    state: dict[str, Any],
    kind: str,
    payload: dict[str, Any],
    timeout: float,
) -> tuple[bool, str]:
    target = str(state.get("active_microtask") or state.get("active_scope") or "global")
    request = {
        "kind": kind,
        "target": target,
        "operation": str(state.get("active_operation") or "tool"),
        "payload": payload,
    }
    request_id = stable_id("A", request)
    request["id"] = request_id
    request["created_at"] = datetime.now().isoformat(timespec="seconds")
    pending = root / "approvals" / "pending"
    decisions = root / "approvals" / "decisions"
    pending.mkdir(parents=True, exist_ok=True)
    decisions.mkdir(parents=True, exist_ok=True)
    request_path = pending / f"{request_id}.json"
    decision_path = decisions / f"{request_id}.json"
    if not request_path.exists():
        temp = request_path.with_suffix(".tmp")
        temp.write_text(json.dumps(request, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(request_path)

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if decision_path.exists():
            decision = load_json(decision_path, {})
            approved = str(decision.get("decision", "")).lower() == "approve"
            reason = str(decision.get("message", "")).strip()
            try:
                request_path.unlink(missing_ok=True)
                if str(decision.get("scope", "once")) == "once":
                    decision_path.unlink(missing_ok=True)
            except OSError:
                pass
            return approved, reason
        time.sleep(0.25)
    return False, f"Autorisation expirée pour {request_id}"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        print(f"tool_guard: JSON invalide: {exc}", file=sys.stderr)
        return 1

    cwd = Path(payload.get("cwd") or os.getcwd()).resolve()
    cfg = load_json(cwd / ".microagent" / "config.json", {})
    guard = cfg.get("context_guard", {})
    tool = str(payload.get("tool_name", ""))
    original = payload.get("tool_input") or {}
    if not isinstance(original, dict):
        emit("allow", "entrée non structurée")
        return 0
    updated = dict(original)

    state_path, state = latest_state(cwd, cfg)
    root = control_root(state)
    mode = approval_mode(root, cfg)
    timeout = float(cfg.get("approval_timeout_seconds", 900))

    max_lines = int(guard.get("max_lines_per_read", 180))
    max_matches = int(guard.get("max_grep_matches", 30))
    max_new_file_chars = int(guard.get("max_new_file_chars", 30000))
    max_edit_chars = int(guard.get("max_edit_chars", 16000))

    if tool == "read_file":
        path = str(updated.get("file_path", ""))
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
            emit("allow", f"lecture bornée à {updated['limit']} lignes", updated)
            return 0

    if tool == "grep_search":
        try:
            limit = int(updated.get("limit", max_matches))
        except (TypeError, ValueError):
            limit = max_matches
        updated["limit"] = min(max(1, limit), max_matches)
        emit("allow", f"recherche bornée à {updated['limit']} résultats", updated)
        return 0

    if tool == "glob" and bool(guard.get("deny_broad_globs", True)):
        pattern = str(updated.get("pattern", "")).strip().replace("\\", "/")
        path = str(updated.get("path", ".")).strip().replace("\\", "/")
        if pattern in {"*", "**", "**/*", "./**/*"} and path in {"", ".", "./", str(cwd).replace("\\", "/")}:
            emit("deny", "Glob racine trop large; utilise LSP, grep ciblé ou un dossier précis.")
            return 0

    if tool in {"read_many_files", "read_multiple_files"}:
        emit("deny", "Lecture massive interdite; localise les symboles puis lis de petites plages.")
        return 0

    approval_needed = False
    approval_kind = tool

    if tool == "write_file":
        raw_path = str(updated.get("file_path", "")).strip()
        target = Path(raw_path)
        if not target.is_absolute():
            target = cwd / target
        if target.exists():
            emit("deny", "Écrasement complet interdit; utilise edit avec un bloc unique.")
            return 0
        content = str(updated.get("content", ""))
        if len(content) > max_new_file_chars:
            emit("deny", "Nouveau fichier trop volumineux; découpe la micro-tâche.")
            return 0
        approval_needed = mode == "all"

    elif tool == "edit":
        raw_path = str(updated.get("file_path", "")).strip()
        target = Path(raw_path)
        if not target.is_absolute():
            target = cwd / target
        if not target.exists():
            emit("deny", "Fichier absent; utilise write_file pour une création.")
            return 0
        old_string = str(updated.get("old_string", ""))
        new_string = str(updated.get("new_string", ""))
        if not old_string:
            emit("deny", "old_string vide interdit; relis la zone et cible un bloc unique.")
            return 0
        if bool(guard.get("deny_replace_all", True)) and bool(updated.get("replace_all", False)):
            emit("deny", "replace_all interdit dans une micro-tâche.")
            return 0
        if len(old_string) + len(new_string) > max_edit_chars:
            emit("deny", "Édition trop volumineuse; cible un symbole ou un petit bloc.")
            return 0
        approval_needed = mode == "all"

    elif tool == "run_shell_command":
        command = str(updated.get("command", "")).strip()
        if bool(updated.get("is_background", False)):
            emit("deny", "Processus en arrière-plan interdit.")
            return 0
        if command_is_dangerous(command):
            emit("deny", "Commande dangereuse interdite même avec autorisation humaine.")
            return 0
        lower = command.lower()
        if re.match(r"^\s*(cat|type|more|rg|grep|git\s+grep)\b", lower) or "get-content" in lower:
            emit("deny", "Lecture shell refusée; utilise LSP, grep_search et read_file borné.")
            return 0
        if command_has_complex_shell(command) and command not in task_commands(cwd, state):
            emit("deny", "Chaîne shell complexe absente du contrat TASK.md.")
            return 0
        exact = task_commands(cwd, state)
        prefixes = [str(value).strip() for value in cfg.get("allowed_command_prefixes", []) if str(value).strip()]
        known_safe = command in exact or any(command == prefix or command.startswith(prefix + " ") for prefix in prefixes)
        if mode == "all":
            approval_needed = True
        elif mode == "commands":
            approval_needed = command not in exact
        else:  # auto
            approval_needed = not known_safe
        approval_kind = "shell"

    if approval_needed:
        if root is None or state_path is None:
            emit("deny", "Autorisation humaine requise mais aucun run actif n'est détecté.")
            return 0
        settings = load_json(root / "settings.json", {})
        grants = settings.get("grants", [])
        if any(grant.get("kind") == approval_kind and grant.get("payload") == updated for grant in grants):
            emit("allow", "Opération déjà approuvée pour ce run", updated)
            return 0
        approved, message = request_approval(root, state, approval_kind, updated, timeout)
        if not approved:
            emit("deny", message or "Opération refusée par l'humain.")
            return 0
        emit("allow", "Opération approuvée par l'humain", updated)
        return 0

    emit("allow", "Opération autorisée par la politique LocalCode", updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
