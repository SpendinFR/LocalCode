#!/usr/bin/env python3
from __future__ import annotations

import argparse
import queue
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

ACTIONS = {"note", "pause", "resume", "review", "revise", "replan", "abort", "status"}
NEEDS_MESSAGE = {"note", "review", "revise", "replan"}

HELP = r"""
Console LocalCode interactive

Texte normal                  note durable sur la cible courante
/help                         affiche cette aide
/status                       run, phase, tâche, opération et worktree
/target M3                    cible les prochaines interventions sur M3
/target current               revient à la phase ou tâche active
/file add docs\architecture.md joint un fichier aux interventions suivantes
/file remove CHEMIN           retire un fichier joint
/file clear                   vide la liste des fichiers joints
/files                        affiche cible et fichiers actifs
/note MESSAGE                 note explicite
/review MESSAGE               reviewer supplémentaire obligatoire
/pause [RAISON]               suspend le sous-processus actif et garde le worktree
/resume [MESSAGE]             reprend en conservant le diff du worktree
/revise MESSAGE               capture le diff, revient au checkpoint et rejoue la cible
/replan MESSAGE               revient au checkpoint et décompose seulement la tâche active
/abort [RAISON]               arrête le run en gardant état, preuves et worktree
/detach                       quitte la saisie; le run continue
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


def pump_output(stream) -> None:
    try:
        for line in iter(stream.readline, ""):
            print(line, end="", flush=True)
    finally:
        stream.close()


def wait_for_run(repo: Path, old_names: set[str], process: subprocess.Popen[str]) -> str:
    runs = repo / ".agent-runs"
    deadline = time.monotonic() + 90
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


def control(repo: Path, run_name: str, action: str, message: str, target: str, files: list[str]) -> int:
    command = [sys.executable, str(repo / ".microagent" / "control.py"), action]
    if message:
        command.append(message)
    command.extend(["--repo", str(repo), "--run", run_name, "--target", target])
    for path in files:
        command.extend(["--file", path])
    return subprocess.run(command, cwd=repo).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Orchestrateur LocalCode avec contrôle dans le même terminal")
    parser.add_argument("task")
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()
    repo = Path(args.repo).resolve()
    runs = repo / ".agent-runs"
    old_names = {path.name for path in runs.iterdir()} if runs.exists() else set()

    process = subprocess.Popen(
        [sys.executable, str(repo / ".microagent" / "orchestrator.py"), args.task, "--repo", str(repo)],
        cwd=repo,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=1,
    )
    assert process.stdout is not None
    threading.Thread(target=pump_output, args=(process.stdout,), daemon=True).start()

    try:
        run_name = wait_for_run(repo, old_names, process)
    except RuntimeError as exc:
        if process.poll() is None:
            process.terminate()
        print(f"ERREUR: {exc}", file=sys.stderr)
        return process.wait()

    print(f"\nConsole attachée au run {run_name}. Tape /help.")
    target = "current"
    files: list[str] = []
    items: queue.Queue[str] = queue.Queue()
    threading.Thread(target=input_worker, args=(items,), daemon=True).start()
    detached = False

    while process.poll() is None:
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
        if action not in ACTIONS:
            print(f"Commande inconnue: /{action}. Tape /help.")
            continue
        if action in NEEDS_MESSAGE and not message:
            print(f"/{action} exige un message.")
            continue
        code = control(repo, run_name, action, message, target, files)
        if code != 0:
            print(f"Échec de /{action} (code {code}).")
        if action == "abort" and code == 0:
            detached = True

    return process.wait()


if __name__ == "__main__":
    raise SystemExit(main())
