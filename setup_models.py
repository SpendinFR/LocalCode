#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class ModelSpec:
    alias: str
    filename: str | None
    repo_id: str | None
    patterns: tuple[str, ...]
    gpu_layers: int
    role: str


SPECS = (
    ModelSpec(
        alias="qwen35-9b",
        filename="Qwen3.5-9B-Q4_K_M.gguf",
        repo_id=None,
        patterns=("*Qwen3.5*9B*Q4_K_M*.gguf", "*qwen3.5*9b*q4_k_m*.gguf"),
        gpu_layers=99,
        role="planner, scout et juge",
    ),
    ModelSpec(
        alias="qwen3coder30-iq2",
        filename="Qwen3-Coder-30B-A3B-Instruct-UD-IQ2_XXS.gguf",
        repo_id="unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
        patterns=("*UD-IQ2_XXS*.gguf",),
        gpu_layers=24,
        role="codeur principal",
    ),
    ModelSpec(
        alias="qwen25coder14-q3",
        filename=None,
        repo_id="Qwen/Qwen2.5-Coder-14B-Instruct-GGUF",
        patterns=("*q3_k_m*.gguf", "*Q3_K_M*.gguf"),
        gpu_layers=28,
        role="reviewer indépendant",
    ),
)


def expand(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def discover(root: Path, spec: ModelSpec) -> list[Path]:
    if not root.exists():
        return []
    found: set[Path] = set()
    if spec.filename:
        found.update(path.resolve() for path in root.rglob(spec.filename) if path.is_file())
    for pattern in spec.patterns:
        found.update(path.resolve() for path in root.rglob(pattern) if path.is_file())
    return sorted(found)


def select(paths: Iterable[Path], filename: str | None) -> Path | None:
    values = list(paths)
    if filename:
        exact = [path for path in values if path.name.lower() == filename.lower()]
        if exact:
            return exact[0]
    first_shards = [path for path in values if "-00001-of-" in path.name]
    if first_shards:
        return sorted(first_shards)[0]
    return sorted(values)[0] if values else None


def download(spec: ModelSpec, destination: Path) -> Path:
    if not spec.repo_id:
        raise RuntimeError(
            f"{spec.filename} est absent. Indique son chemin avec --qwen35-path "
            "ou place-le dans Downloads / LM Studio / le dossier de modèles."
        )
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise RuntimeError("Installe huggingface_hub: python -m pip install huggingface_hub") from exc
    destination.mkdir(parents=True, exist_ok=True)
    print(f"Téléchargement de {spec.alias} depuis {spec.repo_id}...")
    snapshot_download(
        repo_id=spec.repo_id,
        allow_patterns=list(spec.patterns),
        local_dir=str(destination),
    )
    chosen = select(discover(destination, spec), spec.filename)
    if chosen is None:
        raise RuntimeError(f"Aucun GGUF correspondant téléchargé pour {spec.alias}")
    return chosen


def write_preset(repo: Path, resolved: dict[str, Path]) -> Path:
    microagent = repo / ".microagent"
    microagent.mkdir(parents=True, exist_ok=True)
    preset = microagent / "models.ini"
    lines = [
        "version = 1",
        "",
        "[*]",
        "c = 8192",
        "parallel = 1",
        "jinja = true",
        "flash-attn = on",
        "cache-type-k = q4_0",
        "cache-type-v = q4_0",
        "reasoning = off",
        "",
    ]
    for spec in SPECS:
        lines.extend(
            [
                f"[{spec.alias}]",
                f"model = {resolved[spec.alias].as_posix()}",
                f"n-gpu-layers = {spec.gpu_layers}",
                "",
            ]
        )
    preset.write_text("\n".join(lines), encoding="utf-8")
    runtime = {
        "api_base": "http://127.0.0.1:8080/v1",
        "api_key": "local",
        "models_max": 1,
        "preset": str(preset),
        "models": {
            spec.alias: {
                "path": str(resolved[spec.alias]),
                "role": spec.role,
                "gpu_layers": spec.gpu_layers,
            }
            for spec in SPECS
        },
    }
    (microagent / "model-runtime.json").write_text(
        json.dumps(runtime, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return preset


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure les modèles GGUF recommandés pour LocalCode")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--models-dir", required=True)
    parser.add_argument("--existing-dir", action="append", default=[])
    parser.add_argument("--qwen35-path", default="")
    parser.add_argument("--qwen3coder-path", default="")
    parser.add_argument("--reviewer-path", default="")
    parser.add_argument("--no-download", action="store_true")
    args = parser.parse_args()

    repo = expand(args.repo)
    models_dir = expand(args.models_dir)
    explicit = {
        "qwen35-9b": args.qwen35_path,
        "qwen3coder30-iq2": args.qwen3coder_path,
        "qwen25coder14-q3": args.reviewer_path,
    }
    roots = [models_dir]
    roots.extend(expand(value) for value in args.existing_dir if value)

    resolved: dict[str, Path] = {}
    for spec in SPECS:
        raw = explicit[spec.alias]
        if raw:
            path = expand(raw)
            if not path.is_file():
                raise RuntimeError(f"Fichier absent pour {spec.alias}: {path}")
            resolved[spec.alias] = path
            print(f"Fichier fourni pour {spec.alias}: {path}")
            continue

        found: list[Path] = []
        for root in roots:
            found.extend(discover(root, spec))
        chosen = select(found, spec.filename)
        if chosen is not None:
            resolved[spec.alias] = chosen
            print(f"Modèle existant trouvé pour {spec.alias}: {chosen}")
            continue

        if args.no_download:
            raise RuntimeError(f"Modèle absent: {spec.alias}")
        resolved[spec.alias] = download(spec, models_dir / spec.alias)

    preset = write_preset(repo, resolved)
    print(f"Preset llama.cpp créé: {preset}")
    print("Le routeur chargera au maximum un modèle à la fois.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"ERREUR: {exc}", file=sys.stderr)
        raise SystemExit(2)
