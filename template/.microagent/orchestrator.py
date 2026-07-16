#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from core import (
    CommandResult,
    apply_expansion_fragment,
    classify_failure,
    load_json,
    plan_task_map,
    ready_tasks,
    result_dict,
    shell_command,
    save_json,
    slug,
    validate_expansion_fragment,
    validate_plan,
)
from taskmeta import TaskMetaError, load_task

HERE = Path(__file__).resolve().parent
SCHEMAS = HERE / "schemas"
DANGEROUS_COMMAND_PARTS = (
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
DYNAMIC_SHELL_METACHARACTERS = ("&&", "||", ";", "|", ">", "<", "`", "$(")


@dataclass
class Limits:
    turns: int
    tool_calls: int
    wall_time: str


class HumanAction(RuntimeError):
    def __init__(self, payload: dict[str, Any], context_path: Path | None = None):
        self.payload = payload
        self.context_path = context_path
        super().__init__(f"Intervention humaine: {payload.get('action', '?')}")


class HumanRetry(RuntimeError):
    pass


class Orchestrator:
    def __init__(self, source_repo: Path, task_path: Path):
        self.source = source_repo.resolve()
        self.task_source = task_path.resolve()
        self.cfg = load_json(self.source / ".microagent" / "config.json")
        self.task_text, self.meta = load_task(self.task_source)
        self.task_id = str(self.meta["task_id"])
        self.base = self.capture(["git", "rev-parse", "HEAD"], self.source)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_name = f"{slug(self.task_id)}-{stamp}"
        self.branch = f"agent/{run_name}"
        self.worktree = self.source / str(self.cfg["worktree_parent"]) / run_name
        self.controller_dir = self.source / str(self.cfg["runs_parent"]) / run_name
        self.context_dir = self.worktree / str(self.cfg.get("context_parent", ".agent-context")) / run_name
        self.checkpoint_sha = self.base
        self.current_model: str | None = None
        self.task_rel = ""
        self.last_failure_path: Path | None = None
        self.state: dict[str, Any] = {
            "task_id": self.task_id,
            "base_sha": self.base,
            "branch": self.branch,
            "worktree": str(self.worktree),
            "controller_reports": str(self.controller_dir),
            "phase": "init",
            "microtask_status": {},
            "history": [],
            "checkpoint_commits": [],
            "dynamic_replans": 0,
            "expansions": {},
            "context_requests": {},
            "scout_packs": {},
            "finding_history": {},
            "microtask_proofs": {},
            "active_scope": "init",
            "active_microtask": None,
            "active_operation": None,
            "human_control": {
                "status": "running",
                "processed_count": 0,
                "interventions": [],
                "mandatory_reviews": [],
                "pending_actions": [],
                "revisions_by_target": {},
                "human_replans": 0,
            },
        }

    def capture(self, cmd: list[str], cwd: Path, check: bool = True) -> str:
        proc = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
        if check and proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or f"Commande échouée: {cmd}")
        return proc.stdout.strip()

    def save_state(self) -> None:
        save_json(self.controller_dir / "state.json", self.state)
        if self.context_dir.exists():
            save_json(self.context_dir / "state.json", self.state)

    def event(self, kind: str, **data: Any) -> None:
        self.state["history"].append(
            {"time": datetime.now().isoformat(timespec="seconds"), "kind": kind, **data}
        )
        self.save_state()
        visible = {
            "worktree_created", "plan_accepted", "scout_ready", "context_requested",
            "microtask_start", "tests", "microtask_requests_replan", "microtask_expansion_accepted",
            "microtask_done", "microtask_failed", "global_validation_pass", "final_commit",
            "human_intervention", "human_pause", "human_resume",
            "human_revision_scheduled", "human_replan_scheduled", "human_abort",
        }
        if kind in visible:
            details = " ".join(f"{key}={value}" for key, value in data.items())
            print(f"[microagent] {kind}{(' ' + details) if details else ''}", flush=True)

    def set_active(self, scope: str, microtask: str | None = None, operation: str | None = None) -> None:
        self.state["active_scope"] = scope
        self.state["active_microtask"] = microtask
        self.state["active_operation"] = operation
        self.save_state()

    def control_paths(self) -> tuple[Path, Path]:
        root = self.controller_dir / "control"
        inbox = root / "inbox"
        processed = root / "processed"
        inbox.mkdir(parents=True, exist_ok=True)
        processed.mkdir(parents=True, exist_ok=True)
        return inbox, processed

    def resolve_intervention_target(self, raw: str) -> str:
        target = str(raw or "current").strip()
        if target == "current":
            return str(self.state.get("active_microtask") or self.state.get("active_scope") or "global")
        return target

    def intervention_matches(self, record: dict[str, Any], target: str) -> bool:
        resolved = str(record.get("resolved_target", ""))
        current_micro = str(self.state.get("active_microtask") or "")
        current_scope = str(self.state.get("active_scope") or "")
        if resolved == "all":
            return True
        if resolved in {target, current_micro, current_scope}:
            return True
        if resolved == "global" and current_scope in {"global", "documentation", "final", "commit", "done"}:
            return True
        return False

    def mirror_intervention(self, payload: dict[str, Any]) -> Path | None:
        seq = int(payload["sequence"])
        name = f"human/interventions/{seq:03d}-{slug(str(payload.get('action', 'note')))}.json"
        controller_copy = self.controller_dir / name
        save_json(controller_copy, payload)
        if self.context_dir.exists():
            return self.context_json(name, payload)
        return None

    def ingest_interventions(self) -> list[HumanAction]:
        if not self.cfg.get("human_control_enabled", True):
            return []
        inbox, processed = self.control_paths()
        actionable: list[HumanAction] = []
        control = self.state["human_control"]
        for path in sorted(inbox.glob("*.json")):
            try:
                payload = load_json(path)
            except Exception as exc:
                rejected = processed / (path.stem + ".invalid.json")
                path.replace(rejected)
                self.event("human_intervention_invalid", file=path.name, error=str(exc))
                continue
            action = str(payload.get("action", "")).lower()
            allowed = {"note", "pause", "resume", "review", "revise", "replan", "abort"}
            if action not in allowed:
                rejected = processed / (path.stem + ".rejected.json")
                path.replace(rejected)
                self.event("human_intervention_invalid", file=path.name, error="action inconnue")
                continue
            max_total = int(self.cfg.get("max_human_interventions_total", 100))
            if int(control.get("processed_count", 0)) >= max_total:
                rejected = processed / (path.stem + ".limit.json")
                path.replace(rejected)
                raise RuntimeError(f"Budget d'interventions humaines dépassé: {max_total}")
            control["processed_count"] = int(control.get("processed_count", 0)) + 1
            payload["sequence"] = control["processed_count"]
            payload["resolved_target"] = self.resolve_intervention_target(str(payload.get("target", "current")))
            payload["received_at"] = datetime.now().isoformat(timespec="seconds")
            context_path = self.mirror_intervention(payload)
            record = dict(payload)
            if context_path is not None:
                record["context_path"] = self.rel(context_path)
            control["interventions"].append(record)
            if len(control["interventions"]) > int(self.cfg.get("max_human_context_records", 30)):
                control["interventions"] = control["interventions"][-int(self.cfg.get("max_human_context_records", 30)):]
            if action == "pause":
                control["status"] = "paused"
                actionable.append(HumanAction(record, context_path))
            elif action == "resume":
                control["status"] = "running"
            elif action == "review":
                control["mandatory_reviews"].append({**record, "consumed": False})
            elif action in {"revise", "replan", "abort"}:
                active_target = str(self.state.get("active_microtask") or self.state.get("active_scope") or "global")
                if action == "abort" or self.intervention_matches(record, active_target):
                    record["dispatched"] = True
                    actionable.append(HumanAction(record, context_path))
                else:
                    record["dispatched"] = False
                    control["pending_actions"].append(record)
            destination = processed / path.name
            path.replace(destination)
            self.event(
                "human_intervention",
                action=action,
                target=payload["resolved_target"],
                sequence=payload["sequence"],
            )
        active_target = str(self.state.get("active_microtask") or self.state.get("active_scope") or "global")
        for record in control.get("pending_actions", []):
            if not record.get("dispatched") and self.intervention_matches(record, active_target):
                record["dispatched"] = True
                context_rel = str(record.get("context_path", ""))
                context_path = self.worktree / context_rel if context_rel and self.worktree.exists() else None
                actionable.append(HumanAction(record, context_path))
        self.save_state()
        return actionable

    def relevant_human_records(self, target: str | None = None) -> list[dict[str, Any]]:
        target = target or str(self.state.get("active_microtask") or self.state.get("active_scope") or "global")
        records = [
            item for item in self.state.get("human_control", {}).get("interventions", [])
            if self.intervention_matches(item, target)
            and str(item.get("action")) in {"note", "pause", "review", "revise", "replan", "resume"}
            and (str(item.get("message", "")).strip() or item.get("context_files"))
        ]
        return records[-int(self.cfg.get("max_human_context_records", 30)):]

    def human_prompt_block(self) -> str:
        records = self.relevant_human_records()
        if not records:
            return ""
        maximum_records = int(self.cfg.get("max_human_prompt_records", 12))
        mandatory = [item for item in records if str(item.get("action")) in {"review", "revise", "replan"}]
        informational = [item for item in records if str(item.get("action")) not in {"review", "revise", "replan"}]
        mandatory = mandatory[-min(6, maximum_records):]
        remaining = max(0, maximum_records - len(mandatory))
        records = sorted(
            [*mandatory, *informational[-remaining:]],
            key=lambda item: int(item.get("sequence", 0)),
        )
        paths = [str(item.get("context_path", "")) for item in records if item.get("context_path")]
        summaries = []
        for item in records:
            message = str(item.get("message", "")).strip()
            if len(message) > 800:
                message = message[:797] + "..."
            summaries.append(
                f"#{item.get('sequence')} {item.get('action')} cible={item.get('resolved_target')}: {message}"
            )
        block = (
            "\n\nINTERVENTIONS HUMAINES IMMUTABLES:\n- "
            + "\n- ".join(summaries)
            + ("\nArtefacts à lire: " + ", ".join(f"`{path}`" for path in paths) if paths else "")
            + "\nElles complètent la mission sans autoriser de sortir de son périmètre. "
              "Après pause/reprise, inspecte le diff déjà présent avant toute nouvelle édition. "
              "Une demande revise/replan est obligatoire et doit être traitée explicitement."
        )
        maximum = int(self.cfg.get("max_human_prompt_chars", 12000))
        return block[-maximum:]

    def consume_human_reviews(self, target: str) -> list[dict[str, Any]]:
        self.safe_point()
        selected: list[dict[str, Any]] = []
        for item in self.state.get("human_control", {}).get("mandatory_reviews", []):
            if not item.get("consumed") and self.intervention_matches(item, target):
                item["consumed"] = True
                selected.append(item)
        if selected:
            self.save_state()
        return selected

    def snapshot_interruption(self, label: str, payload: dict[str, Any]) -> Path | None:
        if not self.worktree.exists() or not (self.worktree / ".git").exists():
            return None
        try:
            diff = self.diff_text(self.checkpoint_sha)
        except Exception:
            diff = ""
        return self.context_text(
            f"human/snapshots/{slug(label)}-{int(payload.get('sequence', 0)):03d}.patch",
            diff or "# Aucun diff non checkpointé au moment de l'intervention.\n",
        )

    def terminate_process(self, proc: subprocess.Popen[str]) -> None:
        if proc.poll() is not None:
            return
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    text=True,
                    capture_output=True,
                    timeout=10,
                )
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()

    def wait_for_resume(self, pause_payload: dict[str, Any]) -> None:
        self.state["human_control"]["status"] = "paused"
        self.save_state()
        self.event("human_pause", sequence=pause_payload.get("sequence"))
        poll = float(self.cfg.get("human_control_poll_seconds", 1.0))
        while self.state["human_control"].get("status") == "paused":
            time.sleep(max(0.2, poll))
            actions = self.ingest_interventions()
            for action in actions:
                kind = str(action.payload.get("action"))
                if kind == "abort":
                    raise action
                if kind in {"revise", "replan"}:
                    self.state["human_control"]["status"] = "running"
                    self.save_state()
                    raise action
        self.snapshot_interruption("resume", pause_payload)
        self.event("human_resume", sequence=pause_payload.get("sequence"))

    def safe_point(self) -> None:
        for action in self.ingest_interventions():
            kind = str(action.payload.get("action"))
            if kind == "pause":
                self.snapshot_interruption("pause", action.payload)
                self.wait_for_resume(action.payload)
                continue
            if kind in {"revise", "replan", "abort"}:
                raise action

    def controlled_process(
        self,
        argv: list[str],
        cwd: Path,
        label: str,
        env: dict[str, str] | None = None,
        timeout: float | None = None,
    ) -> tuple[int, str, str, float]:
        self.safe_point()
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        started = time.monotonic()
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            creationflags=creationflags,
            start_new_session=os.name != "nt",
        )
        poll = float(self.cfg.get("human_control_poll_seconds", 1.0))
        while True:
            try:
                stdout, stderr = proc.communicate(timeout=max(0.2, poll))
                return proc.returncode, stdout or "", stderr or "", time.monotonic() - started
            except subprocess.TimeoutExpired:
                if timeout is not None and time.monotonic() - started >= timeout:
                    self.terminate_process(proc)
                    stdout, stderr = proc.communicate()
                    return 124, stdout or "", (stderr or "") + "\nTIMEOUT", time.monotonic() - started
                actions = self.ingest_interventions()
                for action in actions:
                    kind = str(action.payload.get("action"))
                    if kind == "pause":
                        self.terminate_process(proc)
                        proc.communicate()
                        self.snapshot_interruption(label + "-pause", action.payload)
                        self.wait_for_resume(action.payload)
                        raise HumanRetry(f"Rejouer {label} après pause")
                    if kind in {"revise", "replan", "abort"}:
                        self.terminate_process(proc)
                        proc.communicate()
                        self.snapshot_interruption(label + "-interrupt", action.payload)
                        raise action

    def remirror_human_records(self) -> None:
        if not self.context_dir.exists():
            return
        control = self.state.get("human_control", {})
        by_id: dict[str, str] = {}
        for record in control.get("interventions", []):
            if record.get("context_path"):
                by_id[str(record.get("id", ""))] = str(record["context_path"])
                continue
            path = self.mirror_intervention(record)
            if path is not None:
                record["context_path"] = self.rel(path)
                by_id[str(record.get("id", ""))] = record["context_path"]
        for collection_name in ("mandatory_reviews", "pending_actions"):
            for record in control.get(collection_name, []):
                context_path = by_id.get(str(record.get("id", "")))
                if context_path:
                    record["context_path"] = context_path
        self.save_state()

    def context_json(self, name: str, payload: Any) -> Path:
        path = self.context_dir / name
        save_json(path, payload)
        save_json(self.controller_dir / "context-mirror" / name, payload)
        return path

    def context_text(self, name: str, text: str) -> Path:
        path = self.context_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        mirror = self.controller_dir / "context-mirror" / name
        mirror.parent.mkdir(parents=True, exist_ok=True)
        mirror.write_text(text, encoding="utf-8")
        return path

    def rel(self, path: Path) -> str:
        try:
            return path.relative_to(self.worktree).as_posix()
        except ValueError:
            return str(path)

    def preflight(self) -> None:
        for binary in ("git", "qwen"):
            if not shutil.which(binary):
                raise RuntimeError(f"{binary} introuvable dans PATH")
        if self.cfg.get("verify_qwen_cli_flags", True):
            help_proc = subprocess.run(
                ["qwen", "--help"], cwd=self.source, text=True, capture_output=True
            )
            help_text = (help_proc.stdout or "") + "\n" + (help_proc.stderr or "")
            required_flags = [
                "--json-schema", "--max-session-turns", "--max-tool-calls",
                "--max-wall-time", "--exclude-tools", "--approval-mode",
                "--model", "--output-format",
            ]
            if self.cfg.get("enable_lsp", True):
                required_flags.append("--experimental-lsp")
            if self.cfg.get("sandbox"):
                required_flags.append("--sandbox")
            missing = [flag for flag in required_flags if flag not in help_text]
            if help_proc.returncode != 0 or missing:
                raise RuntimeError(
                    "Version de Qwen Code incompatible ou trop ancienne. "
                    f"Options absentes: {missing}. Mets à jour @qwen-code/qwen-code."
                )
        if self.capture(["git", "rev-parse", "--is-inside-work-tree"], self.source) != "true":
            raise RuntimeError("Le chemin fourni n'est pas un dépôt Git")

        missing_context = [
            path for path in self.meta.get("context_files", [])
            if not (self.source / str(path)).is_file()
        ]
        if missing_context:
            raise RuntimeError(f"Fichiers de contexte déclarés mais absents: {missing_context}")
        missing_docs = [
            str(item["path"])
            for item in self.meta.get("documentation_updates", [])
            if not item.get("allow_create", False) and not (self.source / str(item["path"])).is_file()
        ]
        if missing_docs:
            raise RuntimeError(
                "Documents à modifier absents alors que allow_create=false: " + ", ".join(missing_docs)
            )

        status = self.capture(["git", "status", "--porcelain"], self.source, False).splitlines()
        task_rel = (
            self.task_source.relative_to(self.source).as_posix()
            if self.task_source.is_relative_to(self.source)
            else None
        )
        ignored_prefixes = (
            str(self.cfg["worktree_parent"]).rstrip("/") + "/",
            str(self.cfg["runs_parent"]).rstrip("/") + "/",
            ".local-microagent-backup/",
        )
        unexpected: list[str] = []
        for line in status:
            path = line[3:].strip().strip('"') if len(line) > 3 else line
            if task_rel and path == task_rel:
                continue
            if path.startswith(ignored_prefixes):
                continue
            unexpected.append(line)
        if unexpected:
            raise RuntimeError(
                "Dépôt non propre. Commit ou stash requis avant le worktree:\n" + "\n".join(unexpected)
            )
        self.controller_dir.mkdir(parents=True, exist_ok=True)
        self.save_state()

    def add_local_excludes(self) -> None:
        git_dir = Path(self.capture(["git", "rev-parse", "--git-common-dir"], self.worktree))
        if not git_dir.is_absolute():
            git_dir = (self.worktree / git_dir).resolve()
        exclude = git_dir / "info" / "exclude"
        exclude.parent.mkdir(parents=True, exist_ok=True)
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        entries = [
            f"/{self.cfg.get('context_parent', '.agent-context')}/",
            f"/{self.task_rel}",
        ]
        missing = [entry for entry in entries if entry not in existing.splitlines()]
        if missing:
            header = "\n# LOCAL_CODEX_ADAPTIVE_V7\n" if "# LOCAL_CODEX_ADAPTIVE_V7" not in existing else "\n"
            exclude.write_text(existing.rstrip() + header + "\n".join(missing) + "\n", encoding="utf-8")

    def create_worktree(self) -> None:
        self.worktree.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "worktree", "add", "-b", self.branch, str(self.worktree), self.base],
            cwd=self.source,
            check=True,
        )
        task_dst = self.worktree / ".tasks" / self.task_source.name
        task_dst.parent.mkdir(parents=True, exist_ok=True)
        task_dst.write_text(self.task_text, encoding="utf-8")
        self.task_rel = task_dst.relative_to(self.worktree).as_posix()
        self.state["task_file"] = self.task_rel
        self.context_dir.mkdir(parents=True, exist_ok=True)
        self.remirror_human_records()
        self.add_local_excludes()
        self.state["phase"] = "worktree"
        self.event("worktree_created")

    def limits(self, key: str) -> Limits:
        raw = self.cfg[key]
        return Limits(int(raw["turns"]), int(raw["tool_calls"]), str(raw["wall_time"]))

    def maybe_unload_previous_model(self, next_model: str) -> None:
        if not self.cfg.get("unload_ollama_between_model_switches", False):
            self.current_model = next_model
            return
        if self.current_model and self.current_model != next_model and shutil.which("ollama"):
            subprocess.run(
                ["ollama", "stop", self.current_model],
                cwd=self.worktree,
                text=True,
                capture_output=True,
                timeout=30,
            )
        self.current_model = next_model

    def qwen_json(
        self,
        label: str,
        prompt: str,
        model: str,
        approval: str,
        limits: Limits,
        schema_name: str,
        read_only: bool,
        excluded_tools_override: str | None = None,
    ) -> dict[str, Any]:
        self.maybe_unload_previous_model(model)
        log_dir = self.controller_dir / "qwen"
        log_dir.mkdir(parents=True, exist_ok=True)
        attempt = 0
        while True:
            attempt += 1
            self.set_active(
                str(self.state.get("active_scope") or "agent"),
                self.state.get("active_microtask"),
                f"qwen:{label}",
            )
            self.safe_point()
            effective_prompt = prompt + self.human_prompt_block()
            index = len(list(log_dir.glob("*.prompt.txt"))) + 1
            stem = f"{index:03d}-{slug(label)}-a{attempt}"
            (log_dir / f"{stem}.prompt.txt").write_text(effective_prompt, encoding="utf-8")

            command = [
                "qwen",
                *(["--experimental-lsp"] if self.cfg.get("enable_lsp", True) else []),
                "-p",
                effective_prompt,
                "--model",
                model,
                "--approval-mode",
                approval,
                "--max-session-turns",
                str(limits.turns),
                "--max-tool-calls",
                str(limits.tool_calls),
                "--max-wall-time",
                limits.wall_time,
                "--json-schema",
                f"@{(SCHEMAS / schema_name).resolve()}",
                "--output-format",
                "text",
            ]
            excluded = excluded_tools_override or (
                "run_shell_command,write_file,edit,agent" if read_only else "agent"
            )
            command.extend(["--exclude-tools", excluded])
            if self.cfg.get("sandbox"):
                command.append("--sandbox")

            try:
                returncode, stdout, stderr, _ = self.controlled_process(
                    command,
                    self.worktree,
                    f"qwen-{label}",
                    env={**os.environ, "OLLAMA_API_KEY": os.environ.get("OLLAMA_API_KEY", "ollama")},
                )
            except HumanRetry:
                continue
            (log_dir / f"{stem}.stdout.txt").write_text(stdout, encoding="utf-8")
            (log_dir / f"{stem}.stderr.txt").write_text(stderr, encoding="utf-8")
            if returncode != 0:
                raise RuntimeError(
                    f"Qwen phase {label} a échoué ({returncode}). Voir {log_dir / f'{stem}.stderr.txt'}"
                )
            try:
                payload = json.loads(stdout)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Sortie JSON invalide pour {label}: {exc}") from exc
            save_json(log_dir / f"{stem}.json", payload)
            self.event("qwen", label=label, model=model, read_only=read_only)
            return payload

    def qwen_coder(self, label: str, prompt: str) -> dict[str, Any]:
        return self.qwen_json(
            label,
            prompt,
            self.cfg["coder_model"],
            self.cfg["coder_approval_mode"],
            self.limits("coder_limits"),
            "coder_result.schema.json",
            read_only=False,
        )

    def qwen_docs(self, label: str, prompt: str) -> dict[str, Any]:
        return self.qwen_json(
            label,
            prompt,
            self.cfg["coder_model"],
            self.cfg["coder_approval_mode"],
            self.limits("coder_limits"),
            "coder_result.schema.json",
            read_only=False,
            excluded_tools_override="run_shell_command,agent",
        )

    def qwen_scout(self, label: str, prompt: str) -> dict[str, Any]:
        return self.qwen_json(
            label,
            prompt,
            self.cfg["scout_model"],
            self.cfg["reviewer_approval_mode"],
            self.limits("scout_limits"),
            "scout.schema.json",
            read_only=True,
        )

    def command_allowed(self, command: str) -> bool:
        normalized = command.strip()
        if not normalized or "\n" in normalized or "\r" in normalized:
            return False
        lower = normalized.lower()
        if any(part in lower for part in DANGEROUS_COMMAND_PARTS):
            return False
        exact = {
            str(value).strip()
            for value in [*self.meta["validation_commands"], *self.meta["full_suite_commands"]]
        }
        if normalized in exact:
            return True
        # Les commandes inventées/corrigées par un petit modèle doivent rester simples.
        # Les pipelines et chaînes complexes ne sont acceptés que s'ils viennent exactement
        # du contrat TASK.md produit par Codex.
        if any(token in normalized for token in DYNAMIC_SHELL_METACHARACTERS):
            return False
        prefixes = [str(value).strip() for value in self.cfg.get("allowed_command_prefixes", [])]
        return any(normalized == prefix or normalized.startswith(prefix + " ") for prefix in prefixes)

    def resolve_commands(
        self,
        original: list[str],
        corrections: list[dict[str, Any]] | None,
        validated: list[str] | None = None,
    ) -> list[str]:
        mapping: dict[str, str] = {}
        for correction in corrections or []:
            old = str(correction.get("original", "")).strip()
            new = str(correction.get("replacement", "")).strip()
            if old in original and self.command_allowed(new):
                mapping[old] = new
        resolved = [mapping.get(str(command).strip(), str(command).strip()) for command in original]
        for command in validated or []:
            command = str(command).strip()
            if command and self.command_allowed(command) and command not in resolved:
                resolved.append(command)
        return list(dict.fromkeys(resolved))

    def plan_policy_errors(self, plan: dict[str, Any]) -> list[str]:
        errors = validate_plan(plan, int(self.cfg["max_microtasks"]))
        for task in plan.get("microtasks", []):
            if len(task.get("likely_files", [])) > int(self.cfg.get("max_files_per_microtask", 6)):
                errors.append(f"{task.get('id', '?')}: trop de fichiers probables")
            if len(task.get("test_commands", [])) > int(self.cfg.get("max_commands_per_microtask", 6)):
                errors.append(f"{task.get('id', '?')}: trop de commandes")
            for command in task.get("test_commands", []):
                if not self.command_allowed(str(command)):
                    errors.append(f"{task.get('id', '?')}: commande refusée: {command}")
        return errors

    def make_plan(
        self,
        prior: dict[str, Any] | None = None,
        issues: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        extra = ""
        if prior is not None:
            prior_path = self.context_json("planning/plan-previous.json", prior)
            issues_path = self.context_json("planning/plan-issues.json", issues or [])
            extra = f"\nRévise `{self.rel(prior_path)}` selon `{self.rel(issues_path)}`."
        context_files = [str(path) for path in self.meta.get("context_files", [])]
        context_rule = (
            "Fichiers de contexte déclarés par Codex: " + ", ".join(f"`{path}`" for path in context_files) + ". "
            if context_files
            else "Codex n'a déclaré aucun fichier de contexte obligatoire. "
        )
        prompt = f"""
Rôle: planner local en lecture seule. Source de vérité: `{self.task_rel}`.
{context_rule}Lis-les seulement s'ils sont utiles à la mission.
Commence par grep/glob ciblés sur les symboles et chemins cités. Ne parcours jamais tout le dépôt.
Lis uniquement de courtes plages autour des définitions, références, imports et tests directs.

Découpe la mission en micro-tâches M1, M2, M3... réellement exécutables par un petit modèle.
Une micro-tâche = un résultat observable, idéalement 1 à 3 fichiers probables, une poignée de symboles,
des invariants, des critères d'acceptation et des tests ciblés. Sépare type/modèle, comportement,
propagation et tests lorsque les preuves peuvent être validées indépendamment.
Ne crée pas de sous-tâches spéculatives et ne recopie pas simplement les paragraphes de Codex.
Les chemins sont des pistes, jamais des limites absolues. Aucun numéro de ligne fragile.
Les commandes doivent être directement exécutables; appelle PowerShell explicitement si nécessaire.
N'écris rien et ne lance aucune commande.{extra}
""".strip()
        return self.qwen_json(
            "planner",
            prompt,
            self.cfg["planner_model"],
            self.cfg["planner_approval_mode"],
            self.limits("planner_limits"),
            "plan.schema.json",
            read_only=True,
        )

    def audit_plan(self, plan_path: Path, label: str, focus: str, model: str) -> dict[str, Any]:
        prompt = f"""
Tu es un auditeur indépendant du plan, en contexte neuf et lecture seule.
Lis `{self.task_rel}`, `{self.rel(plan_path)}` et le vrai dépôt.
Focus prioritaire: {focus}.
Vérifie les relations multifichiers, ordre, dépendances, taille des micro-tâches, invariants,
commandes réellement exécutables sur la plateforme et capacité des tests à détecter une fausse solution.
Le plan peut être correct. N'invente pas de défaut. N'écris rien et ne lance aucune commande.
""".strip()
        return self.qwen_json(
            label,
            prompt,
            model,
            self.cfg["reviewer_approval_mode"],
            self.limits("reviewer_limits"),
            "plan_review.schema.json",
            read_only=True,
        )

    def judge_plan(self, plan_path: Path, audits: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
        audit_paths = [
            self.context_json(f"planning/{slug(name)}.json", payload) for name, payload in audits
        ]
        prompt = f"""
Tu es le juge final du plan en lecture seule. Lis `{self.task_rel}`, `{self.rel(plan_path)}` et les audits
{', '.join(f'`{self.rel(path)}`' for path in audit_paths)}. Vérifie les points dans le dépôt.
Fusionne uniquement les problèmes démontrés, rejette les préférences et doublons.
PASS seulement si le plan est assez petit, cohérent, exécutable et vérifiable par un modèle local faible.
N'écris rien et ne lance aucune commande.
""".strip()
        return self.qwen_json(
            "plan-judge",
            prompt,
            self.cfg["judge_model"],
            self.cfg["reviewer_approval_mode"],
            self.limits("judge_limits"),
            "plan_judge.schema.json",
            read_only=True,
        )

    def establish_plan(self) -> dict[str, Any]:
        prior: dict[str, Any] | None = None
        issues: list[dict[str, Any]] = []
        for attempt in range(int(self.cfg["max_plan_revisions"]) + 1):
            plan = self.make_plan(prior, issues)
            policy = self.plan_policy_errors(plan)
            plan_path = self.context_json("WORK_PLAN.json", plan)
            if policy:
                issues = [
                    {
                        "severity": "critical",
                        "microtask_id": "GLOBAL",
                        "problem": error,
                        "evidence": "validation déterministe du superviseur",
                        "required_correction": "réduire ou corriger la micro-tâche",
                    }
                    for error in policy
                ]
                prior = plan
                continue
            architecture = self.audit_plan(
                plan_path,
                "plan-audit-architecture",
                "architecture, graphe des dépendances, flux et invariants implicites",
                self.cfg["architecture_reviewer_model"],
            )
            execution = self.audit_plan(
                plan_path,
                "plan-audit-execution",
                "micro-tâches vraiment petites, commandes, tests, plateforme et faux positifs",
                self.cfg["execution_reviewer_model"],
            )
            plan_audits: list[tuple[str, dict[str, Any]]] = [
                ("architecture", architecture), ("execution", execution)
            ]
            self.safe_point()
            for directive in self.consume_human_reviews("planning"):
                human_audit = self.audit_plan(
                    plan_path,
                    f"plan-human-review-{directive.get('sequence')}",
                    "contrôle humain obligatoire: " + str(directive.get("message", "")),
                    self.cfg["architecture_reviewer_model"],
                )
                plan_audits.append((f"human-{directive.get('sequence')}", human_audit))
            judgment = self.judge_plan(plan_path, plan_audits)
            if judgment.get("verdict") == "PASS":
                self.context_json("WORK_PLAN.json", plan)
                self.event("plan_accepted", attempt=attempt, dynamic=False)
                return plan
            prior = plan
            issues = list(judgment.get("merged_issues", []))
        raise RuntimeError("Plan non validé après le nombre maximal de révisions")

    def context_manifest(self) -> dict[str, tuple[int, str]]:
        manifest: dict[str, tuple[int, str]] = {}
        if not self.context_dir.exists():
            return manifest
        for path in self.context_dir.rglob("*"):
            if path.is_file():
                rel = path.relative_to(self.context_dir).as_posix()
                if rel == "state.json" or rel.startswith("human/"):
                    continue
                data = path.read_bytes()
                manifest[rel] = (len(data), hashlib.sha256(data).hexdigest())
        return manifest

    def changed_files(self, base_ref: str) -> list[str]:
        tracked = self.capture(["git", "diff", "--name-only", base_ref], self.worktree, False)
        untracked = self.capture(
            ["git", "ls-files", "--others", "--exclude-standard"], self.worktree, False
        )
        return sorted(
            {
                line.strip().replace("\\", "/")
                for blob in (tracked, untracked)
                for line in blob.splitlines()
                if line.strip()
            }
        )

    def diff_text(self, base_ref: str) -> str:
        tracked = self.capture(["git", "diff", "--binary", base_ref], self.worktree, False)
        untracked = self.capture(
            ["git", "ls-files", "--others", "--exclude-standard"], self.worktree, False
        )
        parts = [tracked]
        for rel in untracked.splitlines():
            path = self.worktree / rel
            if path.is_file() and path.stat().st_size < 250_000:
                parts.append(f"\n# UNTRACKED {rel}\n" + path.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(parts)[-180000:]

    def ensure_head_unchanged(self, expected: str) -> None:
        actual = self.capture(["git", "rev-parse", "HEAD"], self.worktree)
        if actual != expected:
            raise RuntimeError(
                "Le codeur a créé/déplacé un commit. " f"HEAD attendu={expected}, observé={actual}"
            )

    def reset_to_checkpoint(self) -> None:
        subprocess.run(["git", "reset", "--hard", self.checkpoint_sha], cwd=self.worktree, check=True)
        subprocess.run(["git", "clean", "-fd"], cwd=self.worktree, check=True)
        self.event("reset_to_checkpoint", sha=self.checkpoint_sha)

    def checkpoint(self, label: str) -> None:
        subprocess.run(["git", "add", "-A"], cwd=self.worktree, check=True)
        staged = self.capture(["git", "diff", "--cached", "--name-only"], self.worktree, False)
        if not staged:
            raise RuntimeError(f"Aucun changement à enregistrer pour le checkpoint {label}")
        subprocess.run(
            ["git", "commit", "-m", f"agent-checkpoint: {label}"],
            cwd=self.worktree,
            check=True,
        )
        self.checkpoint_sha = self.capture(["git", "rev-parse", "HEAD"], self.worktree)
        self.state["checkpoint_commits"].append(self.checkpoint_sha)
        self.event("checkpoint", label=label, sha=self.checkpoint_sha)

    def run_tests(self, commands: list[str], label: str) -> tuple[dict[str, Any], Path]:
        timeout = int(self.meta["command_timeout_seconds"])
        results: list[dict[str, Any]] = []
        for command in list(dict.fromkeys(str(value).strip() for value in commands if str(value).strip())):
            if not self.command_allowed(command):
                results.append(
                    {
                        "command": command,
                        "ok": False,
                        "returncode": 126,
                        "duration_s": 0.0,
                        "output": "Commande refusée par la politique du superviseur",
                        "failure_class": "COMMAND",
                        "shell_family": "policy",
                    }
                )
                continue
            argv, shell_family = shell_command(command)
            while True:
                self.set_active(
                    str(self.state.get("active_scope") or "tests"),
                    self.state.get("active_microtask"),
                    f"test:{label}:{command}",
                )
                try:
                    returncode, stdout, stderr, duration = self.controlled_process(
                        argv,
                        self.worktree,
                        f"test-{label}",
                        env=os.environ.copy(),
                        timeout=timeout,
                    )
                    break
                except HumanRetry:
                    continue
            output = (stdout + "\n" + stderr).strip()
            result = CommandResult(
                command=command,
                ok=returncode == 0,
                returncode=returncode,
                duration_s=round(duration, 3),
                output=output[-24000:],
                failure_class=("ENVIRONMENT" if returncode == 124 else classify_failure(returncode, output)),
                shell_family=shell_family,
            )
            results.append(result_dict(result))
        payload = {
            "label": label,
            "ok": bool(results) and all(result["ok"] for result in results),
            "results": results,
        }
        path = self.context_json(f"tests/{slug(label)}.json", payload)
        self.event("tests", label=label, ok=payload["ok"])
        return payload, path

    def check_forbidden(self, micro: dict[str, Any], start_sha: str, docs_phase: bool = False) -> list[str]:
        changed = self.changed_files(start_sha)
        patterns = [*self.meta["forbidden_paths"], *micro.get("forbidden_changes", []), ".git/**"]
        documentation_paths = [
            str(item["path"]).replace("\\", "/")
            for item in self.meta.get("documentation_updates", [])
        ]
        if not docs_phase:
            patterns.extend(documentation_paths)
        violations: list[str] = []
        for path in changed:
            if path == self.task_rel:
                violations.append(path)
            elif any(fnmatch.fnmatch(path, pattern) for pattern in patterns):
                violations.append(path)
        return violations

    def scout_microtask(
        self,
        micro: dict[str, Any],
        request: dict[str, Any] | None = None,
        prior_packs: list[Path] | None = None,
        round_no: int = 0,
    ) -> Path:
        active = self.context_json(f"active/{slug(micro['id'])}.json", micro)
        prior_text = ""
        if prior_packs:
            prior_text = "\nPaquets déjà disponibles: " + ", ".join(
                f"`{self.rel(path)}`" for path in prior_packs
            )
        request_text = ""
        if request:
            req_path = self.context_json(
                f"research/{slug(micro['id'])}-request-{round_no}.json", request
            )
            request_text = f"\nRéponds précisément à la demande `{self.rel(req_path)}`."
        declared_context = [str(path) for path in self.meta.get("context_files", [])]
        context_hint = (
            " Contexte déclaré par Codex: " + ", ".join(f"`{path}`" for path in declared_context) + "."
            if declared_context
            else ""
        )
        prompt = f"""
Rôle: éclaireur de code en lecture seule. Mission `{self.task_rel}`, micro-tâche `{self.rel(active)}`.
But: donner au codeur un PETIT paquet de contexte prouvé, pas un résumé du dépôt.{context_hint}
Méthode obligatoire: grep/glob ciblé → définition/références/imports/tests directs → lecture par petites plages.
Arrête dès que l'objectif et ses dépendances immédiates sont localisés.
Retourne au plus {int(self.cfg.get('max_scout_files', 10))} fichiers avec symboles et plages utiles,
les relations importantes, tests existants, faits prouvés et inconnues. Aucun conseil de style.
N'écris rien et ne lance aucune commande.{prior_text}{request_text}
""".strip()
        payload = self.qwen_scout(f"scout-{micro['id']}-{round_no}", prompt)
        path = self.context_json(f"scouts/{slug(micro['id'])}-{round_no}.json", payload)
        self.state["scout_packs"].setdefault(micro["id"], []).append(self.rel(path))
        self.event("scout_ready", id=micro["id"], round=round_no, files=len(payload.get("files", [])))
        return path

    def coder_prompt(
        self,
        micro: dict[str, Any],
        evidence_paths: list[Path] | None = None,
        context_paths: list[Path] | None = None,
        global_task: bool = False,
    ) -> str:
        micro_path = self.context_json(f"active/{slug(micro['id'])}.json", micro)
        evidence = ""
        if evidence_paths:
            evidence = "\nPreuves d'échec/revue: " + ", ".join(
                f"`{self.rel(path)}`" for path in evidence_paths
            )
        context = ""
        if context_paths:
            context = "\nPaquets de contexte ciblés: " + ", ".join(
                f"`{self.rel(path)}`" for path in context_paths
            )
        finding_rule = ""
        if micro.get("finding_ids"):
            finding_rule = (
                "\nRéparation de review: lis le repair_ticket et les artefacts sources directement. "
                f"Traite exactement {micro['finding_ids']}. Renseigne addressed_finding_ids et "
                "unresolved_finding_ids; n'invente aucun nouvel objectif."
            )
        return f"""
Rôle: codeur junior autonome. Mission `{self.task_rel}`, micro-tâche `{self.rel(micro_path)}`.
Lis d'abord les paquets ciblés et les preuves. Ne lis ensuite que ce qui manque, jamais tout le dépôt.
Outils: grep_search/glob pour localiser; read_file avec offset/limit pour lire une petite plage;
edit avec un old_string unique contenant le contexte voisin pour modifier un fichier existant;
write_file seulement pour créer un nouveau fichier; run_shell_command pour tests/build.
Boucle LIVE: localiser → lire ciblé → edit minimal → test → stdout/stderr → corriger commande ou code → retester.
Ne remplace jamais un fichier entier lorsqu'un edit ciblé suffit. Une édition ambiguë doit échouer puis être relocalisée.
Erreur de commande = corriger la commande. Erreur de code = corriger le code avec test reproductible.
Sous Windows, appelle PowerShell explicitement. N'affaiblis jamais un test. Ne committe pas.
Si une information manque, retourne CONTEXT avec 1 à 3 recherches précises; le scout répondra.
Choisis PLAN seulement si l'objectif parent ne peut pas être validé sans le décomposer en sous-tâches nécessaires.
{'Réparation globale: limite-toi au défaut démontré.' if global_task else ''}{finding_rule}
{context}{evidence}
Consigne commandes tentées, corrections, commandes validées et findings traités/non résolus.
""".strip()

    def review(
        self,
        micro: dict[str, Any],
        label: str,
        focus: str,
        model: str,
        start_sha: str,
        context_paths: list[Path] | None = None,
    ) -> dict[str, Any]:
        active = self.context_json(f"active/{slug(micro['id'])}.json", micro)
        diff_path = self.context_text(
            f"diffs/{slug(micro['id'])}-{slug(label)}.patch", self.diff_text(start_sha)
        )
        packs = ""
        if context_paths:
            packs = " Paquets ciblés: " + ", ".join(f"`{self.rel(p)}`" for p in context_paths) + "."
        prompt = f"""
Reviewer indépendant, lecture seule. Lis la mission `{self.task_rel}`, la micro-tâche
`{self.rel(active)}`, le diff `{self.rel(diff_path)}` et seulement le code directement relié.{packs}
Focus: {focus}. Le patch peut être correct. Maximum {int(self.cfg.get('max_findings_per_review', 5))} findings.
Pour chaque défaut, conserve un lien explicite avec un critère/invariant de la mission dans mission_anchor,
et donne fichier, symbole, scénario, attendu, observé, preuve et test reproductible.
Aucun conseil de style, aucune reformulation vague. N'écris rien et ne lance aucune commande.
""".strip()
        return self.qwen_json(
            label,
            prompt,
            model,
            self.cfg["reviewer_approval_mode"],
            self.limits("reviewer_limits"),
            "review.schema.json",
            read_only=True,
        )

    def normalize_judgment(
        self,
        micro: dict[str, Any],
        judgment: dict[str, Any],
        start_sha: str,
    ) -> dict[str, Any]:
        changed = set(self.changed_files(start_sha))
        accepted: list[dict[str, Any]] = []
        rejected = list(judgment.get("rejected", []))
        max_findings = int(self.cfg.get("max_findings_per_repair", 4))
        for raw in list(judgment.get("accepted", []))[:max_findings]:
            if not isinstance(raw, dict):
                continue
            file_path = str(raw.get("file", "")).replace("\\", "/").strip()
            path = Path(file_path)
            mission_anchor = str(raw.get("mission_anchor", "")).strip()
            reason = ""
            if not file_path or path.is_absolute() or ".." in path.parts:
                reason = "chemin absent ou dangereux"
            elif not (self.worktree / path).exists() and file_path not in changed:
                reason = "fichier non présent dans le dépôt ni dans le diff"
            elif not str(raw.get("problem", "")).strip():
                reason = "problème vide"
            elif not str(raw.get("evidence", "")).strip():
                reason = "preuve vide"
            elif not str(raw.get("required_test", "")).strip():
                reason = "test requis vide"
            elif self.cfg.get("require_finding_mission_anchor", True) and not mission_anchor:
                reason = "aucun lien avec la mission Codex"
            if reason:
                rejected.append({"source": str(raw.get("source", "judge")), "reason": reason})
                continue
            basis = "|".join(
                str(raw.get(key, "")).strip()
                for key in ("file", "symbol", "mission_anchor", "scenario", "problem", "evidence", "required_test")
            )
            finding_id = "F-" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:10]
            accepted.append({"finding_id": finding_id, **raw})
        return {"accepted": accepted, "rejected": rejected}

    def judge_findings(
        self,
        micro: dict[str, Any],
        reviews: list[tuple[str, dict[str, Any]]],
        start_sha: str,
    ) -> tuple[dict[str, Any], Path, list[Path]]:
        paths = [
            self.context_json(f"reviews/{slug(micro['id'])}-{slug(name)}.json", payload)
            for name, payload in reviews
        ]
        diff_path = self.context_text(
            f"diffs/{slug(micro['id'])}-judge.patch", self.diff_text(start_sha)
        )
        prompt = f"""
Tu es le juge de findings en lecture seule. Lis la mission `{self.task_rel}`, la micro-tâche,
le diff `{self.rel(diff_path)}`, le vrai code et les revues {', '.join(f'`{self.rel(p)}`' for p in paths)}.
Vérifie chaque finding toi-même. Accepte seulement les défauts démontrables et reliés à la mission.
Préserve les données concrètes du finding: fichier, symbole, mission_anchor, scénario, attendu, observé,
preuve et test requis. Ne transforme pas une remarque en conseil vague. Rejette styles, doublons et spéculations.
N'écris rien et ne lance aucune commande.
""".strip()
        raw = self.qwen_json(
            "finding-judge",
            prompt,
            self.cfg["judge_model"],
            self.cfg["reviewer_approval_mode"],
            self.limits("judge_limits"),
            "judge.schema.json",
            read_only=True,
        )
        payload = self.normalize_judgment(micro, raw, start_sha)
        payload["microtask_id"] = micro["id"]
        payload["mission_file"] = self.task_rel
        payload["diff_file"] = self.rel(diff_path)
        payload["source_reviews"] = [self.rel(path) for path in paths]
        path = self.context_json(f"judgments/{slug(micro['id'])}.json", payload)
        return payload, path, paths

    def failure_triage(
        self,
        micro: dict[str, Any],
        coder_result: dict[str, Any],
        tests: dict[str, Any],
        start_sha: str,
    ) -> tuple[dict[str, Any], Path]:
        active = self.context_json(f"active/{slug(micro['id'])}.json", micro)
        coder_path = self.context_json(f"failures/{slug(micro['id'])}-coder.json", coder_result)
        tests_path = self.context_json(f"failures/{slug(micro['id'])}-tests.json", tests)
        diff_path = self.context_text(
            f"failures/{slug(micro['id'])}-diff.patch", self.diff_text(start_sha)
        )
        prompt = f"""
Tu es le planner de récupération, lecture seule, avec vision de toute la mission.
Lis `{self.task_rel}`, le plan global, la micro-tâche `{self.rel(active)}`, le résultat codeur
`{self.rel(coder_path)}`, les tests `{self.rel(tests_path)}`, le diff `{self.rel(diff_path)}` et le vrai dépôt.
Décide précisément:
- FIX_COMMAND si le code peut être bon mais la commande/runner est incorrecte ;
- RETRY_CODE si une petite correction locale suffit ;
- EXPAND_MICROTASK si l'objectif parent ne peut être validé sans le décomposer en sous-tâches nécessaires ;
- ENVIRONMENT_BLOCKED si dépendance/service/credential externe empêche objectivement le travail.
Ne choisis pas EXPAND_MICROTASK pour une simple erreur de syntaxe, de commande ou un test rouge local.
Pour RETRY_CODE, fournis une seule micro-tâche de réparation petite et testable.
Pour FIX_COMMAND, fournis les remplacements exacts, compatibles avec cmd.exe ou appel PowerShell explicite.
N'écris rien et ne lance aucune commande.
""".strip()
        payload = self.qwen_json(
            "failure-triage",
            prompt,
            self.cfg["planner_model"],
            self.cfg["planner_approval_mode"],
            self.limits("planner_limits"),
            "failure_triage.schema.json",
            read_only=True,
        )
        path = self.context_json(f"failures/{slug(micro['id'])}-triage.json", payload)
        self.last_failure_path = path
        return payload, path

    def finding_recurrence_exceeded(
        self, micro_id: str, judgment: dict[str, Any]
    ) -> tuple[bool, list[str]]:
        history = self.state["finding_history"].setdefault(micro_id, {})
        repeated: list[str] = []
        limit = int(self.cfg.get("max_same_finding_recurrence", 1))
        for finding in judgment.get("accepted", []):
            finding_id = str(finding.get("finding_id", ""))
            if not finding_id:
                continue
            history[finding_id] = int(history.get(finding_id, 0)) + 1
            if history[finding_id] > limit:
                repeated.append(finding_id)
        self.save_state()
        return bool(repeated), repeated

    def repair_from_findings(
        self,
        parent: dict[str, Any],
        judgment_path: Path,
        review_paths: list[Path],
        tests_path: Path,
        round_no: int,
    ) -> tuple[dict[str, Any], Path, dict[str, Any]]:
        judgment = load_json(judgment_path)
        accepted = list(judgment.get("accepted", []))
        finding_ids = [str(item["finding_id"]) for item in accepted]
        files = list(dict.fromkeys(
            [str(item.get("file", "")) for item in accepted if item.get("file")]
            + list(parent.get("likely_files", []))
        ))[: int(self.cfg.get("max_files_per_microtask", 6))]
        symbols = list(dict.fromkeys(
            [str(item.get("symbol", "")) for item in accepted if item.get("symbol")]
            + list(parent.get("symbols", []))
        ))[:12]
        acceptance = [
            f"{item['finding_id']}: {item['problem']} — preuve attendue: {item['required_test']}"
            for item in accepted
        ][:10]
        ticket = {
            "ticket_id": f"{parent['id']}-REVIEW-R{round_no}",
            "parent_microtask_id": parent["id"],
            "mission_file": self.task_rel,
            "source_microtask": parent,
            "accepted_findings": accepted,
            "source_artifacts": {
                "judgment": self.rel(judgment_path),
                "reviews": [self.rel(path) for path in review_paths],
                "tests": self.rel(tests_path),
            },
            "rules": [
                "Traiter uniquement les finding_id listés.",
                "Lire les artefacts sources directement; ne pas se fier à une paraphrase.",
                "Ajouter ou renforcer le test reproductible demandé avant de déclarer le finding résolu.",
                "Ne pas affaiblir un test existant et ne pas modifier un comportement hors mission.",
            ],
        }
        ticket_path = self.context_json(
            f"repairs/{slug(parent['id'])}-round-{round_no}.json", ticket
        )
        repair = {
            "id": ticket["ticket_id"],
            "kind": "implementation",
            "depends_on": [],
            "title": f"Réparer {', '.join(finding_ids)}",
            "goal": "Résoudre exactement les findings acceptés du ticket sans élargir le périmètre",
            "likely_files": files,
            "symbols": symbols,
            "invariants": list(parent.get("invariants", [])),
            "acceptance": acceptance or ["Tous les findings du ticket sont démontrés comme résolus"],
            "test_commands": list(parent.get("test_commands", [])),
            "forbidden_changes": list(parent.get("forbidden_changes", [])),
            "finding_ids": finding_ids,
            "repair_ticket": self.rel(ticket_path),
        }
        scout_request = {
            "type": "review_repair",
            "repair_ticket": self.rel(ticket_path),
            "finding_ids": finding_ids,
            "requests": [
                {
                    "question": f"Localiser la cause et les appels directement liés à {item['finding_id']}: {item['problem']}",
                    "symbols": [item.get("symbol", "")] if item.get("symbol") else [],
                    "suspected_paths": [item.get("file", "")] if item.get("file") else [],
                    "reason": item.get("evidence", ""),
                }
                for item in accepted
            ],
        }
        return repair, ticket_path, scout_request

    def write_microtask_proof(
        self,
        micro: dict[str, Any],
        start_sha: str,
        tests_path: Path,
        judgment_path: Path,
        context_paths: list[Path],
    ) -> Path:
        diff = self.diff_text(start_sha)
        proof = {
            "microtask_id": micro["id"],
            "mission_file": self.task_rel,
            "start_sha": start_sha,
            "diff_sha256": hashlib.sha256(diff.encode("utf-8")).hexdigest(),
            "changed_files": self.changed_files(start_sha),
            "tests": self.rel(tests_path),
            "judgment": self.rel(judgment_path),
            "context_packs": [self.rel(path) for path in context_paths],
            "status": "verified",
        }
        path = self.context_json(f"proofs/{slug(micro['id'])}.json", proof)
        self.state["microtask_proofs"][micro["id"]] = self.rel(path)
        self.save_state()
        return path

    def execute_microtask(self, micro: dict[str, Any], track_status: bool = True) -> str:
        start_sha = self.checkpoint_sha
        if track_status:
            self.state["microtask_status"][micro["id"]] = "in_progress"
        self.event("microtask_start", id=micro["id"], start_sha=start_sha)
        active = micro
        evidence_paths: list[Path] = []
        context_paths: list[Path] = [self.scout_microtask(micro, round_no=0)]
        context_requests = 0
        repair_count = 0
        commands = list(micro.get("test_commands", []))
        max_rounds = (
            int(self.cfg["max_repairs_per_microtask"])
            + int(self.cfg.get("max_context_requests_per_microtask", 2))
            + 1
        )

        for round_no in range(max_rounds):
            coder_prompt = self.coder_prompt(active, evidence_paths, context_paths, not track_status)
            context_before = self.context_manifest()
            result = self.qwen_coder(f"coder-{active['id']}", coder_prompt)
            context_after = self.context_manifest()
            if context_after != context_before:
                changed_controls = sorted(
                    key for key in set(context_before) | set(context_after)
                    if context_before.get(key) != context_after.get(key)
                )
                raise RuntimeError(
                    "Le codeur a modifié les fichiers de contrôle dans .agent-context: "
                    + ", ".join(changed_controls)
                )
            expected_findings = set(str(x) for x in active.get("finding_ids", []))
            if expected_findings:
                addressed = set(str(x) for x in result.get("addressed_finding_ids", []))
                unresolved_ids = set(str(x) for x in result.get("unresolved_finding_ids", []))
                missing_ids = sorted(expected_findings - addressed)
                if missing_ids or unresolved_ids:
                    result["status"] = "BLOCKED"
                    result["escalation"] = "CODE"
                    result.setdefault("unresolved", []).append(
                        "Findings non déclarés résolus: " + ", ".join(sorted(set(missing_ids) | unresolved_ids))
                    )
            result_path = self.context_json(f"coder/{slug(active['id'])}.json", result)
            self.ensure_head_unchanged(start_sha)

            requests = list(result.get("research_requests", []))
            if result.get("escalation") == "CONTEXT" or requests:
                if context_requests < int(self.cfg.get("max_context_requests_per_microtask", 2)):
                    request = {
                        "microtask_id": micro["id"],
                        "requests": requests or [{
                            "question": "Quelle information précise manque pour terminer ?",
                            "symbols": active.get("symbols", []),
                            "suspected_paths": active.get("likely_files", []),
                            "reason": "Demande de contexte du codeur."
                        }],
                    }
                    context_requests += 1
                    self.state["context_requests"][micro["id"]] = context_requests
                    self.event("context_requested", id=micro["id"], round=context_requests)
                    context_paths.append(self.scout_microtask(
                        micro,
                        request=request,
                        prior_packs=context_paths,
                        round_no=context_requests,
                    ))
                    evidence_paths = [result_path]
                    continue
                evidence_paths = [result_path]

            violations = self.check_forbidden(active, start_sha)
            changed_now = self.changed_files(start_sha)
            max_changed = int(self.cfg.get("max_changed_files_per_microtask", 8))
            if len(changed_now) > max_changed:
                scope_path = self.context_json(
                    f"violations/{slug(active['id'])}-scope.json",
                    {
                        "problem": "micro-tâche trop large",
                        "changed_files": changed_now,
                        "max_changed_files": max_changed,
                        "required_action": "revenir au checkpoint et replanifier en tâches plus petites",
                    },
                )
                self.last_failure_path = scope_path
                self.reset_to_checkpoint()
                if track_status:
                    self.state["microtask_status"][micro["id"]] = "pending"
                self.event("microtask_requests_replan", id=micro["id"], reason="scope-too-large")
                return "replan"
            if violations:
                violation_path = self.context_json(
                    f"violations/{slug(active['id'])}.json", {"forbidden_paths_touched": violations}
                )
                evidence_paths = [violation_path]
                if repair_count >= int(self.cfg["max_repairs_per_microtask"]):
                    break
                repair_count += 1
                active = {
                    **active,
                    "id": f"{micro['id']}-FORBIDDEN-R{round_no + 1}",
                    "goal": active["goal"] + "; annuler les modifications interdites et rester dans le périmètre",
                }
                continue

            commands = self.resolve_commands(
                commands,
                result.get("command_corrections", []),
                result.get("validated_commands", []),
            )
            tests, tests_path = self.run_tests(commands, f"{micro['id']}-round-{round_no}")

            can_review = tests["ok"] and (
                result.get("status") == "DONE" and result.get("escalation") in ("NONE", "COMMAND")
            )
            if not can_review:
                triage, triage_path = self.failure_triage(micro, result, tests, start_sha)
                decision = triage.get("decision")
                if decision == "FIX_COMMAND":
                    commands = self.resolve_commands(commands, triage.get("corrected_commands", []))
                    retry_tests, retry_path = self.run_tests(
                        commands, f"{micro['id']}-command-fix-{round_no}"
                    )
                    if retry_tests["ok"]:
                        tests = retry_tests
                        tests_path = retry_path
                        can_review = True
                    else:
                        evidence_paths = [triage_path, retry_path, result_path]
                elif decision == "RETRY_CODE" and triage.get("repair_task"):
                    if repair_count >= int(self.cfg["max_repairs_per_microtask"]):
                        break
                    repair_count += 1
                    repair = triage["repair_task"]
                    active = {
                        "id": f"{micro['id']}-CODE-R{round_no + 1}",
                        "kind": "implementation",
                        "depends_on": [],
                        **repair,
                    }
                    commands = list(dict.fromkeys([*commands, *active.get("test_commands", [])]))
                    evidence_paths = [triage_path, tests_path, result_path]
                    continue
                elif decision == "EXPAND_MICROTASK":
                    self.reset_to_checkpoint()
                    if track_status:
                        self.state["microtask_status"][micro["id"]] = "pending"
                    self.event("microtask_requests_replan", id=micro["id"])
                    return "replan"
                else:
                    if track_status:
                        self.state["microtask_status"][micro["id"]] = "blocked"
                    self.event("microtask_environment_blocked", id=micro["id"])
                    return "blocked"

            if not can_review:
                if repair_count >= int(self.cfg["max_repairs_per_microtask"]):
                    break
                continue

            logic = self.review(
                micro,
                "logic-review",
                "logique, invariants, sécurité, concurrence, erreurs et flux multifichiers",
                self.cfg["architecture_reviewer_model"],
                start_sha,
                context_paths,
            )
            integration = self.review(
                micro,
                "integration-review",
                "intégration, contrats, plateforme, couverture des tests et risque de faux vert",
                self.cfg["execution_reviewer_model"],
                start_sha,
                context_paths,
            )
            reviews: list[tuple[str, dict[str, Any]]] = [("logic", logic), ("integration", integration)]
            self.safe_point()
            for directive in self.consume_human_reviews(str(micro["id"])):
                human_review = self.review(
                    micro,
                    f"human-review-{directive.get('sequence')}",
                    "contrôle humain obligatoire: " + str(directive.get("message", "")),
                    self.cfg["architecture_reviewer_model"],
                    start_sha,
                    context_paths,
                )
                reviews.append((f"human-{directive.get('sequence')}", human_review))
            judgment, judgment_path, review_paths = self.judge_findings(
                micro, reviews, start_sha
            )
            if judgment.get("accepted"):
                repeated, repeated_ids = self.finding_recurrence_exceeded(micro["id"], judgment)
                if repeated:
                    repeat_path = self.context_json(
                        f"failures/{slug(micro['id'])}-repeated-findings.json",
                        {
                            "problem": "les mêmes findings ont survécu à une réparation",
                            "finding_ids": repeated_ids,
                            "judgment": self.rel(judgment_path),
                            "required_action": "revenir au checkpoint et replanifier le périmètre restant",
                        },
                    )
                    self.last_failure_path = repeat_path
                    self.reset_to_checkpoint()
                    if track_status:
                        self.state["microtask_status"][micro["id"]] = "pending"
                    self.event("microtask_requests_replan", id=micro["id"], reason="repeated-finding")
                    return "replan"
                if repair_count >= int(self.cfg["max_repairs_per_microtask"]):
                    evidence_paths = [judgment_path, *review_paths]
                    break
                repair_count += 1
                active, ticket_path, scout_request = self.repair_from_findings(
                    micro, judgment_path, review_paths, tests_path, repair_count
                )
                repair_scout = self.scout_microtask(
                    micro,
                    request=scout_request,
                    prior_packs=context_paths,
                    round_no=100 + repair_count,
                )
                context_paths.append(repair_scout)
                commands = list(dict.fromkeys([*commands, *active.get("test_commands", [])]))
                evidence_paths = [ticket_path, judgment_path, *review_paths, tests_path]
                continue

            self.safe_point()
            late_reviews = self.consume_human_reviews(str(micro["id"]))
            if late_reviews:
                for directive in late_reviews:
                    human_review = self.review(
                        micro,
                        f"human-late-review-{directive.get('sequence')}",
                        "contrôle humain obligatoire avant checkpoint: " + str(directive.get("message", "")),
                        self.cfg["architecture_reviewer_model"],
                        start_sha,
                        context_paths,
                    )
                    reviews.append((f"human-late-{directive.get('sequence')}", human_review))
                judgment, judgment_path, review_paths = self.judge_findings(micro, reviews, start_sha)
                if judgment.get("accepted"):
                    if repair_count >= int(self.cfg["max_repairs_per_microtask"]):
                        evidence_paths = [judgment_path, *review_paths]
                        break
                    repair_count += 1
                    active, ticket_path, scout_request = self.repair_from_findings(
                        micro, judgment_path, review_paths, tests_path, repair_count
                    )
                    context_paths.append(self.scout_microtask(
                        micro, request=scout_request, prior_packs=context_paths, round_no=150 + repair_count
                    ))
                    commands = list(dict.fromkeys([*commands, *active.get("test_commands", [])]))
                    evidence_paths = [ticket_path, judgment_path, *review_paths, tests_path]
                    continue

            if not self.changed_files(start_sha):
                evidence_paths = [
                    self.context_json(
                        f"violations/{slug(micro['id'])}-no-change.json",
                        {"problem": "aucun fichier du dépôt n'a été modifié"},
                    )
                ]
                continue

            self.write_microtask_proof(
                micro, start_sha, tests_path, judgment_path, context_paths
            )
            self.checkpoint(micro["id"])
            if track_status:
                self.state["microtask_status"][micro["id"]] = "done"
            self.event("microtask_done", id=micro["id"], round=round_no)
            return "done"

        self.reset_to_checkpoint()
        if track_status:
            self.state["microtask_status"][micro["id"]] = "failed"
        self.event("microtask_failed", id=micro["id"])
        return "blocked"

    def make_expansion(
        self,
        plan: dict[str, Any],
        parent: dict[str, Any],
        prior: dict[str, Any] | None = None,
        issues: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        if not self.last_failure_path:
            raise RuntimeError("Expansion demandée sans preuve d'échec")
        plan_path = self.context_json("planning/expansion-current-plan.json", plan)
        parent_path = self.context_json(
            f"planning/expansion-parent-{slug(parent['id'])}.json", parent
        )
        extra = ""
        if prior is not None:
            prior_path = self.context_json(
                f"planning/expansion-previous-{slug(parent['id'])}.json", prior
            )
            issues_path = self.context_json(
                f"planning/expansion-issues-{slug(parent['id'])}.json", issues or []
            )
            extra = f" Révise `{self.rel(prior_path)}` selon `{self.rel(issues_path)}`."
        next_depth = int(parent.get("expansion_depth", 0)) + 1
        prompt = f"""
Rôle: planner de décomposition en lecture seule. Mission `{self.task_rel}`.
Plan actuel `{self.rel(plan_path)}`. Parent échoué `{self.rel(parent_path)}`.
Preuve d'échec `{self.rel(self.last_failure_path)}`.

Tu ne peux PAS réécrire le plan global. Décompose uniquement `{parent['id']}` en au plus
{int(self.cfg.get('max_children_per_expansion', 4))} enfants nécessaires à la validation de ce parent.
Exemples d'ids: `{parent['id']}a`, `{parent['id']}b`. Profondeur obligatoire: {next_depth}.
Chaque enfant doit expliquer `necessity_for_parent` et citer exactement les critères du parent qu'il couvre
dans `parent_acceptance_covered`. L'union des enfants doit couvrir tous les critères du parent.
Les dépendances peuvent viser seulement les dépendances originales du parent ou un enfant du fragment.
N'ajoute aucune amélioration facultative, documentation, refactor opportuniste ou objectif nouveau.
Un test rouge local ou une erreur de commande doit rester une réparation, pas une décomposition.
N'écris rien et ne lance aucune commande.{extra}
""".strip()
        return self.qwen_json(
            f"expander-{parent['id']}",
            prompt,
            self.cfg["planner_model"],
            self.cfg["planner_approval_mode"],
            self.limits("planner_limits"),
            "expansion.schema.json",
            read_only=True,
        )

    def establish_expansion(
        self, plan: dict[str, Any], parent_id: str
    ) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        parent = plan_task_map(plan).get(parent_id)
        if parent is None:
            raise RuntimeError(f"Micro-tâche à décomposer absente: {parent_id}")
        prior: dict[str, Any] | None = None
        issues: list[dict[str, Any]] = []
        revisions = int(self.cfg.get("max_expansion_revisions", 1))
        for attempt in range(revisions + 1):
            fragment = self.make_expansion(plan, parent, prior, issues)
            errors = validate_expansion_fragment(
                plan,
                parent_id,
                fragment,
                int(self.cfg.get("max_children_per_expansion", 4)),
                int(self.cfg.get("max_expansion_depth", 2)),
            )
            merged: dict[str, Any] | None = None
            leaves: list[str] = []
            if not errors:
                merged, leaves = apply_expansion_fragment(plan, parent_id, fragment)
                errors.extend(self.plan_policy_errors(merged))
                added_total = sum(
                    len(item.get("children", []))
                    for item in self.state.get("expansions", {}).values()
                ) + len(fragment.get("children", []))
                if added_total > int(self.cfg.get("max_added_microtasks_total", 8)):
                    errors.append(
                        f"budget total d'enfants dépassé: {added_total} > "
                        f"{int(self.cfg.get('max_added_microtasks_total', 8))}"
                    )
            fragment_path = self.context_json(
                f"planning/expansion-{slug(parent_id)}-attempt-{attempt}.json", fragment
            )
            if errors:
                issues = [
                    {
                        "severity": "critical",
                        "microtask_id": parent_id,
                        "problem": error,
                        "evidence": "validation déterministe du fragment",
                        "required_correction": "ne garder que les enfants nécessaires au parent",
                    }
                    for error in errors
                ]
                prior = fragment
                continue
            assert merged is not None
            candidate_path = self.context_json(
                f"planning/expansion-{slug(parent_id)}-candidate.json", merged
            )
            architecture = self.audit_plan(
                candidate_path,
                f"expansion-{parent_id}-architecture",
                f"vérifier que les enfants remplacent uniquement {parent_id}, sont nécessaires et couvrent ses critères",
                self.cfg["architecture_reviewer_model"],
            )
            execution = self.audit_plan(
                candidate_path,
                f"expansion-{parent_id}-execution",
                "taille des enfants, ordre, tests ciblés et absence de tâches spéculatives",
                self.cfg["execution_reviewer_model"],
            )
            judgment = self.judge_plan(
                candidate_path, [("architecture", architecture), ("execution", execution)]
            )
            if judgment.get("verdict") == "PASS":
                self.context_json("WORK_PLAN.json", merged)
                self.context_json(
                    f"planning/expansion-{slug(parent_id)}-accepted.json",
                    {"fragment": self.rel(fragment_path), "leaves": leaves},
                )
                return merged, fragment, leaves
            prior = fragment
            issues = list(judgment.get("merged_issues", []))
        raise RuntimeError(f"Décomposition de {parent_id} non validée")

    def dynamic_replan(self, old_plan: dict[str, Any], failed_id: str) -> dict[str, Any]:
        if self.state["dynamic_replans"] >= int(self.cfg["max_dynamic_replans"]):
            raise RuntimeError("Nombre maximal de décompositions dynamiques atteint")
        completed = {
            task_id
            for task_id, status in self.state["microtask_status"].items()
            if status == "done"
        }
        old_map = plan_task_map(old_plan)
        new_plan, fragment, leaves = self.establish_expansion(old_plan, failed_id)
        new_map = plan_task_map(new_plan)
        altered_done = [
            task_id
            for task_id in completed
            if task_id not in new_map or new_map[task_id] != old_map.get(task_id)
        ]
        if altered_done:
            raise RuntimeError(f"La décomposition a altéré des tâches terminées: {altered_done}")

        statuses = dict(self.state["microtask_status"])
        statuses[failed_id] = "expanded"
        for child in fragment["children"]:
            statuses[str(child["id"])] = "pending"
        self.state["microtask_status"] = statuses
        self.state["expansions"][failed_id] = {
            "reason": fragment.get("replan_reason", ""),
            "children": [str(child["id"]) for child in fragment["children"]],
            "leaves": leaves,
        }
        self.state["dynamic_replans"] += 1
        self.event(
            "microtask_expansion_accepted",
            parent=failed_id,
            children=self.state["expansions"][failed_id]["children"],
        )
        return new_plan

    def global_validation(self) -> None:
        commands = [*self.meta["validation_commands"], *self.meta["full_suite_commands"]]
        for cycle in range(int(self.cfg["max_global_repair_cycles"]) + 1):
            tests, tests_path = self.run_tests(commands, f"global-{cycle}")
            synthetic = {
                "id": "GLOBAL",
                "title": "Validation globale",
                "kind": "integration",
                "goal": "Valider toutes les interactions de la mission complète",
                "depends_on": [],
                "likely_files": [],
                "symbols": [],
                "invariants": [],
                "acceptance": ["toutes les commandes globales passent"],
                "test_commands": commands,
                "forbidden_changes": [],
            }
            start_sha = self.checkpoint_sha
            global_context = self.context_json(
                f"final/global-review-context-{cycle}.json",
                {
                    "mission_file": self.task_rel,
                    "work_plan": self.rel(self.context_dir / "WORK_PLAN.json"),
                    "microtask_status": self.state.get("microtask_status", {}),
                    "microtask_proofs": self.state.get("microtask_proofs", {}),
                    "expansions": self.state.get("expansions", {}),
                    "tests": self.rel(tests_path),
                },
            )
            context_paths: list[Path] = [global_context]
            if tests["ok"]:
                logic = self.review(
                    synthetic,
                    "global-architecture-review",
                    "cohérence globale, invariants, régressions et relations entre toutes les micro-tâches",
                    self.cfg["architecture_reviewer_model"],
                    self.base,
                    context_paths,
                )
                integration = self.review(
                    synthetic,
                    "global-execution-review",
                    "tests, build, compatibilité, plateforme et faux verts",
                    self.cfg["execution_reviewer_model"],
                    self.base,
                    context_paths,
                )
                global_reviews: list[tuple[str, dict[str, Any]]] = [
                    ("architecture", logic), ("execution", integration)
                ]
                self.safe_point()
                for directive in self.consume_human_reviews("global"):
                    human_review = self.review(
                        synthetic,
                        f"global-human-review-{directive.get('sequence')}",
                        "contrôle humain global obligatoire: " + str(directive.get("message", "")),
                        self.cfg["architecture_reviewer_model"],
                        self.base,
                        context_paths,
                    )
                    global_reviews.append((f"human-{directive.get('sequence')}", human_review))
                judgment, judgment_path, review_paths = self.judge_findings(
                    synthetic, global_reviews, self.base
                )
                self.safe_point()
                late_global_reviews = self.consume_human_reviews("global")
                if late_global_reviews:
                    for directive in late_global_reviews:
                        human_review = self.review(
                            synthetic,
                            f"global-human-late-review-{directive.get('sequence')}",
                            "contrôle humain global obligatoire avant validation: "
                            + str(directive.get("message", "")),
                            self.cfg["architecture_reviewer_model"],
                            self.base,
                            context_paths,
                        )
                        global_reviews.append((f"human-late-{directive.get('sequence')}", human_review))
                    judgment, judgment_path, review_paths = self.judge_findings(
                        synthetic, global_reviews, self.base
                    )
                if not judgment.get("accepted"):
                    self.event("global_validation_pass", cycle=cycle)
                    return
                repair, ticket_path, scout_request = self.repair_from_findings(
                    synthetic, judgment_path, review_paths, tests_path, cycle + 1
                )
                context_paths.append(
                    self.scout_microtask(
                        synthetic, request=scout_request, round_no=200 + cycle
                    )
                )
                evidence = [ticket_path, judgment_path, *review_paths, tests_path]
            else:
                repair = {
                    "id": f"GLOBAL-TEST-REPAIR-{cycle + 1}",
                    "title": "Réparer les tests globaux",
                    "kind": "integration",
                    "goal": "Résoudre uniquement les échecs globaux démontrés",
                    "depends_on": [],
                    "likely_files": [],
                    "symbols": [],
                    "invariants": [],
                    "acceptance": ["suite globale passe sans affaiblir les tests"],
                    "test_commands": commands,
                    "forbidden_changes": [],
                }
                evidence = [tests_path]

            if cycle >= int(self.cfg["max_global_repair_cycles"]):
                break
            result = self.qwen_coder(
                repair["id"], self.coder_prompt(repair, evidence, context_paths, global_task=True)
            )
            self.context_json(f"coder/{slug(repair['id'])}.json", result)
            self.ensure_head_unchanged(start_sha)
            if self.check_forbidden(repair, start_sha):
                self.reset_to_checkpoint()
                raise RuntimeError("La réparation globale a modifié un chemin interdit")
            retest, _ = self.run_tests(commands, f"global-repair-{cycle + 1}")
            if retest["ok"] and self.changed_files(start_sha):
                self.checkpoint(repair["id"])
        raise RuntimeError("Validation globale non obtenue")

    def finalize_docs(self) -> None:
        updates = list(self.meta.get("documentation_updates", []))
        if not updates:
            self.event("documentation_skipped", reason="aucune mise à jour demandée par Codex")
            return

        summary_path = self.context_json(
            "final/task-summary.json",
            {
                "task_id": self.task_id,
                "completed_microtasks": [
                    task_id
                    for task_id, status in self.state["microtask_status"].items()
                    if status in {"done", "expanded"}
                ],
                "expansions": self.state.get("expansions", {}),
                "commands": [*self.meta["validation_commands"], *self.meta["full_suite_commands"]],
                "base_sha": self.base,
                "head_sha": self.checkpoint_sha,
                "documentation_updates": updates,
            },
        )
        instructions_path = self.context_json("final/documentation-updates.json", updates)
        allowed = {str(item["path"]).replace("\\", "/") for item in updates}
        prompt = f"""
Tu es le finaliseur documentaire. Lis la mission `{self.task_rel}`, le résumé
`{self.rel(summary_path)}` et les instructions exactes `{self.rel(instructions_path)}`.
Tu peux modifier UNIQUEMENT les chemins listés dans ce fichier.
Applique chaque instruction sans inventer d'autre document, section ou rapport.
Respecte les marqueurs demandés. Si allow_create=false, le fichier doit déjà exister.
Utilise des edits ciblés pour les fichiers existants. Ne lance aucune commande et ne committe pas.
""".strip()
        start_sha = self.checkpoint_sha
        existed_before = {
            str(item["path"]).replace("\\", "/"): (self.worktree / str(item["path"])).exists()
            for item in updates
        }
        before = self.context_manifest()
        result = self.qwen_docs("documentation-finalizer", prompt)
        after = self.context_manifest()
        if before != after:
            raise RuntimeError("Le finaliseur a modifié les fichiers de contrôle")
        self.context_json("final/documentation-result.json", result)
        changed = self.changed_files(start_sha)
        if any(path not in allowed for path in changed):
            raise RuntimeError(f"Finalisation documentaire hors périmètre: {changed}")

        for item in updates:
            rel = str(item["path"]).replace("\\", "/")
            path = self.worktree / rel
            if not path.exists():
                raise RuntimeError(f"Document demandé absent après finalisation: {rel}")
            if not item.get("allow_create", False) and not existed_before.get(rel, False):
                raise RuntimeError(f"Création interdite par la mission pour: {rel}")
            if item.get("must_change", True) and rel not in changed:
                raise RuntimeError(f"Document demandé non modifié: {rel}")
            text = path.read_text(encoding="utf-8", errors="replace")
            missing = [
                marker for marker in item.get("required_markers", []) if marker not in text
            ]
            if missing:
                raise RuntimeError(f"Marqueurs absents de {rel}: {missing}")

        doc_repair_count = 0
        while True:
            self.safe_point()
            directives = self.consume_human_reviews("documentation")
            if not directives:
                break
            synthetic = {
                "id": "DOCUMENTATION",
                "title": "Validation documentaire demandée par l'humain",
                "kind": "documentation",
                "goal": "Vérifier uniquement les mises à jour documentaires déclarées par Codex",
                "depends_on": [],
                "likely_files": sorted(allowed),
                "symbols": [],
                "invariants": [],
                "acceptance": [str(item.get("instruction", "")) for item in updates],
                "test_commands": [],
                "forbidden_changes": [],
            }
            reviews: list[tuple[str, dict[str, Any]]] = []
            for directive in directives:
                payload = self.review(
                    synthetic,
                    f"documentation-human-review-{directive.get('sequence')}",
                    "contrôle documentaire humain obligatoire: " + str(directive.get("message", "")),
                    self.cfg["architecture_reviewer_model"],
                    start_sha,
                    [instructions_path, summary_path],
                )
                reviews.append((f"human-{directive.get('sequence')}", payload))
            judgment, judgment_path, review_paths = self.judge_findings(
                synthetic, reviews, start_sha
            )
            if not judgment.get("accepted"):
                continue
            doc_repair_count += 1
            maximum = int(self.cfg.get("max_human_doc_review_repairs", 1))
            if doc_repair_count > maximum:
                raise RuntimeError("La revue humaine documentaire reste en échec après correction")
            repair_prompt = f"""
Tu es le finaliseur documentaire chargé d'une correction obligatoire. Lis la mission `{self.task_rel}`,
les instructions `{self.rel(instructions_path)}`, le jugement `{self.rel(judgment_path)}` et les revues
{', '.join(f'`{self.rel(path)}`' for path in review_paths)}.
Corrige exactement les findings acceptés, uniquement dans: {', '.join(sorted(allowed))}.
Respecte allow_create, must_change et required_markers. Utilise des edits ciblés.
Ne lance aucune commande, ne modifie aucun autre fichier et ne committe pas.
""".strip()
            before_repair = self.context_manifest()
            result = self.qwen_docs("documentation-human-repair", repair_prompt)
            after_repair = self.context_manifest()
            if before_repair != after_repair:
                raise RuntimeError("La correction documentaire a modifié les fichiers de contrôle")
            self.context_json("final/documentation-human-repair-result.json", result)
            repaired_changed = self.changed_files(start_sha)
            if any(path not in allowed for path in repaired_changed):
                raise RuntimeError(f"Correction documentaire hors périmètre: {repaired_changed}")
            for item in updates:
                rel = str(item["path"]).replace("\\", "/")
                path = self.worktree / rel
                if not path.exists():
                    raise RuntimeError(f"Document demandé absent après correction: {rel}")
                text = path.read_text(encoding="utf-8", errors="replace")
                missing = [marker for marker in item.get("required_markers", []) if marker not in text]
                if missing:
                    raise RuntimeError(f"Marqueurs absents de {rel} après correction: {missing}")
            changed = repaired_changed

        if changed:
            self.checkpoint("documentation")

    def deterministic_final_checks(self) -> None:
        commands = [*self.meta["validation_commands"], *self.meta["full_suite_commands"]]
        tests, _ = self.run_tests(commands, "final-deterministic")
        if not tests["ok"]:
            raise RuntimeError("Les validations finales ont échoué après la documentation")
        changed = self.changed_files(self.base)
        if self.meta.get("require_test_changes"):
            globs = self.meta.get("test_file_globs", [])
            if not any(any(fnmatch.fnmatch(path, pattern) for pattern in globs) for path in changed):
                raise RuntimeError("Aucun fichier de test modifié alors que require_test_changes=true")
        if not changed:
            raise RuntimeError("Aucun changement final")

    def squash_commit(self) -> None:
        if not self.cfg.get("auto_commit", True):
            return
        subprocess.run(["git", "reset", "--soft", self.base], cwd=self.worktree, check=True)
        staged = self.capture(["git", "diff", "--cached", "--name-only"], self.worktree, False)
        if not staged:
            raise RuntimeError("Aucun changement staged pour le commit final")
        message = self.meta.get("commit_message") or f"feat: complete {self.task_id}"
        subprocess.run(["git", "commit", "-m", str(message)], cwd=self.worktree, check=True)
        self.checkpoint_sha = self.capture(["git", "rev-parse", "HEAD"], self.worktree)
        count = self.capture(["git", "rev-list", "--count", f"{self.base}..HEAD"], self.worktree)
        if count != "1":
            raise RuntimeError(f"Le squash final n'a pas produit un commit unique: {count}")
        self.state["commit_sha"] = self.checkpoint_sha
        self.event("final_commit", sha=self.checkpoint_sha)

    def final_human_review_gate(self) -> bool:
        directives = self.consume_human_reviews("global")
        if not directives:
            return False
        commands = [*self.meta["validation_commands"], *self.meta["full_suite_commands"]]
        tests, tests_path = self.run_tests(commands, "human-final-review")
        if not tests["ok"]:
            raise RuntimeError("Les tests ont échoué pendant la revue humaine finale")
        synthetic = {
            "id": "HUMAN-FINAL",
            "title": "Revue humaine finale obligatoire",
            "kind": "integration",
            "goal": "Vérifier la demande humaine avant le commit final",
            "depends_on": [],
            "likely_files": [],
            "symbols": [],
            "invariants": [],
            "acceptance": [str(item.get("message", "")) for item in directives],
            "test_commands": commands,
            "forbidden_changes": [],
        }
        reviews: list[tuple[str, dict[str, Any]]] = []
        for directive in directives:
            payload = self.review(
                synthetic,
                f"human-final-review-{directive.get('sequence')}",
                "contrôle humain obligatoire avant commit: " + str(directive.get("message", "")),
                self.cfg["architecture_reviewer_model"],
                self.base,
                [tests_path],
            )
            reviews.append((f"human-{directive.get('sequence')}", payload))
        judgment, judgment_path, review_paths = self.judge_findings(
            synthetic, reviews, self.base
        )
        if not judgment.get("accepted"):
            return False
        start_sha = self.checkpoint_sha
        repair, ticket_path, scout_request = self.repair_from_findings(
            synthetic, judgment_path, review_paths, tests_path, 1
        )
        context_paths = [
            self.scout_microtask(synthetic, request=scout_request, round_no=900)
        ]
        result = self.qwen_coder(
            repair["id"],
            self.coder_prompt(
                repair,
                [ticket_path, judgment_path, *review_paths, tests_path],
                context_paths,
                global_task=True,
            ),
        )
        self.context_json("final/human-final-repair-result.json", result)
        self.ensure_head_unchanged(start_sha)
        if self.check_forbidden(repair, start_sha):
            self.reset_to_checkpoint()
            raise RuntimeError("La réparation issue de la revue humaine finale est hors périmètre")
        retest, _ = self.run_tests(commands, "human-final-repair")
        if not retest["ok"]:
            self.reset_to_checkpoint()
            raise RuntimeError("La réparation issue de la revue humaine finale ne passe pas les tests")
        if self.changed_files(start_sha):
            self.checkpoint("human-final-review")
        return True

    def abort_from_human(self, action: HumanAction) -> None:
        self.state["phase"] = "aborted"
        self.event("human_abort", sequence=action.payload.get("sequence"))
        raise RuntimeError("Exécution arrêtée par intervention humaine; worktree et preuves conservés")

    def count_human_revision(self, target: str) -> int:
        counts = self.state["human_control"].setdefault("revisions_by_target", {})
        counts[target] = int(counts.get(target, 0)) + 1
        maximum = int(self.cfg.get("max_human_revisions_per_target", 3))
        if counts[target] > maximum:
            raise RuntimeError(f"Trop de révisions humaines pour {target}: {counts[target]} > {maximum}")
        self.save_state()
        return counts[target]

    def human_replan_evidence(self, action: HumanAction, target: str) -> Path:
        snapshot = self.snapshot_interruption(f"replan-{target}", action.payload)
        return self.context_json(
            f"human/replans/{slug(target)}-{int(action.payload.get('sequence', 0)):03d}.json",
            {
                "problem": "décomposition demandée par l'humain pour terminer la micro-tâche cible",
                "target": target,
                "intervention": action.payload,
                "interrupted_diff": self.rel(snapshot) if snapshot else None,
                "required_action": (
                    "décomposer uniquement la cible en enfants nécessaires à sa validation; "
                    "respecter les budgets de profondeur, enfants et tâches ajoutées"
                ),
            },
        )

    def handle_micro_intervention(
        self, action: HumanAction, plan: dict[str, Any], micro_id: str
    ) -> dict[str, Any]:
        kind = str(action.payload.get("action"))
        if kind == "abort":
            self.abort_from_human(action)
        if kind == "revise":
            revision = self.count_human_revision(micro_id)
            self.snapshot_interruption(f"revise-{micro_id}", action.payload)
            self.reset_to_checkpoint()
            self.state["microtask_status"][micro_id] = "pending"
            self.event(
                "human_revision_scheduled",
                target=micro_id,
                revision=revision,
                sequence=action.payload.get("sequence"),
            )
            return plan
        if kind == "replan":
            human_replans = int(self.state["human_control"].get("human_replans", 0)) + 1
            maximum = int(self.cfg.get("max_human_replans_total", 3))
            if human_replans > maximum:
                raise RuntimeError(f"Trop de replans humains: {human_replans} > {maximum}")
            self.state["human_control"]["human_replans"] = human_replans
            self.last_failure_path = self.human_replan_evidence(action, micro_id)
            self.reset_to_checkpoint()
            self.state["microtask_status"][micro_id] = "pending"
            new_plan = self.dynamic_replan(plan, micro_id)
            self.event(
                "human_replan_scheduled",
                target=micro_id,
                count=human_replans,
                sequence=action.payload.get("sequence"),
            )
            return new_plan
        return plan

    def run(self) -> None:
        self.preflight()
        self.create_worktree()

        plan_restarts = 0
        while True:
            self.set_active("planning", None, "establish-plan")
            try:
                plan = self.establish_plan()
                break
            except HumanAction as action:
                kind = str(action.payload.get("action"))
                if kind == "abort":
                    self.abort_from_human(action)
                if kind not in {"revise", "replan"}:
                    raise
                plan_restarts += 1
                if plan_restarts > int(self.cfg.get("max_human_plan_restarts", 2)):
                    raise RuntimeError("Trop de relances humaines du plan initial")
                self.event(
                    "human_revision_scheduled",
                    target="planning",
                    revision=plan_restarts,
                    sequence=action.payload.get("sequence"),
                )

        self.state["microtask_status"] = {task["id"]: "pending" for task in plan["microtasks"]}
        self.save_state()

        while any(status == "pending" for status in self.state["microtask_status"].values()):
            available = ready_tasks(plan, self.state)
            if not available:
                raise RuntimeError("Aucune micro-tâche prête: dépendances bloquées ou plan incohérent")
            micro = available[0]
            micro_id = str(micro["id"])
            self.set_active("microtask", micro_id, "execute")
            try:
                outcome = self.execute_microtask(micro, track_status=True)
            except HumanAction as action:
                plan = self.handle_micro_intervention(action, plan, micro_id)
                continue
            if outcome == "done":
                continue
            if outcome == "replan":
                plan = self.dynamic_replan(plan, micro_id)
                continue
            raise RuntimeError(f"Micro-tâche bloquée: {micro_id}")

        final_restarts = 0
        while True:
            try:
                self.set_active("global", None, "global-validation")
                self.global_validation()
                self.set_active("documentation", None, "documentation")
                self.finalize_docs()
                self.set_active("final", None, "deterministic-checks")
                self.deterministic_final_checks()
                self.safe_point()
                if self.final_human_review_gate():
                    continue
                self.set_active("commit", None, "squash-commit")
                self.safe_point()
                if self.final_human_review_gate():
                    continue
                self.squash_commit()
                break
            except HumanAction as action:
                kind = str(action.payload.get("action"))
                if kind == "abort":
                    self.abort_from_human(action)
                if kind not in {"revise", "replan"}:
                    raise
                final_restarts += 1
                if final_restarts > int(self.cfg.get("max_human_global_revisions", 3)):
                    raise RuntimeError("Trop de révisions humaines de la validation globale")
                self.snapshot_interruption("global-revision", action.payload)
                self.reset_to_checkpoint()
                self.event(
                    "human_revision_scheduled",
                    target="global",
                    revision=final_restarts,
                    sequence=action.payload.get("sequence"),
                )
        self.state["phase"] = "done"
        self.set_active("done", None, None)
        self.save_state()
        print("\nSUCCÈS")
        print(f"Branche: {self.branch}")
        print(f"Worktree: {self.worktree}")
        if self.state.get("commit_sha"):
            print(f"Commit final: {self.state['commit_sha']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Codex task → plan audité → scouts ciblés → micro-codeur live → review → commit"
    )
    parser.add_argument("task")
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    task = Path(args.task)
    task = task if task.is_absolute() else repo / task
    orchestrator: Orchestrator | None = None
    try:
        orchestrator = Orchestrator(repo, task)
        orchestrator.run()
        return 0
    except (RuntimeError, TaskMetaError, OSError, KeyError, ValueError) as exc:
        if orchestrator is not None and orchestrator.state.get("phase") != "aborted":
            orchestrator.state["phase"] = "failed"
            try:
                orchestrator.save_state()
            except OSError:
                pass
        print(f"\nÉCHEC: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
