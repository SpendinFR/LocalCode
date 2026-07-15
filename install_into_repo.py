from __future__ import annotations
import argparse, shutil, sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / "template"
BEGIN = "<!-- LOCAL_MICROAGENT_V7_BEGIN -->"
END = "<!-- LOCAL_MICROAGENT_V7_END -->"
LEGACY_BLOCKS = [("<!-- LOCAL_MICROAGENT_V6_BEGIN -->", "<!-- LOCAL_MICROAGENT_V6_END -->"), ("<!-- LOCAL_MICROAGENT_V5_BEGIN -->", "<!-- LOCAL_MICROAGENT_V5_END -->"), ("<!-- LOCAL_MICROAGENT_V4_BEGIN -->", "<!-- LOCAL_MICROAGENT_V4_END -->"), ("<!-- LOCAL_MICROAGENT_V3_BEGIN -->", "<!-- LOCAL_MICROAGENT_V3_END -->")]

def backup(path: Path, repo: Path, backup_root: Path) -> None:
    if not path.exists(): return
    rel = path.relative_to(repo)
    dst = backup_root / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dst)

def copy_template(repo: Path, backup_root: Path) -> None:
    for src in TEMPLATE.rglob("*"):
        if not src.is_file(): continue
        rel = src.relative_to(TEMPLATE)
        dst = repo / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            backup(dst, repo, backup_root)
            if rel.as_posix() == ".tasks/TASK_TEMPLATE.md":
                existing = dst.read_text(encoding="utf-8", errors="replace")
                # Upgrade the known V3–V6 template, but preserve a genuinely custom template.
                if not any(marker in existing for marker in (
                    '"plan_file"', '"build_guide_file"', 'REMPLACER_PAR_TESTS_CIBLES_REELS'
                )):
                    continue
        shutil.copy2(src, dst)

def merge_agents(repo: Path, backup_root: Path) -> None:
    path = repo / "AGENTS.md"
    if path.exists(): backup(path, repo, backup_root)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    block = (ROOT / "AGENTS_BLOCK.md").read_text(encoding="utf-8").strip()
    markers = [(BEGIN, END), *LEGACY_BLOCKS]
    found = next(((begin, end) for begin, end in markers if begin in existing and end in existing), None)
    if found:
        begin, end = found
        before = existing.split(begin, 1)[0].rstrip()
        after = existing.split(end, 1)[1].lstrip()
        text = before + "\n\n" + block + "\n"
        if after:
            text += "\n" + after
    else:
        text = existing.rstrip() + ("\n\n" if existing.strip() else "") + block + "\n"
    path.write_text(text, encoding="utf-8")

def update_gitignore(repo: Path, backup_root: Path) -> None:
    path = repo / ".gitignore"
    if path.exists(): backup(path, repo, backup_root)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    entries = [".agent-worktrees/", ".agent-runs/", ".agent-context/", ".local-microagent-backup/", "**/__pycache__/", ".microagent/__pycache__/"]
    lines = set(existing.splitlines())
    missing = [x for x in entries if x not in lines]
    if missing:
        path.write_text(existing.rstrip() + "\n\n# Local Codex Adaptive Micro-Agent V7\n" + "\n".join(missing) + "\n", encoding="utf-8")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo", nargs="?", default=".")
    args = ap.parse_args()
    repo = Path(args.repo).resolve()
    try:
        inside = __import__('subprocess').run(["git", "rev-parse", "--is-inside-work-tree"], cwd=repo, text=True, capture_output=True)
    except OSError as exc:
        print(f"git introuvable: {exc}", file=sys.stderr); return 2
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        print(f"{repo} n'est pas un dépôt Git", file=sys.stderr); return 2
    backup_root = repo / ".local-microagent-backup" / datetime.now().strftime("%Y%m%d-%H%M%S")
    copy_template(repo, backup_root)
    merge_agents(repo, backup_root)
    update_gitignore(repo, backup_root)
    print(f"Kit V7 installé dans {repo}")
    print(f"Sauvegardes: {backup_root}")
    print("Crée une mission .tasks/TASK-XXX.md puis lance en une commande:")
    print(r"  .\agent.ps1 .tasks\TASK-XXX.md   # Windows PowerShell")
    print(r"  agent.cmd .tasks\TASK-XXX.md      # Windows cmd")
    print("  ./agent.sh .tasks/TASK-XXX.md       # Linux/macOS")
    print("Contrôle live depuis un second terminal:")
    print(r"  .\agent-control.ps1 status")
    print(r"  .\agent-control.ps1 pause")
    print('  .\\agent-control.ps1 resume \"instruction optionnelle\"')
    return 0

if __name__ == "__main__": raise SystemExit(main())
