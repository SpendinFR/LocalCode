# LocalCode V9 — résilience, contrôle et contexte compact

Cette couche est additive : `orchestrator.py` reste le moteur historique et `resilient_orchestrator.py` ajoute les mécanismes de production.

## Lancement et reprise

```powershell
.\agent.ps1 .tasks\TASK-042.md
.\agent.ps1 -Resume latest
.\agent.ps1 -Resume TASK-042-20260716-153000
```

`Ctrl+C`, une erreur Qwen ou une panne du serveur conservent le run et affichent la commande de reprise.

## Affichage permanent

Les phases, modèles, outils, fichiers, tests, retries, compactages et décisions opérationnelles sont affichés sans activer un mode trace. Les blocs `<think>` ne sont pas affichés et ne sont pas réinjectés dans les prompts.

## Dialogue humain

Tout texte normal devient une note figée sur la tâche active au moment de l'envoi. Une note ou contrainte non vérifiée bloque le checkpoint de la tâche.

```text
Ne remplace pas ce contrat historique.
/constraint Préserver le format public.
/ask Pourquoi ce test est-il nécessaire ?
/answer Q-123 Le test est la source de vérité.
```

L'agent ne demande de l'aide qu'après les recherches locales et scouts ciblés, avec au maximum deux questions par micro-tâche et cinq par run.

## Autorisations

Mode par défaut : `commands`.

- commandes exactement déclarées dans `TASK.md` : automatiques ;
- nouvelle commande shell : demande `/approve` ou `/deny` ;
- commandes dangereuses : toujours interdites ;
- `all` : demande aussi avant `edit` et `write_file` ;
- `auto` : autorise les commandes sûres correspondant aux préfixes configurés.

```text
/approval commands
/approval all
/approve A-123 once
/approve A-123 run
/deny A-123 commande inutile
/stats
```

## Budgets 8K

Les trois modèles restent à 8192 tokens de contexte. Le superviseur réserve explicitement l'espace nécessaire aux outils et à la sortie, puis compacte déterministiquement l'entrée avant dépassement.

- Qwen3.5-9B : sortie réservée 2200, outils 2900, sécurité 700 ;
- Qwen3-Coder-30B IQ2 : sortie réservée 2200, outils 3600, sécurité 700 ;
- Qwen2.5-Coder-14B Q3 : sortie réservée 1700, outils 3100, sécurité 700.

Les sorties fournisseurs sont également limitées à 2048/1536 tokens pour éviter les JSON coupés par un prompt trop gros.

## Récupération

- deux retries classifiés ;
- redémarrage du routeur en cas d'erreur serveur ;
- compaction supplémentaire en cas de contexte trop grand ;
- modèle de secours pour les rôles en lecture seule ;
- réparation de JSON ;
- si le codeur a déjà modifié des fichiers, récupération du résultat par un agent en lecture seule, sans rejouer aveuglément les éditions ;
- reprise du même worktree, de la même branche, des tâches déjà validées et du dernier checkpoint.

## Test de capacité

Au premier lancement, ou lorsque les fichiers modèles changent, chaque modèle doit réussir un petit test JSON. Le résultat est mis en cache dans `.microagent/model-capabilities.json`.


## Second terminal

Les mêmes actions restent disponibles avec `agent-control.ps1` :

```powershell
.\agent-control.ps1 stats
.\agent-control.ps1 approve -RequestId A-123 -Scope run
.\agent-control.ps1 answer "Le test est la source de vérité" -RequestId Q-123
```
