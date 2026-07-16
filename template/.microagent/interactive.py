#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import queue
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

ACTIONS = {
    "note", "constraint", "pause", "resume", "review", "revise", "replan", "abort",
    "status", "stats", "ask", "answer", "approve", "deny", "approval",
}
NEEDS_MESSAGE = {"note", "constraint", "review", "revise", "replan", "ask", "answer"}

HELP = r"""
Console LocalCode interactive

Texte normal                    note durable sur la cible courante
/help                           affiche cette aide
/status                         affiche run, phase, tâche, opération, worktree et attentes
/stats                          affiche durées, tokens estimés, retries et changements de modèle
/target M3                      cible les prochaines interventions sur M3
/target current                 revient à la cible active
/file add CHEMIN                joint un fichier aux interventions suivantes
/file remove CHEMIN             retire un fichier joint
/file clear                     vide la liste
/files                          affiche cible et fichiers
/note MESSAGE                   note explicite
/constraint MESSAGE             contrainte dure vérifiée avant checkpoint
/ask QUESTION                   question au prochain point sûr; réponse en lecture seule
/answer Q-... MESSAGE           répond à une question bloquante de l'agent
/review MESSAGE                 reviewer supplémentaire obligatoire
/pause [RAISON]                 interrompt le sous-processus actif
/resume [MESSAGE]               reprend en conservant le diff
/revise MESSAGE                 revient au checkpoint et rejoue la cible
/replan MESSAGE                 redécoupe seulement la tâche active
/approval auto|commands|all     auto: connu seulement; commands: TASK auto, reste demandé; all: commandes+éditions
/approve A-... [once|run]       autorise une opération en attente
/deny A-... [RAISON]            refuse une opération
/abort [RAISON]                 arrête en gardant état, preuves et worktree
/detach                         quitte la saisie; le run continue

L'affichage détaillé est permanent. Les raisonnements internes bruts sont masqués; les actions,
preuves, décisions opérationnelles, fichiers et tests restent visibles.
""".strip()


def parse_line(line: str) -> tuple[str, str]:
    value = line.strip()
    if not value:
        return "empty", ""
    if not value.startswith("/"):
        return "note", value
    command, _, rest = value[1:].partition(" ")
    return command.lower(), rest.strip()


def input_worker(items: queue.Queue[str]) -> None:
    while True:
        try:
            items.put(input("localcode> "))
        except EOFError:
            items.put("/detach")
            return
        except KeyboardInterrupt:
            print("\nUtilise /pause, /abort ou /detach.")


def pump_output(stream: Any) -> None:
    try:
        for line in iter(stream.readline, ""):
            print(line, end="", flush=True)
    finally:
        stream.close()


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def wait_for_new_run(repo: Path, old_names: set[str], process: subprocess.Popen[str]) -> str:
    runs = repo / ".agent-runs"
    deadline = time.monotonic() + 120
    while process.poll() is None and time.monotonic() < deadline:
        if runs.exists():
            candidates = sorted(
                (
                    path for path in runs.iterdir()
                    if path.is_dir() and path.name not in old_names and (path / "state.json").exists()
                ),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                return candidates[0].name
        time.sleep(0.2)
    raise RuntimeError("Le nouveau run n'a pas créé son state.json")


def control(
    repo: Path, run_name: str, action: str, message: str, target: str,
    files: list[str], request_id: str = "", scope: str = "once",
) -> int:
    command = [sys.executable, str(repo / ".microagent" / "control.py"), action]
    if message:
        command.append(message)
    command.extend(["--repo", str(repo), "--run", run_name, "--target", target])
    if request_id:
        command.extend(["--request-id", request_id])
    if scope:
        command.extend(["--scope", scope])
    for path in files:
        command.extend(["--file", path])
    return subprocess.run(command, cwd=repo).returncode


def display_pending(run_dir: Path, shown: set[str]) -> None:
    approval_root = run_dir / "control" / "approvals" / "pending"
    if approval_root.exists():
        for path in sorted(approval_root.glob("*.json")):
            payload = load_json(path)
            request_id = str(payload.get("id", path.stem))
            marker = f"approval:{request_id}"
            if marker in shown:
                continue
            shown.add(marker)
            print(
                f"\n[AUTORISATION {request_id}] {payload.get('kind')} cible={payload.get('target')}\n"
                f"{json.dumps(payload.get('payload'), ensure_ascii=False)[:1200]}\n"
                f"/approve {request_id} ou /deny {request_id} raison",
                flush=True,
            )
    state = load_json(run_dir / "state.json")
    for item in state.get("questions", []):
        if item.get("answered"):
            continue
        question_id = str(item.get("id"))
        marker = f"question:{question_id}"
        if marker in shown:
            continue
        shown.add(marker)
        print(
            f"\n[QUESTION {question_id} · {item.get('target')}]\n{item.get('question')}\n"
            f"/answer {question_id} ta réponse",
            flush=True,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Orchestrateur LocalCode résilient avec contrôle dans le même terminal")
    parser.add_argument("task", nargs="?")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--resume", default="")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    runs = repo / ".agent-runs"
    old_names = {path.name for path in runs.iterdir()} if runs.exists() else set()

    command = [sys.executable, str(repo / ".microagent" / "resilient_orchestrator.py"), "--repo", str(repo)]
    if args.resume:
        command.extend(["--resume", args.resume])
    elif args.task:
        command.insert(2, args.task)
    else:
        print("Une mission ou --resume est requis", file=sys.stderr)
        return 2

    process = subprocess.Popen(
        command, cwd=repo, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=1,
    )
    assert process.stdout is not None
    threading.Thread(target=pump_output, args=(process.stdout,), daemon=True).start()

    try:
        if args.resume:
            if args.resume == "latest":
                candidates = sorted(
                    (path for path in runs.iterdir() if path.is_dir() and (path / "state.json").exists()),
                    key=lambda path: path.stat().st_mtime,
                    reverse=True,
                )
                run_name = candidates[0].name
            else:
                run_name = Path(args.resume).name
        else:
            run_name = wait_for_new_run(repo, old_names, process)
    except Exception as exc:
        if process.poll() is None:
            process.terminate()
        print(f"ERREUR: {exc}", file=sys.stderr)
        return process.wait()

    run_dir = runs / run_name
    print(f"\nConsole attachée au run {run_name}. Tape /help.")
    target = "current"
    files: list[str] = []
    items: queue.Queue[str] = queue.Queue()
    threading.Thread(target=input_worker, args=(items,), daemon=True).start()
    detached = False
    shown: set[str] = set()

    while process.poll() is None:
        display_pending(run_dir, shown)
        if detached:
            time.sleep(0.25)
            continue
        try:
            line = items.get(timeout=0.25)
        except queue.Empty:
            continue
        action, message = parse_line(line)
        if action == "empty":
            continue
        if action in {"help", "?"}:
            print(HELP)
            continue
        if action == "detach":
            print("Console détachée; le run continue.")
            detached = True
            continue
        if action == "target":
            if message:
                target = message
                print(f"Cible: {target}")
            else:
                print(f"Cible actuelle: {target}")
            continue
        if action == "files":
            print(f"Cible: {target}")
            print("Fichiers: " + (", ".join(files) if files else "aucun"))
            continue
        if action == "file":
            parts = shlex.split(message, posix=False)
            if parts and parts[0].lower() == "clear":
                files.clear()
            elif len(parts) >= 2 and parts[0].lower() in {"add", "remove"}:
                path = parts[1].strip('"')
                if parts[0].lower() == "add" and path not in files:
                    files.append(path)
                elif parts[0].lower() == "remove" and path in files:
                    files.remove(path)
            else:
                print("Usage: /file add CHEMIN | /file remove CHEMIN | /file clear")
                continue
            print("Fichiers: " + (", ".join(files) if files else "aucun"))
            continue

        request_id = ""
        scope = "once"
        actual_message = message
        if action in {"approve", "deny", "answer"}:
            parts = message.split(maxsplit=2)
            if not parts:
                print(f"/{action} exige un identifiant")
                continue
            request_id = parts[0]
            if action == "approve":
                if len(parts) >= 2 and parts[1] in {"once", "run"}:
                    scope = parts[1]
                    actual_message = parts[2] if len(parts) == 3 else ""
                else:
                    actual_message = ""
            else:
                actual_message = " ".join(parts[1:])
        if action == "approval":
            actual_message = message.lower()
        if action not in ACTIONS:
            print(f"Commande inconnue: /{action}. Tape /help.")
            continue
        if action in NEEDS_MESSAGE and not actual_message:
            print(f"/{action} exige un message.")
            continue
        code = control(repo, run_name, action, actual_message, target, files, request_id, scope)
        if code != 0:
            print(f"Échec de /{action} (code {code}).")
        if action == "abort" and code == 0:
            detached = True

    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
