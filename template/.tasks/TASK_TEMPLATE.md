<!-- AGENT_TASK_META
{
  "task_id": "TASK-000",
  "context_files": [],
  "documentation_updates": [],
  "require_test_changes": true,
  "test_file_globs": [
    "tests/**", "test/**", "**/tests/**", "**/*.test.*", "**/*.spec.*", "**/test_*.py", "**/*_test.py"
  ],
  "validation_commands": [
    "REMPLACER_PAR_TESTS_CIBLES_REELS"
  ],
  "full_suite_commands": [
    "REMPLACER_PAR_TYPECHECK_LINT_BUILD_ET_SUITE_COMPLETE"
  ],
  "forbidden_paths": [
    ".env", ".env.*", "**/secrets/**", "**/*.pem", "**/*.key", ".microagent/**", ".qwen/**"
  ],
  "command_timeout_seconds": 1800,
  "commit_message": "feat: complete TASK-000"
}
AGENT_TASK_META -->

# TASK-000 — Titre précis

## Objectif observable

## Comportement actuel et preuves dans le dépôt

## Carte du flux multifichier

- Entrée :
- Appels intermédiaires :
- État/persistance :
- Sortie :
- Gestion d'erreur :
- Concurrence/transactions :

## Fichiers, symboles et relations probablement concernés

Les chemins sont des pistes. Le planner local doit vérifier définitions, références, imports, contrats et tests.

- `path/file.ext` — `SymbolName` — relation avec le flux

## Invariants à préserver

## Ordre logique recommandé

## Cas limites, risques et scénarios adversariaux

## Commandes vérifiées

Les commandes doivent fonctionner directement dans le dépôt. Sous Windows, Qwen Code utilise `cmd.exe`.
Pour PowerShell, écrire explicitement `powershell -NoProfile -Command "..."` ou `pwsh ...`.

## Critères d'acceptation

- [ ] comportement principal démontré ;
- [ ] tests ciblés verts ;
- [ ] typecheck/lint/build verts ;
- [ ] suite complète verte.

## Modifications interdites ou hors périmètre

## Documentation demandée par Codex

Aucune documentation n'est modifiée automatiquement. Déclarer chaque fichier dans
`documentation_updates` avec une instruction précise et, si possible, des marqueurs vérifiables.
