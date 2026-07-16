#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import orchestrator as legacy
from core import CommandResult, classify_failure, cli_command, load_json, ready_tasks, resolve_cli, result_dict, save_json, shell_command, slug
from resilience import (
    classify_qwen_failure,
    command_has_complex_shell,
    command_is_dangerous,
    compact_prompt,
    estimate_tokens,
    extract_json,
    process_alive,
    payload_matches_schema,
    sampled_file_fingerprint,
    stable_id,
)
from taskmeta import TaskMetaError, load_task

def paint(text: str, code: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"

class ResilientOrchestrator(legacy.Orchestrator):
    """Additive safety/resilience layer over the tested V8 orchestration engine."""

    resume_mode = False

    def __init__(self, source_repo: Path, task_path: Path):
        super().__init__(source_repo, task_path)
        self._initialize_resilience()

    @classmethod
    def from_run(cls, source_repo: Path, requested: str) -> "ResilientOrchestrator":
        self = cls.__new__(cls)
        self.source = source_repo.resolve()
        self.cfg = load_json(self.source / ".microagent" / "config.json")
        runs = self.source / str(self.cfg.get("runs_parent", ".agent-runs"))
        if requested == "latest":
            candidates = sorted(
                (path for path in runs.iterdir() if path.is_dir() and (path / "state.json").exists()),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            ) if runs.exists() else []
            if not candidates:
                raise RuntimeError("Aucun run reprenable")
            self.controller_dir = candidates[0]
        else:
            candidate = Path(requested)
            self.controller_dir = (candidate if candidate.is_absolute() else runs / candidate).resolve()
        state_path = self.controller_dir / "state.json"
        if not state_path.exists():
            raise RuntimeError(f"state.json absent: {state_path}")
        self.state = load_json(state_path)
        if str(self.state.get("phase", "")) == "done":
            raise RuntimeError(f"Le run {self.controller_dir.name} est déjà terminé")
        self.worktree = Path(str(self.state.get("worktree", ""))).resolve()
        if not self.worktree.exists():
            raise RuntimeError(f"Worktree absent: {self.worktree}")
        self.context_dir = self.worktree / str(self.cfg.get("context_parent", ".agent-context")) / self.controller_dir.name
        self.task_rel = str(self.state.get("task_file", ""))
        self.task_source = self.worktree / self.task_rel
        if not self.task_source.exists():
            raise RuntimeError(f"Mission absente du worktree: {self.task_source}")
        self.task_text, self.meta = load_task(self.task_source)
        self.task_id = str(self.state.get("task_id") or self.meta["task_id"])
        self.base = str(self.state.get("base_sha"))
        self.branch = str(self.state.get("branch"))
        checkpoints = list(self.state.get("checkpoint_commits", []))
        self.checkpoint_sha = str(self.state.get("checkpoint_sha") or (checkpoints[-1] if checkpoints else self.base))
        self.current_model = None
        self.last_failure_path = None
        self.resume_mode = True
        self._initialize_resilience()
        return self

    def _initialize_resilience(self) -> None:
        self._active_proc: subprocess.Popen[str] | None = None
        self._inside_process = False
        self._lock_owned = False
        self._last_visible_operation = ""
        self._last_operation_model: str | None = None
        self._resume_evidence: dict[str, Path] = {}
        self.state.setdefault("checkpoint_sha", self.checkpoint_sha)
        self.state.setdefault("operations", [])
        self.state.setdefault("questions", [])
        self.state.setdefault("question_counts", {"total": 0, "by_target": {}})
        self.state.setdefault("runtime", {})
        control = self.state.setdefault("human_control", {})
        control.setdefault("interventions", [])
        control.setdefault("mandatory_reviews", [])
        control.setdefault("pending_actions", [])
        control.setdefault("processed_count", 0)
        control.setdefault("status", "running")

    def save_state(self) -> None:
        self.state["checkpoint_sha"] = self.checkpoint_sha
        runtime = self.state.setdefault("runtime", {})
        runtime["heartbeat_at"] = datetime.now().isoformat(timespec="seconds")
        runtime["pid"] = os.getpid()
        super().save_state()
        if self._lock_owned:
            lock = self.controller_dir / "run.lock"
            lock.write_text(
                json.dumps({"pid": os.getpid(), "heartbeat_at": runtime["heartbeat_at"]}, indent=2) + "\n",
                encoding="utf-8",
            )

    def acquire_lock(self) -> None:
        self.controller_dir.mkdir(parents=True, exist_ok=True)
        path = self.controller_dir / "run.lock"
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                pid = int(existing.get("pid", 0))
            except Exception:
                pid = 0
            if pid and pid != os.getpid() and process_alive(pid):
                raise RuntimeError(f"Run déjà piloté par le PID {pid}")
        path.write_text(json.dumps({"pid": os.getpid()}, indent=2) + "\n", encoding="utf-8")
        default_settings = self.source / str(self.cfg.get("runs_parent", ".agent-runs")) / "default-control-settings.json"
        run_settings = self.controller_dir / "control" / "settings.json"
        if default_settings.exists() and not run_settings.exists():
            run_settings.parent.mkdir(parents=True, exist_ok=True)
            run_settings.write_text(default_settings.read_text(encoding="utf-8"), encoding="utf-8")
        self._lock_owned = True

    def release_lock(self) -> None:
        if self._lock_owned:
            try:
                (self.controller_dir / "run.lock").unlink(missing_ok=True)
            finally:
                self._lock_owned = False

    def event(self, kind: str, **data: Any) -> None:
        super().event(kind, **data)
        if kind.startswith("resilience_") or kind in {
            "human_question", "human_answer", "human_note_verified", "approval_wait",
            "qwen_retry", "qwen_recovered", "context_compacted", "router_restarted",
            "source_advanced", "resume_started", "resume_partial_diff",
        }:
            details = " ".join(f"{key}={value}" for key, value in data.items())
            print(paint(f"[LocalCode] {kind}{(' ' + details) if details else ''}", "33"), flush=True)

    def set_active(self, scope: str, microtask: str | None = None, operation: str | None = None) -> None:
        super().set_active(scope, microtask, operation)
        visible = f"{scope}:{microtask or '-'}:{operation or '-'}"
        if visible != self._last_visible_operation:
            self._last_visible_operation = visible
            progress = ""
            statuses = self.state.get("microtask_status", {})
            if statuses:
                done = sum(1 for value in statuses.values() if value in {"done", "expanded"})
                progress = f" {done}/{len(statuses)}"
            print(paint(f"\n[{microtask or scope} · {operation or 'idle'}]{progress}", "36;1"), flush=True)

    def preflight(self) -> None:
        super().preflight()
        self._record_runtime_versions()

    def create_worktree(self) -> None:
        super().create_worktree()
        self._probe_models_if_needed()

    def context_manifest(self) -> dict[str, tuple[int, str]]:
        manifest = super().context_manifest()
        return {
            key: value for key, value in manifest.items()
            if not key.startswith(("recovery/", "compaction/", "approvals/"))
        }

    def _record_runtime_versions(self) -> None:
        def version(command: list[str]) -> str:
            try:
                proc = subprocess.run(command, cwd=self.source, text=True, capture_output=True, timeout=20)
                return ((proc.stdout or "") + " " + (proc.stderr or "")).strip()[:1000]
            except Exception as exc:
                return f"unavailable: {exc}"

        runtime = self.state.setdefault("runtime", {})
        runtime["environment"] = {
            "platform": platform.platform(),
            "python": sys.version,
            "git": version(["git", "--version"]),
            "qwen": version(cli_command(self.qwen_prefix or resolve_cli("qwen") or ["qwen"], ["--version"])),
            "llama_server": version(["llama-server", "--version"]) if shutil.which("llama-server") else "not in PATH",
            "kit_commit": version(["git", "rev-parse", "HEAD"]),
        }
        runtime_path = self.source / ".microagent" / "model-runtime.json"
        if runtime_path.exists():
            model_runtime = load_json(runtime_path)
            models: dict[str, Any] = {}
            for alias, item in model_runtime.get("models", {}).items():
                path = Path(str(item.get("path", "")))
                models[alias] = sampled_file_fingerprint(path) if path.is_file() else {"path": str(path), "missing": True}
            runtime["models"] = models
        self.save_state()

    def _probe_models_if_needed(self) -> None:
        if not self.cfg.get("model_capability_probe", True):
            return
        runtime_path = self.source / ".microagent" / "model-runtime.json"
        if not runtime_path.exists():
            return
        runtime = load_json(runtime_path)
        fingerprint_payload = {
            alias: sampled_file_fingerprint(Path(str(item["path"])))
            for alias, item in runtime.get("models", {}).items()
            if Path(str(item.get("path", ""))).is_file()
        }
        fingerprint_payload["_config_sha256"] = hashlib.sha256(
            (self.source / ".microagent" / "config.json").read_bytes()
        ).hexdigest()
        fingerprint_payload["_settings_sha256"] = hashlib.sha256(
            (self.source / ".qwen" / "settings.json").read_bytes()
        ).hexdigest()
        fingerprint_payload["_qwen_version"] = str(
            self.state.get("runtime", {}).get("environment", {}).get("qwen", "unknown")
        )
        fingerprint = hashlib.sha256(json.dumps(fingerprint_payload, sort_keys=True).encode()).hexdigest()
        cache_path = self.source / str(self.cfg.get("runs_parent", ".agent-runs")) / "model-capabilities.json"
        cache = load_json(cache_path) if cache_path.exists() else {}
        if cache.get("fingerprint") == fingerprint and cache.get("ok") is True:
            return
        results: dict[str, Any] = {}
        schema = legacy.SCHEMAS / "health.schema.json"
        aliases = list(dict.fromkeys([
            self.cfg["planner_model"], self.cfg["coder_model"], self.cfg["architecture_reviewer_model"]
        ]))
        for alias in aliases:
            changed_before_probe = set(self.changed_files(self.base))
            is_coder = alias == self.cfg["coder_model"]
            probe_file = self.context_dir / "capability" / "edit-probe.txt"
            probe_file.parent.mkdir(parents=True, exist_ok=True)
            if is_coder:
                probe_file.write_text("PROBE=0\n", encoding="utf-8")
                probe_prompt = (
                    f"Teste les outils sans lancer de shell. Lis `{self.rel(probe_file)}`, utilise edit "
                    "pour remplacer exactement PROBE=0 par PROBE=1, puis réponds avec ok=true et ton identifiant. "
                    "Ne modifie aucun autre fichier."
                )
                excluded = "run_shell_command,agent"
            else:
                probe_prompt = (
                    "Teste la lecture ciblée et le LSP: localise la classe Orchestrator dans "
                    "`.microagent/orchestrator.py`, lis une courte plage, puis réponds avec ok=true et ton identifiant. "
                    "N'écris rien et ne lance aucune commande."
                )
                excluded = "run_shell_command,write_file,edit,agent"
            print(paint(f"[capacité] Chargement, JSON et outils: {alias}", "34;1"), flush=True)
            command = cli_command(
                self.qwen_prefix or resolve_cli("qwen") or ["qwen"],
                [
                    *(["--experimental-lsp"] if self.cfg.get("enable_lsp", True) else []),
                    "-p", probe_prompt,
                    "--model", alias, "--approval-mode", "auto" if is_coder else "plan",
                    "--max-session-turns", "8", "--max-tool-calls", "12",
                    "--max-wall-time", "8m", "--json-schema", f"@{schema.resolve()}",
                    "--output-format", "text", "--exclude-tools", excluded,
                ],
            )
            started = time.monotonic()
            proc = subprocess.run(command, cwd=self.worktree, text=True, capture_output=True, timeout=600)
            duration = time.monotonic() - started
            payload = extract_json(proc.stdout or "")
            edit_ok = not is_coder or (probe_file.exists() and "PROBE=1" in probe_file.read_text(encoding="utf-8"))
            probe_file.unlink(missing_ok=True)
            unexpected_changes = sorted(set(self.changed_files(self.base)) - changed_before_probe)
            results[alias] = {
                "returncode": proc.returncode, "payload": payload, "edit_ok": edit_ok,
                "unexpected_changes": unexpected_changes,
                "duration_s": round(duration, 3), "stderr": (proc.stderr or "")[-2000:],
            }
            if unexpected_changes:
                self.reset_to_checkpoint()
            if (
                proc.returncode != 0
                or not isinstance(payload, dict)
                or not payload_matches_schema(payload, schema)
                or payload.get("ok") is not True
                or not edit_ok
                or bool(unexpected_changes)
            ):
                save_json(cache_path, {"fingerprint": fingerprint, "ok": False, "results": results})
                raise RuntimeError(f"Le modèle {alias} échoue au test de capacité JSON/outils")
        save_json(cache_path, {"fingerprint": fingerprint, "ok": True, "tested_at": datetime.now().isoformat(), "results": results})

    def _budget(self, model: str) -> dict[str, int]:
        budgets = self.cfg.get("model_budgets", {})
        default = {"context": 8192, "output": 1400, "tool_reserve": 3000, "safety": 600}
        return {**default, **budgets.get(model, {})}

    def _prepare_prompt(self, label: str, prompt: str, model: str, attempt: int) -> str:
        budget = self._budget(model)
        max_input = max(900, int(budget["context"]) - int(budget["output"]) - int(budget["tool_reserve"]) - int(budget["safety"]))
        compacted, changed = compact_prompt(prompt, max_input)
        if changed:
            archive = self.controller_dir / "compaction" / f"{slug(label)}-a{attempt}-full.txt"
            archive.parent.mkdir(parents=True, exist_ok=True)
            archive.write_text(prompt, encoding="utf-8")
            compacted += (
                "\n\nLe prompt complet est archivé pour audit dans " + str(archive) + ". "
                "N'essaie pas de le relire intégralement; utilise seulement les références ciblées ci-dessus."
            )
            self.event("context_compacted", label=label, before=estimate_tokens(prompt), after=estimate_tokens(compacted), budget=max_input)
        return compacted

    def _operation_start(self, label: str, model: str, prompt: str, read_only: bool, attempt: int) -> dict[str, Any]:
        operation = {
            "id": stable_id("OP", {"label": label, "attempt": attempt, "time": time.time_ns()}),
            "label": label,
            "model": model,
            "attempt": attempt,
            "read_only": read_only,
            "status": "running",
            "started_at": datetime.now().isoformat(timespec="seconds"),
            "checkpoint_sha": self.checkpoint_sha,
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "prompt_tokens_estimated": estimate_tokens(prompt),
            "model_switch": self._last_operation_model not in {None, model},
            "changed_files_before": self.changed_files(self.checkpoint_sha) if self.worktree.exists() else [],
        }
        self._last_operation_model = model
        self.state.setdefault("operations", []).append(operation)
        self.state["operations"] = self.state["operations"][-120:]
        self.save_state()
        return operation

    def _operation_end(self, operation: dict[str, Any], status: str, **extra: Any) -> None:
        operation.update(status=status, finished_at=datetime.now().isoformat(timespec="seconds"), **extra)
        if self.worktree.exists():
            operation["changed_files_after"] = self.changed_files(self.checkpoint_sha)
        self.save_state()

    def _restart_router(self) -> bool:
        try:
            if os.name == "nt":
                script = self.source / "start-model-router.ps1"
                command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script), "-Restart"]
            else:
                script = self.source / "start-model-router.sh"
                command = [str(script)]
            proc = subprocess.run(command, cwd=self.source, text=True, capture_output=True, timeout=180)
            if proc.returncode == 0:
                self.event("router_restarted")
                return True
        except Exception:
            pass
        return False

    def _qwen_command(
        self, prompt: str, model: str, approval: str, limits: legacy.Limits,
        schema_name: str, read_only: bool, excluded_tools_override: str | None,
    ) -> list[str]:
        command = cli_command(
            self.qwen_prefix or resolve_cli("qwen") or ["qwen"],
            [
                *(["--experimental-lsp"] if self.cfg.get("enable_lsp", True) else []),
                "-p", prompt,
                "--model", model,
                "--approval-mode", approval,
                "--max-session-turns", str(limits.turns),
                "--max-tool-calls", str(limits.tool_calls),
                "--max-wall-time", limits.wall_time,
                "--json-schema", f"@{(legacy.SCHEMAS / schema_name).resolve()}",
                "--output-format", "text",
            ],
        )
        excluded = excluded_tools_override or (
            "run_shell_command,write_file,edit,agent" if read_only else "agent"
        )
        command.extend(["--exclude-tools", excluded])
        if self.cfg.get("sandbox"):
            command.append("--sandbox")
        return command

    def qwen_json(
        self, label: str, prompt: str, model: str, approval: str, limits: legacy.Limits,
        schema_name: str, read_only: bool, excluded_tools_override: str | None = None,
    ) -> dict[str, Any]:
        self.maybe_unload_previous_model(model)
        log_dir = self.controller_dir / "qwen"
        log_dir.mkdir(parents=True, exist_ok=True)
        primary = model
        candidates = [primary]
        fallback = str(self.cfg.get("model_fallbacks", {}).get(primary, "")).strip()
        if fallback and fallback != primary:
            candidates.append(fallback)
        max_attempts = int(self.cfg.get("max_qwen_retries", 2)) + 1
        last_error = "échec inconnu"
        base_human = self.human_prompt_block()

        for candidate_index, candidate in enumerate(candidates):
            for attempt in range(1, max_attempts + 1):
                effective = self._prepare_prompt(label, prompt + base_human, candidate, attempt)
                self.set_active(
                    str(self.state.get("active_scope") or "agent"),
                    self.state.get("active_microtask"),
                    f"qwen:{label}:a{attempt}",
                )
                self.safe_point()
                operation = self._operation_start(label, candidate, effective, read_only, attempt)
                stem = f"{len(list(log_dir.glob('*.prompt.txt'))) + 1:03d}-{slug(label)}-{slug(candidate)}-a{attempt}"
                (log_dir / f"{stem}.prompt.txt").write_text(effective, encoding="utf-8")
                command = self._qwen_command(effective, candidate, approval, limits, schema_name, read_only, excluded_tools_override)
                try:
                    returncode, stdout, stderr, duration = self.controlled_process(
                        command,
                        self.worktree,
                        f"qwen-{label}",
                        env={
                            **os.environ,
                            "LLAMA_API_KEY": os.environ.get("LLAMA_API_KEY", "local"),
                            "OLLAMA_API_KEY": os.environ.get("OLLAMA_API_KEY", "local"),
                        },
                    )
                except legacy.HumanRetry:
                    self._operation_end(operation, "interrupted")
                    continue
                (log_dir / f"{stem}.stdout.txt").write_text(stdout, encoding="utf-8")
                (log_dir / f"{stem}.stderr.txt").write_text(stderr, encoding="utf-8")
                payload = extract_json(stdout) if returncode == 0 else None
                if isinstance(payload, dict) and payload_matches_schema(payload, legacy.SCHEMAS / schema_name):
                    save_json(log_dir / f"{stem}.json", payload)
                    self._operation_end(operation, "completed", duration_s=round(duration, 3), output_tokens_estimated=estimate_tokens(stdout))
                    self.event("qwen", label=label, model=candidate, read_only=read_only)
                    summary = str(
                        payload.get("summary") or payload.get("mission_summary") or payload.get("rationale")
                        or payload.get("verdict") or payload.get("status") or payload.get("answer") or "terminé"
                    )
                    print(f"[{label}] {summary[:500]}", flush=True)
                    before_files = set(operation.get("changed_files_before", []))
                    after_files = set(self.changed_files(self.checkpoint_sha)) if self.worktree.exists() else set()
                    touched = sorted(after_files - before_files)
                    if touched:
                        print("  [modifié] " + ", ".join(touched[:12]), flush=True)
                    return payload

                changed = self.changed_files(self.checkpoint_sha) if self.worktree.exists() else []
                if not read_only and changed:
                    recovered = self._recover_side_effect_result(label, schema_name, stdout, stderr, candidate)
                    if recovered is not None:
                        self._operation_end(operation, "recovered", duration_s=round(duration, 3), output_tokens_estimated=estimate_tokens(stdout + stderr))
                        self.event("qwen_recovered", label=label, changed=len(changed), returncode=returncode)
                        return recovered

                if returncode == 0:
                    repaired = self._repair_structured_output(label, schema_name, stdout, candidate)
                    if repaired is not None:
                        self._operation_end(operation, "recovered", duration_s=round(duration, 3), output_tokens_estimated=estimate_tokens(stdout))
                        self.event("qwen_recovered", label=label, kind="json")
                        return repaired
                    failure = "JSON"
                    last_error = f"Sortie JSON invalide pour {label}"
                else:
                    failure = classify_qwen_failure(returncode, stdout, stderr)
                    last_error = f"Qwen {label} a échoué ({returncode}, {failure})"
                self._operation_end(operation, "failed", failure=failure, returncode=returncode, output_tokens_estimated=estimate_tokens(stdout + stderr))

                if failure == "SERVER" and attempt < max_attempts:
                    self._restart_router()
                if failure == "CONTEXT":
                    prompt = compact_prompt(prompt, max(700, self._budget(candidate)["context"] // 3))[0]
                if failure == "MEMORY" and candidate_index + 1 < len(candidates):
                    break
                self.event("qwen_retry", label=label, model=candidate, attempt=attempt, failure=failure)
                if not read_only and changed:
                    # Never replay a side-effecting coder blindly.
                    break
            if not read_only and self.changed_files(self.checkpoint_sha):
                break
        raise RuntimeError(last_error)

    def _repair_structured_output(self, label: str, schema_name: str, raw: str, source_model: str) -> dict[str, Any] | None:
        recovery_model = str(self.cfg.get("recovery_model", self.cfg.get("judge_model", source_model)))
        prompt = (
            "Tu répares uniquement une sortie structurée. Ne lis ni n'écris aucun fichier. "
            f"Respecte exactement le schéma {schema_name}. Voici la sortie incomplète:\n\n{raw[-12000:]}"
        )
        limits = legacy.Limits(4, 0, "5m")
        command = self._qwen_command(prompt, recovery_model, "plan", limits, schema_name, True, "run_shell_command,write_file,edit,agent")
        try:
            rc, stdout, _, _ = self.controlled_process(command, self.worktree, f"qwen-repair-{label}", env={**os.environ, "LLAMA_API_KEY": "local"})
            payload = extract_json(stdout)
            return payload if (rc == 0 and isinstance(payload, dict) and payload_matches_schema(payload, legacy.SCHEMAS / schema_name)) else None
        except Exception:
            return None

    def _recover_side_effect_result(
        self, label: str, schema_name: str, stdout: str, stderr: str, source_model: str,
    ) -> dict[str, Any] | None:
        diff_path = self.context_text(
            f"recovery/{slug(label)}-partial.patch",
            self.diff_text(self.checkpoint_sha) or "# Aucun diff détecté\n",
        )
        raw_path = self.context_text(
            f"recovery/{slug(label)}-truncated-output.txt",
            (stdout + "\n\nSTDERR:\n" + stderr)[-24000:],
        )
        recovery_model = str(self.cfg.get("recovery_model", self.cfg.get("judge_model", source_model)))
        prompt = f"""
Rôle: récupérateur en lecture seule après sortie tronquée d'un codeur.
Le code a peut-être déjà été modifié. Ne relance aucune édition et aucune commande.
Inspecte le diff `{self.rel(diff_path)}`, la sortie `{self.rel(raw_path)}` et le worktree réel.
Reconstruis uniquement le résultat JSON attendu par `{schema_name}`. Déclare BLOCKED si le diff est ambigu.
""".strip()
        limits = legacy.Limits(10, 20, "10m")
        command = self._qwen_command(prompt, recovery_model, "plan", limits, schema_name, True, "run_shell_command,write_file,edit,agent")
        try:
            rc, recovered, _, _ = self.controlled_process(command, self.worktree, f"qwen-side-effect-recovery-{label}", env={**os.environ, "LLAMA_API_KEY": "local"})
            payload = extract_json(recovered)
            return payload if (rc == 0 and isinstance(payload, dict) and payload_matches_schema(payload, legacy.SCHEMAS / schema_name)) else None
        except Exception:
            return None

    def _display_stream_line(self, label: str, channel: str, line: str, thinking: dict[str, bool]) -> None:
        stripped = line.rstrip()
        if not stripped:
            return
        low = stripped.lower()
        if "<think" in low:
            thinking[channel] = True
        if thinking[channel]:
            if "</think>" in low:
                thinking[channel] = False
            return
        candidate = stripped.lstrip()
        jsonish = candidate.startswith("{") or bool(re.match(r'^\[(?:\s*[\{\"0-9\-\]]|\s*$)', candidate))
        if jsonish and label.startswith("qwen-"):
            return
        prefix = "test" if label.startswith("test-") else "agent"
        print(f"  [{prefix}] {stripped[:1200]}", flush=True)

    def controlled_process(
        self, argv: list[str], cwd: Path, label: str, env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, str, float]:
        self.safe_point()
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        proc = subprocess.Popen(
            argv, cwd=cwd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=env, creationflags=creationflags, start_new_session=os.name != "nt", bufsize=1,
        )
        self._active_proc = proc
        self._inside_process = True
        started = time.monotonic()
        items: queue.Queue[tuple[str, str | None]] = queue.Queue()
        outputs = {"stdout": [], "stderr": []}
        thinking = {"stdout": False, "stderr": False}

        def reader(name: str, stream: Any) -> None:
            try:
                for value in iter(stream.readline, ""):
                    items.put((name, value))
            finally:
                items.put((name, None))

        assert proc.stdout is not None and proc.stderr is not None
        threading.Thread(target=reader, args=("stdout", proc.stdout), daemon=True).start()
        threading.Thread(target=reader, args=("stderr", proc.stderr), daemon=True).start()
        closed: set[str] = set()
        poll = float(self.cfg.get("human_control_poll_seconds", 1.0))
        try:
            while proc.poll() is None or len(closed) < 2:
                try:
                    channel, line = items.get(timeout=max(0.1, min(0.5, poll)))
                    if line is None:
                        closed.add(channel)
                    else:
                        outputs[channel].append(line)
                        self._display_stream_line(label, channel, line, thinking)
                except queue.Empty:
                    pass
                if timeout is not None and time.monotonic() - started >= timeout:
                    self.terminate_process(proc)
                    outputs["stderr"].append("\nTIMEOUT\n")
                    return 124, "".join(outputs["stdout"]), "".join(outputs["stderr"]), time.monotonic() - started
                for action in self.ingest_interventions():
                    kind = str(action.payload.get("action"))
                    if kind == "pause":
                        self.terminate_process(proc)
                        self.snapshot_interruption(label + "-pause", action.payload)
                        self.wait_for_resume(action.payload)
                        raise legacy.HumanRetry(f"Rejouer {label} après pause")
                    if kind in {"revise", "replan", "abort"}:
                        self.terminate_process(proc)
                        self.snapshot_interruption(label + "-interrupt", action.payload)
                        raise action
            return proc.returncode or 0, "".join(outputs["stdout"]), "".join(outputs["stderr"]), time.monotonic() - started
        except KeyboardInterrupt:
            self.terminate_process(proc)
            self.state["phase"] = "interrupted"
            self.event("resilience_keyboard_interrupt", label=label)
            raise
        finally:
            self._inside_process = False
            self._active_proc = None

    def ingest_interventions(self) -> list[legacy.HumanAction]:
        if not self.cfg.get("human_control_enabled", True):
            return []
        inbox, processed = self.control_paths()
        actionable: list[legacy.HumanAction] = []
        control = self.state["human_control"]
        allowed = {
            "note", "constraint", "pause", "resume", "review", "revise", "replan", "abort",
            "ask", "answer",
        }
        for path in sorted(inbox.glob("*.json")):
            try:
                payload = load_json(path)
            except Exception as exc:
                path.replace(processed / (path.stem + ".invalid.json"))
                self.event("human_intervention_invalid", file=path.name, error=str(exc))
                continue
            action = str(payload.get("action", "")).lower()
            if action not in allowed:
                path.replace(processed / (path.stem + ".rejected.json"))
                continue
            control["processed_count"] = int(control.get("processed_count", 0)) + 1
            payload["sequence"] = control["processed_count"]
            payload["resolved_target"] = str(
                payload.get("resolved_target")
                or self.resolve_intervention_target(str(payload.get("target", "current")))
            )
            payload["received_at"] = datetime.now().isoformat(timespec="seconds")
            payload.setdefault("delivery", "queued")
            context_path = self.mirror_intervention(payload)
            record = dict(payload)
            if context_path is not None:
                record["context_path"] = self.rel(context_path)
            control["interventions"].append(record)
            control["interventions"] = control["interventions"][-int(self.cfg.get("max_human_context_records", 30)):]
            if action == "pause":
                control["status"] = "paused"
                actionable.append(legacy.HumanAction(record, context_path))
            elif action == "resume":
                control["status"] = "running"
            elif action == "review":
                control["mandatory_reviews"].append({**record, "consumed": False})
            elif action in {"revise", "replan", "abort"}:
                active = str(self.state.get("active_microtask") or self.state.get("active_scope") or "global")
                if action == "abort" or self.intervention_matches(record, active):
                    record["dispatched"] = True
                    actionable.append(legacy.HumanAction(record, context_path))
                else:
                    record["dispatched"] = False
                    control["pending_actions"].append(record)
            elif action == "ask":
                record["answered"] = False
            elif action == "answer":
                question_id = str(record.get("request_id", ""))
                for question in self.state.get("questions", []):
                    if question.get("id") == question_id and not question.get("answered"):
                        question["answered"] = True
                        question["answer"] = str(record.get("message", ""))
                        question["answered_at"] = datetime.now().isoformat(timespec="seconds")
                        self.event("human_answer", id=question_id)
            path.replace(processed / path.name)
            self.event("human_intervention", action=action, target=payload["resolved_target"], sequence=payload["sequence"])
        active = str(self.state.get("active_microtask") or self.state.get("active_scope") or "global")
        for record in control.get("pending_actions", []):
            if not record.get("dispatched") and self.intervention_matches(record, active):
                record["dispatched"] = True
                actionable.append(legacy.HumanAction(record, None))
        self.save_state()
        return actionable

    def relevant_human_records(self, target: str | None = None) -> list[dict[str, Any]]:
        target = target or str(self.state.get("active_microtask") or self.state.get("active_scope") or "global")
        actions = {"note", "constraint", "review", "revise", "replan", "resume", "answer"}
        return [
            item for item in self.state.get("human_control", {}).get("interventions", [])
            if self.intervention_matches(item, target)
            and str(item.get("action")) in actions
            and (str(item.get("message", "")).strip() or item.get("context_files"))
        ][-int(self.cfg.get("max_human_context_records", 30)):]

    def human_prompt_block(self) -> str:
        records = self.relevant_human_records()
        if not records:
            return ""
        maximum_records = int(self.cfg.get("max_human_prompt_records", 5))
        maximum_chars = int(self.cfg.get("max_human_prompt_chars", 4000))
        mandatory = [item for item in records if str(item.get("action")) in {"constraint", "review", "revise", "replan"}]
        optional = [item for item in records if item not in mandatory]
        selected = sorted([*mandatory[-3:], *optional[-max(0, maximum_records - 3):]], key=lambda item: int(item.get("sequence", 0)))[-maximum_records:]
        lines = []
        now = datetime.now().isoformat(timespec="seconds")
        for item in selected:
            message = str(item.get("message", "")).strip()[:700]
            lines.append(f"#{item.get('sequence')} {item.get('action')} cible={item.get('resolved_target')}: {message}")
            if item.get("delivery") == "queued":
                item["delivery"] = "injected"
                item["injected_at"] = now
        self.save_state()
        block = "\n\nINTERVENTIONS HUMAINES ACTIVES:\n- " + "\n- ".join(lines)
        block += "\nTraite-les explicitement sans élargir la mission."
        return block[-maximum_chars:]

    def safe_point(self) -> None:
        actions = self.ingest_interventions()
        for action in actions:
            kind = str(action.payload.get("action"))
            if kind == "pause":
                self.snapshot_interruption("pause", action.payload)
                self.wait_for_resume(action.payload)
            elif kind in {"revise", "replan", "abort"}:
                raise action
        if not self._inside_process:
            self._answer_user_asks()

    def _answer_user_asks(self) -> None:
        for item in self.state.get("human_control", {}).get("interventions", []):
            if item.get("action") != "ask" or item.get("answered") or item.get("answering"):
                continue
            item["answering"] = True
            self.save_state()
            prompt = f"""
Réponds brièvement à la question humaine suivante en lecture seule, à partir de la mission, du state.json,
du worktree et des preuves existantes. Ne modifie rien et ne lance aucune commande.
Question: {item.get('message')}
""".strip()
            try:
                result = self.qwen_json(
                    f"human-ask-{item.get('sequence')}", prompt, self.cfg["judge_model"],
                    self.cfg["reviewer_approval_mode"], legacy.Limits(8, 18, "10m"),
                    "human_answer.schema.json", True,
                )
            except Exception:
                item["answering"] = False
                self.save_state()
                raise
            item["answered"] = True
            item["answering"] = False
            item["answer"] = str(result.get("answer", ""))
            item["answered_at"] = datetime.now().isoformat(timespec="seconds")
            print(paint(f"\n[RÉPONSE] {item['answer']}", "32;1"), flush=True)
            self.save_state()

    def _ask_human(self, micro_id: str, request: dict[str, Any]) -> str:
        counts = self.state.setdefault("question_counts", {"total": 0, "by_target": {}})
        by_target = counts.setdefault("by_target", {})
        if int(counts.get("total", 0)) >= int(self.cfg.get("max_human_questions_per_run", 5)):
            return "Budget de questions humaines épuisé."
        if int(by_target.get(micro_id, 0)) >= int(self.cfg.get("max_human_questions_per_microtask", 2)):
            return "Budget de questions humaines de cette micro-tâche épuisé."
        question = str((request.get("requests") or [{}])[0].get("question") or "Quelle information manque pour continuer ?")
        question_id = stable_id("Q", {"micro": micro_id, "question": question, "time": time.time_ns()})
        record = {
            "id": question_id,
            "target": micro_id,
            "question": question,
            "request": request,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "answered": False,
        }
        self.state.setdefault("questions", []).append(record)
        counts["total"] = int(counts.get("total", 0)) + 1
        by_target[micro_id] = int(by_target.get(micro_id, 0)) + 1
        self.save_state()
        self.event("human_question", id=question_id, target=micro_id)
        print(paint(f"\n[QUESTION {question_id} · {micro_id}]\n{question}\nRéponds avec: /answer {question_id} ta réponse", "35;1"), flush=True)
        while not record.get("answered"):
            time.sleep(max(0.25, float(self.cfg.get("human_control_poll_seconds", 1.0))))
            for action in self.ingest_interventions():
                if str(action.payload.get("action")) == "abort":
                    raise action
                if str(action.payload.get("action")) in {"revise", "replan"}:
                    raise action
        return str(record.get("answer", ""))

    def scout_microtask(
        self, micro: dict[str, Any], request: dict[str, Any] | None = None,
        prior_packs: list[Path] | None = None, round_no: int = 0,
    ) -> Path:
        threshold = int(self.cfg.get("human_question_after_context_rounds", 2))
        if request and round_no > threshold:
            answer = self._ask_human(str(micro["id"]), request)
            return self.context_json(
                f"human/questions/{slug(str(micro['id']))}-{round_no}.json",
                {"status": "HUMAN_ANSWER", "request": request, "answer": answer},
            )
        return super().scout_microtask(micro, request=request, prior_packs=prior_packs, round_no=round_no)

    def _pending_human_constraints(self, target: str) -> list[dict[str, Any]]:
        self.ingest_interventions()
        return [
            item for item in self.state.get("human_control", {}).get("interventions", [])
            if item.get("action") in {"note", "constraint"}
            and self.intervention_matches(item, target)
            and item.get("delivery") != "verified"
        ]

    def _verify_human_barrier(self, target: str) -> None:
        pending = self._pending_human_constraints(target)
        if not pending:
            return
        self.human_prompt_block()
        diff_path = self.context_text(
            f"human/barriers/{slug(target)}.patch",
            self.diff_text(self.checkpoint_sha) or "# Aucun diff\n",
        )
        prompt = f"""
Rôle: contrôleur final en lecture seule avant checkpoint `{target}`.
Lis la mission `{self.task_rel}`, le diff `{self.rel(diff_path)}` et les interventions humaines actives.
PASS uniquement si chaque note/contrainte visant cette cible a été prise en compte ou n'est pas applicable,
avec une justification vérifiable. Ne modifie rien et ne lance aucune commande.
""".strip()
        result = self.qwen_json(
            f"human-barrier-{target}", prompt, self.cfg["architecture_reviewer_model"],
            self.cfg["reviewer_approval_mode"], legacy.Limits(10, 20, "10m"),
            "human_ack.schema.json", True,
        )
        if result.get("verdict") != "PASS":
            payload = {
                "action": "revise",
                "message": "Intervention humaine non satisfaite: " + "; ".join(result.get("issues", [])),
                "sequence": 0,
                "resolved_target": target,
            }
            raise legacy.HumanAction(payload)
        now = datetime.now().isoformat(timespec="seconds")
        for item in pending:
            item["delivery"] = "verified"
            item["verified_at"] = now
        self.save_state()
        self.event("human_note_verified", target=target, count=len(pending))

    def checkpoint(self, label: str) -> None:
        # Three gates close the race where a note arrives just before validation.
        prior = self.checkpoint_sha
        self._verify_human_barrier(label)
        subprocess.run(["git", "add", "-A"], cwd=self.worktree, check=True)
        self.safe_point()
        self._verify_human_barrier(label)
        staged = self.capture(["git", "diff", "--cached", "--name-only"], self.worktree, False)
        if not staged:
            raise RuntimeError(f"Aucun changement à enregistrer pour le checkpoint {label}")
        subprocess.run(
            ["git", "commit", "-m", f"agent-checkpoint: {label}"],
            cwd=self.worktree, check=True,
        )
        # Keep the previous checkpoint active while consuming a last-millisecond note;
        # diff_text(prior) still sees the complete just-created commit.
        self.safe_point()
        self._verify_human_barrier(label)
        self.checkpoint_sha = self.capture(["git", "rev-parse", "HEAD"], self.worktree)
        self.state["checkpoint_commits"].append(self.checkpoint_sha)
        self.event("checkpoint", label=label, sha=self.checkpoint_sha, previous=prior)

    def _approval_mode(self) -> str:
        settings = self.controller_dir / "control" / "settings.json"
        if settings.exists():
            return str(load_json(settings).get("approval_mode", self.cfg.get("human_approval_mode", "commands")))
        return str(self.cfg.get("human_approval_mode", "commands"))

    def _request_approval(self, kind: str, payload: dict[str, Any]) -> bool:
        request = {
            "kind": kind,
            "target": str(self.state.get("active_microtask") or self.state.get("active_scope") or "global"),
            "operation": str(self.state.get("active_operation") or kind),
            "payload": payload,
        }
        settings_path = self.controller_dir / "control" / "settings.json"
        settings = load_json(settings_path) if settings_path.exists() else {}
        for grant in settings.get("grants", []):
            if grant.get("kind") == kind and grant.get("payload") == payload:
                return True
        request_id = stable_id("A", request)
        request["id"] = request_id
        request["created_at"] = datetime.now().isoformat(timespec="seconds")
        pending = self.controller_dir / "control" / "approvals" / "pending"
        decisions = self.controller_dir / "control" / "approvals" / "decisions"
        pending.mkdir(parents=True, exist_ok=True)
        decisions.mkdir(parents=True, exist_ok=True)
        save_json(pending / f"{request_id}.json", request)
        self.event("approval_wait", id=request_id, kind=kind)
        print(paint(f"\n[AUTORISATION {request_id}] {kind}: {json.dumps(payload, ensure_ascii=False)[:1000]}\n/approve {request_id} ou /deny {request_id} raison", "33;1"), flush=True)
        deadline = time.monotonic() + float(self.cfg.get("approval_timeout_seconds", 900))
        decision_path = decisions / f"{request_id}.json"
        while time.monotonic() < deadline:
            if decision_path.exists():
                decision = load_json(decision_path)
                (pending / f"{request_id}.json").unlink(missing_ok=True)
                if str(decision.get("scope", "once")) == "once":
                    decision_path.unlink(missing_ok=True)
                return str(decision.get("decision", "")).lower() == "approve"
            time.sleep(0.25)
            for action in self.ingest_interventions():
                if str(action.payload.get("action")) == "abort":
                    raise action
        return False

    def _safe_new_command(self, command: str) -> bool:
        return not command_is_dangerous(command) and not command_has_complex_shell(command)

    def resolve_commands(
        self, original: list[str], corrections: list[dict[str, Any]] | None,
        validated: list[str] | None = None,
    ) -> list[str]:
        mapping: dict[str, str] = {}
        for correction in corrections or []:
            old = str(correction.get("original", "")).strip()
            new = str(correction.get("replacement", "")).strip()
            if old in original and self._safe_new_command(new):
                mapping[old] = new
        resolved = [mapping.get(str(command).strip(), str(command).strip()) for command in original]
        for command in validated or []:
            command = str(command).strip()
            if command and self._safe_new_command(command) and command not in resolved:
                resolved.append(command)
        return list(dict.fromkeys(resolved))

    def run_tests(self, commands: list[str], label: str) -> tuple[dict[str, Any], Path]:
        timeout = int(self.meta["command_timeout_seconds"])
        results: list[dict[str, Any]] = []
        exact = {
            str(value).strip()
            for value in [*self.meta["validation_commands"], *self.meta["full_suite_commands"]]
            if str(value).strip()
        }
        for command in list(dict.fromkeys(str(value).strip() for value in commands if str(value).strip())):
            if command_is_dangerous(command):
                approved = False
            else:
                mode = self._approval_mode()
                needs = mode == "all" or (mode == "commands" and command not in exact) or (
                    mode == "auto" and not legacy.Orchestrator.command_allowed(self, command)
                )
                approved = not needs or self._request_approval("shell", {"command": command, "label": label})
            if not approved:
                results.append({
                    "command": command, "ok": False, "returncode": 126, "duration_s": 0.0,
                    "output": "Commande refusée ou autorisation expirée", "failure_class": "COMMAND",
                    "shell_family": "policy",
                })
                continue
            argv, shell_family = shell_command(command)
            self.set_active(str(self.state.get("active_scope") or "tests"), self.state.get("active_microtask"), f"test:{label}:{command}")
            while True:
                try:
                    rc, stdout, stderr, duration = self.controlled_process(
                        argv, self.worktree, f"test-{label}", env=os.environ.copy(), timeout=timeout,
                    )
                    break
                except legacy.HumanRetry:
                    continue
            output = (stdout + "\n" + stderr).strip()
            full_log = self.controller_dir / "tests" / f"{slug(label)}-{stable_id('C', command)}.log"
            full_log.parent.mkdir(parents=True, exist_ok=True)
            full_log.write_text(output, encoding="utf-8")
            compact = output[-12000:]
            result = CommandResult(
                command=command, ok=rc == 0, returncode=rc, duration_s=round(duration, 3),
                output=compact, failure_class=("ENVIRONMENT" if rc == 124 else classify_failure(rc, output)),
                shell_family=shell_family,
            )
            results.append(result_dict(result))
        payload = {"label": label, "ok": bool(results) and all(item["ok"] for item in results), "results": results}
        path = self.context_json(f"tests/{slug(label)}.json", payload)
        self.event("tests", label=label, ok=payload["ok"])
        return payload, path

    def coder_prompt(
        self, micro: dict[str, Any], evidence_paths: list[Path], context_paths: list[Path], global_task: bool,
    ) -> str:
        prompt = super().coder_prompt(micro, evidence_paths, context_paths, global_task)
        evidence = self._resume_evidence.get(str(micro.get("id")))
        if evidence:
            prompt += (
                f"\n\nREPRISE APRÈS INTERRUPTION: inspecte d'abord le diff partiel `{self.rel(evidence)}`. "
                "Conserve les modifications cohérentes, corrige seulement ce qui manque et ne recommence pas aveuglément."
            )
        return prompt

    def squash_commit(self) -> None:
        self._verify_human_barrier("global")
        current = self.capture(["git", "rev-parse", "HEAD"], self.source, False)
        if current and current != self.base:
            self.event("source_advanced", base=self.base, current=current)
            print("[AVERTISSEMENT] La branche source a avancé pendant le run; le commit devra être rebasé avant fusion.", flush=True)
        super().squash_commit()

    def _resume_run(self) -> None:
        self.event("resume_started", run=self.controller_dir.name)
        if self.capture(["git", "rev-parse", "--is-inside-work-tree"], self.worktree) != "true":
            raise RuntimeError("Le worktree de reprise n'est plus valide")
        actual_branch = self.capture(["git", "branch", "--show-current"], self.worktree)
        if actual_branch != self.branch:
            raise RuntimeError(f"Branche de reprise inattendue: {actual_branch} != {self.branch}")
        plan_path = self.context_dir / "WORK_PLAN.json"
        if not plan_path.exists():
            self.state["phase"] = "planning"
            plan = self.establish_plan()
            self.state["microtask_status"] = {task["id"]: "pending" for task in plan["microtasks"]}
        else:
            plan = load_json(plan_path)
            statuses = self.state.setdefault("microtask_status", {})
            for task in plan.get("microtasks", []):
                status = statuses.get(task["id"], "pending")
                if status in {"in_progress", "failed", "blocked"}:
                    statuses[task["id"]] = "pending"
            active = str(self.state.get("active_microtask") or "")
            dirty = self.diff_text(self.checkpoint_sha)
            if active and dirty.strip():
                path = self.context_text(f"recovery/resume-{slug(active)}.patch", dirty)
                self._resume_evidence[active] = path
                self.event("resume_partial_diff", target=active, path=self.rel(path))
        self.save_state()

        while any(status == "pending" for status in self.state["microtask_status"].values()):
            available = ready_tasks(plan, self.state)
            if not available:
                raise RuntimeError("Aucune micro-tâche prête pendant la reprise")
            micro = available[0]
            micro_id = str(micro["id"])
            self.set_active("microtask", micro_id, "resume-execute")
            try:
                outcome = self.execute_microtask(micro, track_status=True)
            except legacy.HumanAction as action:
                plan = self.handle_micro_intervention(action, plan, micro_id)
                continue
            if outcome == "done":
                continue
            if outcome == "replan":
                plan = self.dynamic_replan(plan, micro_id)
                continue
            raise RuntimeError(f"Micro-tâche bloquée après reprise: {micro_id}")

        self.set_active("global", None, "global-validation")
        self.global_validation()
        self.set_active("documentation", None, "documentation")
        self.finalize_docs()
        self.set_active("final", None, "deterministic-checks")
        self.deterministic_final_checks()
        self._verify_human_barrier("global")
        self.set_active("commit", None, "squash-commit")
        self.squash_commit()
        self.state["phase"] = "done"
        self.set_active("done", None, None)
        self.save_state()
        print("\nSUCCÈS APRÈS REPRISE", flush=True)

    def run(self) -> None:
        self.acquire_lock()
        try:
            if self.resume_mode:
                self.preflight()
                self._probe_models_if_needed()
                self._resume_run()
            else:
                super().run()
        finally:
            self.release_lock()


def main() -> int:
    parser = argparse.ArgumentParser(description="LocalCode résilient: reprise, budgets, dialogue et approbations")
    parser.add_argument("task", nargs="?")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--resume", default="")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    orchestrator: ResilientOrchestrator | None = None
    try:
        if args.resume:
            orchestrator = ResilientOrchestrator.from_run(repo, args.resume)
        else:
            if not args.task:
                raise RuntimeError("Une mission est requise sans --resume")
            task = Path(args.task)
            task = task if task.is_absolute() else repo / task
            orchestrator = ResilientOrchestrator(repo, task)
        orchestrator.run()
        return 0
    except KeyboardInterrupt:
        if orchestrator is not None:
            orchestrator.state["phase"] = "interrupted"
            orchestrator.save_state()
            if orchestrator.worktree.exists():
                print(
                    f"\nINTERRUPTION SAUVEGARDÉE. Reprise: .\\agent.ps1 -Resume {orchestrator.controller_dir.name}",
                    file=sys.stderr,
                )
            else:
                print("\nInterruption avant création du worktree; relance la mission normalement.", file=sys.stderr)
        return 130
    except (RuntimeError, TaskMetaError, OSError, KeyError, ValueError) as exc:
        if orchestrator is not None and orchestrator.state.get("phase") not in {"aborted", "interrupted"}:
            orchestrator.state["phase"] = "failed"
            try:
                orchestrator.save_state()
            except OSError:
                pass
        print(f"\nÉCHEC: {exc}", file=sys.stderr)
        if orchestrator is not None:
            if orchestrator.worktree.exists():
                print(f"Reprise: .\\agent.ps1 -Resume {orchestrator.controller_dir.name}", file=sys.stderr)
            else:
                print("Relance la mission normalement: aucun worktree reprenable n'a été créé.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
