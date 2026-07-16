#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any

DANGEROUS_PARTS = (
    "rm -rf",
    "git reset --hard",
    "git clean -",
    "git push",
    "git merge",
    "git rebase",
    "git cherry-pick",
    "git worktree",
    "format ",
    "diskpart",
    "del /s",
    "remove-item -recurse",
    "shutdown",
    "reboot",
)
DYNAMIC_METACHARACTERS = ("&&", "||", ";", "|", ">", "<", "`", "$(")


def estimate_tokens(text: str) -> int:
    """Conservative estimate for mixed French, English and source code."""
    return max(1, math.ceil(len(text) / 3.4))


def extract_json(text: str) -> dict[str, Any] | list[Any] | None:
    """Recover one JSON value from fenced, noisy or slightly truncated output."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    starts = [index for index in (raw.find("{"), raw.find("[")) if index >= 0]
    if not starts:
        return None
    start = min(starts)
    candidate = raw[start:]

    in_string = False
    escaped = False
    stack: list[str] = []
    end = None
    pairs = {"}": "{", "]": "["}
    for offset, char in enumerate(candidate):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "{[":
            stack.append(char)
        elif char in "}]":
            if stack and stack[-1] == pairs[char]:
                stack.pop()
                if not stack:
                    end = offset + 1
                    break
            else:
                return None
    if end is not None:
        try:
            return json.loads(candidate[:end])
        except json.JSONDecodeError:
            return None

    # Safe repair only for an output cut at the end: close open string and containers.
    repaired = candidate.rstrip()
    if in_string:
        repaired += '"'
    for opener in reversed(stack):
        repaired += "}" if opener == "{" else "]"
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None


def compact_prompt(text: str, max_tokens: int) -> tuple[str, bool]:
    """Deterministically compact while preserving contracts, paths and the latest state."""
    if estimate_tokens(text) <= max_tokens:
        return text, False
    max_chars = max(1800, int(max_tokens * 3.2))
    lines = text.splitlines()
    priority_terms = (
        "rôle:", "role:", "source de vérité", "mission", "objectif", "goal", "invariant",
        "accept", "intervention", "finding", "test", "commande", "forbidden", "interdit",
        "checkpoint", "diff", "preuve", "evidence", "unknown", "bloqué", "blocked",
        ".json", ".patch", ".md", "`",
    )
    priority: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = line.strip().lower()
        if normalized and any(term in normalized for term in priority_terms):
            if line not in seen:
                priority.append(line)
                seen.add(line)

    head = lines[: min(28, len(lines))]
    tail = lines[max(0, len(lines) - 38):]
    selected = head + ["", "[CONTEXTE COMPACTÉ — faits et références prioritaires]", ""] + priority + ["", "[ÉTAT LE PLUS RÉCENT]", ""] + tail
    result = "\n".join(selected)
    if len(result) > max_chars:
        head_chars = max_chars // 3
        tail_chars = max_chars - head_chars - 120
        result = result[:head_chars] + "\n\n[... contexte intermédiaire archivé sur disque ...]\n\n" + result[-tail_chars:]
    return result, True


def classify_qwen_failure(returncode: int, stdout: str, stderr: str) -> str:
    blob = (stdout + "\n" + stderr).lower()
    if returncode == 124 or "timeout" in blob or "timed out" in blob:
        return "TIMEOUT"
    if any(value in blob for value in ("connection refused", "failed to connect", "econnrefused", "server error", "502", "503", "504")):
        return "SERVER"
    if any(value in blob for value in ("context length", "context window", "too many tokens", "prompt is too long", "kv cache")):
        return "CONTEXT"
    if any(value in blob for value in ("out of memory", "cuda error", "vulkan error", "ggml_abort", "bad_alloc")):
        return "MEMORY"
    return "PROCESS"


def command_is_dangerous(command: str) -> bool:
    lower = command.strip().lower()
    return not lower or "\n" in lower or "\r" in lower or any(part in lower for part in DANGEROUS_PARTS)


def command_has_complex_shell(command: str) -> bool:
    return any(token in command for token in DYNAMIC_METACHARACTERS)


def sampled_file_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        digest.update(handle.read(1024 * 1024))
        if stat.st_size > 1024 * 1024:
            handle.seek(max(0, stat.st_size - 1024 * 1024))
            digest.update(handle.read(1024 * 1024))
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sample_sha256": digest.hexdigest(),
    }


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def stable_id(prefix: str, payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return f"{prefix}-{hashlib.sha256(raw).hexdigest()[:10]}"


def payload_matches_schema(payload: Any, schema_path: Path) -> bool:
    """Validate with jsonschema when available, with a strict required-key fallback."""
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    try:
        import jsonschema  # type: ignore

        jsonschema.validate(payload, schema)
        return True
    except ImportError:
        pass
    except Exception:
        return False

    def check(value: Any, node: dict[str, Any]) -> bool:
        expected = node.get("type")
        if isinstance(expected, list):
            allowed = expected
        else:
            allowed = [expected] if expected else []
        matches_type = not allowed or any(
            (kind == "object" and isinstance(value, dict))
            or (kind == "array" and isinstance(value, list))
            or (kind == "string" and isinstance(value, str))
            or (kind == "integer" and isinstance(value, int) and not isinstance(value, bool))
            or (kind == "number" and isinstance(value, (int, float)) and not isinstance(value, bool))
            or (kind == "boolean" and isinstance(value, bool))
            or (kind == "null" and value is None)
            for kind in allowed
        )
        if not matches_type:
            return False
        if "enum" in node and value not in node["enum"]:
            return False
        if isinstance(value, dict):
            required = node.get("required", [])
            if any(key not in value for key in required):
                return False
            properties = node.get("properties", {})
            if node.get("additionalProperties") is False and any(key not in properties for key in value):
                return False
            return all(check(item, properties[key]) for key, item in value.items() if key in properties)
        if isinstance(value, list):
            if "maxItems" in node and len(value) > int(node["maxItems"]):
                return False
            item_schema = node.get("items")
            return not item_schema or all(check(item, item_schema) for item in value)
        return True

    return check(payload, schema)
