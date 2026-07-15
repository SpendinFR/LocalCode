# Local Codex Adaptive Micro-Agent V7 — RTX 3070

Ce kit prend une mission précise créée par Codex et la fait exécuter localement par de petits modèles jusqu'à un commit unique, dans une branche et un worktree isolés.

## Objectif réel

Le but n'est pas d'empiler des agents. Il s'agit de réduire la difficulté intellectuelle à chaque étape afin qu'un modèle 7–8B puisse travailler comme un développeur junior correctement encadré :

```text
Codex senior écrit TASK.md
→ planner local découpe en micro-tâches
→ deux audits + un juge contrôlent le plan
→ scout fournit seulement les fichiers/symboles/plages utiles
→ codeur réalise une micro-tâche en boucle live
→ l'humain peut noter, imposer une review, pauser, réviser ou demander un replan borné
→ superviseur relance réellement les tests
→ deux reviewers cherchent les défauts
→ juge filtre les faux positifs
→ ticket de réparation immuable + scout ciblé
→ codeur corrige puis double revue
→ micro-tâche suivante
→ validation globale + documentation demandée par Codex uniquement + commit unique
```

## Boucle adaptative du codeur

Dans une même session Qwen Code, le codeur peut chercher, lire une plage, modifier, lancer un test, recevoir `stdout/stderr`, corriger la commande ou le code, puis retester. S'il manque une information, il retourne une demande précise au superviseur ; un scout neuf recherche cette information et le codeur est relancé avec un petit paquet supplémentaire.

Le codeur n'est jamais obligé de rester dans une liste figée de fichiers. Il peut découvrir une dépendance, mais il doit la localiser par symbole/import/référence et rester dans le plafond de fichiers de la micro-tâche. Un dépassement déclenche une replanification au lieu d'une modification massive.

## Circuit de review sans « téléphone arabe »

Les remarques ne sont pas reformulées de modèle en modèle :

1. le reviewer fournit fichier, symbole, scénario, attendu, observé, preuve, test et lien avec la mission Codex ;
2. le juge vérifie le finding dans le vrai code ;
3. le superviseur lui attribue un `finding_id` stable ;
4. le superviseur crée lui-même un ticket contenant les findings acceptés, le diff, les revues originales et les résultats des tests ;
5. un scout localise uniquement les causes et appels associés ;
6. le codeur reçoit le ticket et les sources originales, puis doit déclarer les `finding_id` résolus ;
7. tests et reviewers sont rejoués sur le diff complet de la micro-tâche.

Si le même finding survit à une réparation, le système n'enchaîne pas les corrections aveugles : il revient au checkpoint et demande au planner de redécouper le travail.

## Documentation pilotée uniquement par Codex

Le superviseur n'impose aucun nom de document. Dans `AGENT_TASK_META`, Codex peut déclarer :

- `context_files` : documents utiles à consulter pendant le plan, le scout ou la revue ;
- `documentation_updates` : fichiers à modifier à la fin, avec instruction, marqueurs, autorisation de création et obligation de changement.

Si `documentation_updates` est vide, aucune documentation n'est modifiée. Les documents déclarés pour la phase finale sont protégés pendant le codage des micro-tâches.

## Décomposition dynamique bornée

Le plan initial contient des tâches `M1`, `M2`, `M3` petites et vérifiables. Si les preuves montrent que `M2` ne peut pas être validée telle quelle, le planner peut remplacer uniquement `M2` par un fragment comme `M2a → M2b`. Chaque enfant doit expliquer pourquoi il est nécessaire à `M2` et couvrir explicitement un ou plusieurs critères d'acceptation de `M2`. `M3` reste bloquée jusqu'à validation cumulative du fragment.

La liberté est bornée : 4 enfants maximum par expansion, profondeur maximale 2, une révision du fragment, 3 expansions dynamiques et 8 tâches ajoutées au total. Les tâches déjà validées et les objectifs en aval ne peuvent pas être réécrits.

## Protection du contexte et du temps

- `read_many_files` interdit ;
- `read_file` limité à 180 lignes par appel ;
- `grep_search` limité à 30 résultats ;
- globs racine massifs interdits ;
- `cat`, `Get-Content`, `rg`, `grep`, `find .`, `dir /s` et lectures massives via shell bloqués ;
- scout limité à 10 fichiers et 4 plages par fichier ;
- micro-tâche prévue pour 1 à 4 fichiers, 6 fichiers probables maximum ;
- replanification si plus de 8 fichiers sont modifiés ;
- deux demandes de contexte complémentaires maximum par micro-tâche ;
- deux réparations maximum, puis blocage ou replanification ;
- modèles chargés successivement, jamais simultanément ;
- 100 interventions enregistrables par run, mais seulement 12 résumés injectés à la fois et 12 000 caractères maximum ;
- 3 révisions humaines par cible, 3 replans humains et 3 reprises globales maximum.

Les tests, builds, linters et typecheckers restent exécutables par shell.

## Modèles par défaut

- planner, codeur et reviewer logique : `qwen3:8b` ;
- scout, reviewer technique et juge : `qwen2.5-coder:7b`.

Les deux variantes Ollama font environ 5,2 Go et 4,7 Go respectivement ; elles sont chargées successivement. Le contexte du 8B est borné à 8K dans la configuration pour réduire le risque de débordement VRAM et la lenteur. Tu peux changer les rôles dans `.microagent/config.json` et `.qwen/settings.json`.

## Installation

### Windows PowerShell

```powershell
Expand-Archive .\local-codex-adaptive-microagent-v7.zip
cd .\local-codex-adaptive-microagent-v7
.\install.ps1 C:\chemin\vers\ton-repo
```

### Linux/macOS

```bash
unzip local-codex-adaptive-microagent-v7.zip
cd local-codex-adaptive-microagent-v7
./install.sh /chemin/vers/ton-repo
```

L'installation ajoute le superviseur, la configuration Qwen Code, les launchers et le modèle de TASK. Committe une fois le kit installé avant la première mission.

## Utilisation en une commande

Après que Codex a créé `.tasks/TASK-042.md`, à la racine du dépôt :

```powershell
.\agent.ps1 .tasks\TASK-042.md
```

Sous `cmd.exe` :

```bat
agent.cmd .tasks\TASK-042.md
```

Sous Linux/macOS :

```bash
./agent.sh .tasks/TASK-042.md
```

La commande effectue le préflight, crée le worktree et peut aller seule jusqu'au commit. Elle reste contrôlable en direct depuis un second terminal. Elle ne fusionne et ne pousse rien.

En cas de succès, elle affiche la branche, le worktree et le SHA. En cas d'échec, le worktree et les preuves sont conservés dans `.agent-runs/...`.

## Intervention humaine en direct

Depuis un second PowerShell ouvert à la racine du dépôt, `agent-control.ps1` cible par défaut le dernier run actif et la phase ou micro-tâche courante. Le canal est surveillé pendant les appels Qwen et pendant les commandes de tests. Les interventions sont enregistrées dans `.agent-runs/<run>/control/`, recopiées dans le paquet de contexte et conservées dans `state.json`.

```powershell
.\agent-control.ps1 status
.\agent-control.ps1 note "Vérifie aussi le cas stop/go"
.\agent-control.ps1 review "Contrôle obligatoirement les appelants historiques"
.\agent-control.ps1 pause
.\agent-control.ps1 resume "J'ai ajouté une précision dans le worktree"
.\agent-control.ps1 revise "Reprends la micro-tâche avec ce scénario obligatoire"
.\agent-control.ps1 replan "Décompose seulement la tâche active car cette étape est nécessaire à sa validation"
.\agent-control.ps1 abort
```

Tu peux cibler une tâche future ou la validation globale :

```powershell
.\agent-control.ps1 note "Préserver ce contrat" -Target M3
.\agent-control.ps1 review "Vérifier la compatibilité Windows" -Target global
.\agent-control.ps1 note "Lire aussi ce fichier" -File docs\architecture.md
```

Sémantique des actions :

- `note` ajoute une instruction durable au contexte des agents concernés sans interrompre le travail ;
- `review` impose un reviewer supplémentaire avant que la cible puisse être validée ;
- `pause` interrompt le sous-processus actif, conserve le worktree et attend `resume` ;
- `revise` capture le diff, revient au dernier checkpoint et rejoue la cible avec l'instruction humaine ;
- `replan` revient au checkpoint puis autorise uniquement la décomposition bornée de la micro-tâche active ;
- `abort` arrête le run en conservant le worktree, l'état et toutes les preuves.

Une intervention visant une tâche future est mise en attente puis déclenchée quand cette tâche devient active. Les notes n'ajoutent aucune tâche. Les révisions sont limitées à 3 par cible ; les replans humains à 3 au total et restent soumis aux limites normales : 4 enfants, profondeur 2, 8 tâches ajoutées et 3 expansions dynamiques. Le prompt humain injecté est également plafonné afin d'éviter une croissance incontrôlée des tokens.

Pour modifier toi-même un fichier, utilise `pause`, ouvre le chemin `Worktree` affiché par `status`, fais ton changement, puis `resume` avec une explication. Pour repartir proprement plutôt que conserver le diff en cours, utilise `revise`.

## Préparer la mission Codex

Utilise `CODEX_PROMPT_TASK.md`. Codex doit analyser le dépôt et remplir `.tasks/TASK_TEMPLATE.md` sans placeholder, notamment avec les vraies commandes de tests, lint, typecheck, build et suite complète.

## Revue finale Codex

Le kit local termine sur un commit prêt à être jugé. `CODEX_PROMPT_FINAL_REVIEW.md` fournit le prompt de revue senior. Cette étape n'est pas lancée automatiquement parce qu'elle dépend de ton installation/authentification Codex et parce qu'une revue refusée nécessite une décision : nouvelle passe locale ou nouveau plan Codex.

## Ce qui reste à ajuster sur ton dépôt

À régler dès la première mission réelle :

- commandes exactes et timeouts de ta stack ;
- services externes nécessaires aux tests ;
- éventuels `context_files` et `documentation_updates` déclarés par Codex dans la mission ;
- configuration LSP ;
- fichiers générés à ignorer ;
- seuil de 6/8 fichiers si tes changements atomiques sont naturellement plus larges ;
- contexte 8K ou 12K selon la VRAM réellement utilisée ;
- qualité comparative de `qwen3:8b` et `qwen2.5-coder:7b` comme codeur principal.

## Validation du kit

Les scénarios suivants ont été testés avec un faux Qwen : installation, plan audité, exécution complète, commande unique, demande de scout, replanification, ticket de finding direct, réparation, double revue, documentation, commit unique, canal de contrôle atomique, pause/reprise, note injectée, révision avec rollback, replan humain borné et reviewer humain obligatoire.

Cela valide la mécanique, pas le niveau intellectuel du vrai modèle. La première tâche sur ton dépôt doit être importante mais non critique afin de mesurer les prompts, les commandes et la taille de micro-tâche.
