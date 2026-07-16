# LocalCode V9 — Micro-agent local résilient pour RTX 3070

LocalCode prend une mission technique précise, généralement préparée par Codex dans un fichier `TASK.md`, puis la fait exécuter localement par plusieurs modèles spécialisés jusqu’à la production d’un commit unique dans une branche et un worktree isolés.

La V9 ajoute une couche de production autour du moteur V8 :

- reprise après interruption ;
- retries classifiés ;
- récupération des sorties JSON tronquées ;
- récupération spéciale après une modification de code partiellement terminée ;
- contrôle humain dans le même terminal ;
- autorisation explicite des nouvelles commandes ;
- gestion compacte du contexte 8K ;
- tests de capacité des modèles ;
- verrouillage des runs ;
- affichage opérationnel permanent.

Le moteur historique reste présent dans `.microagent/orchestrator.py`. La couche V9 est portée par `.microagent/resilient_orchestrator.py`.

---

## Matériel visé

La configuration par défaut cible environ :

- 8 Go de VRAM ;
- 16 Go de RAM ;
- une carte graphique de type RTX 3070 ;
- des modèles GGUF servis successivement avec `llama.cpp` ;
- un seul modèle chargé en mémoire à la fois.

Le système ne cherche pas à exécuter plusieurs gros modèles simultanément. Il attribue chaque rôle au modèle le plus adapté et laisse le routeur remplacer le modèle actif lorsque le rôle change.

---

## Architecture générale

```text
Codex senior prépare TASK.md
→ planner local découpe la mission
→ deux audits contrôlent le plan
→ juge filtre les critiques faibles
→ scout localise les fichiers, symboles et plages utiles
→ codeur traite une micro-tâche
→ tests ciblés
→ reviewers indépendants
→ juge filtre les findings
→ ticket de réparation immuable
→ réparation ciblée
→ micro-tâche suivante
→ validation globale
→ documentation demandée dans TASK.md
→ commit final unique
```

Pendant toute l’exécution :

```text
humain
↔ console interactive
↔ notes, contraintes, réponses et autorisations
↔ superviseur résilient
```

Le système ne fusionne et ne pousse rien automatiquement.

---

## Modèles recommandés

### Planner, scout, juge et récupération

```text
Qwen3.5-9B-Q4_K_M.gguf
alias : qwen35-9b
```

Rôles :

- compréhension de la mission ;
- planification ;
- exploration ciblée en lecture seule ;
- arbitrage des audits ;
- classification des échecs ;
- réparation de sorties JSON ;
- récupération d’un résultat après sortie tronquée.

### Codeur principal

```text
Qwen3-Coder-30B-A3B-Instruct-UD-IQ2_XXS.gguf
alias : qwen3coder30-iq2
```

Rôles :

- modification du code ;
- création des tests ;
- exécution des commandes autorisées ;
- correction d’erreurs ;
- traitement des tickets de réparation.

### Reviewers indépendants

```text
Qwen2.5-Coder-14B-Instruct Q3_K_M
alias : qwen25coder14-q3
```

Rôles :

- revue logique et architecturale ;
- revue d’intégration et d’exécution ;
- contrôle des contrats ;
- compatibilité plateforme ;
- recherche de faux positifs.

Le reviewer n’est volontairement pas le même modèle que le codeur.

---

## Routeur llama.cpp

Qwen Code utilise le fournisseur OpenAI-compatible local :

```text
http://127.0.0.1:8080/v1
```

Alias disponibles :

```text
qwen35-9b
qwen3coder30-iq2
qwen25coder14-q3
```

Le routeur est configuré pour conserver au maximum un modèle chargé :

```text
--models-max 1
```

Conséquences :

- un seul processus `llama-server` ;
- un seul modèle actif en VRAM/RAM ;
- chargement à la demande selon le rôle ;
- réutilisation du modèle si deux rôles successifs utilisent le même alias ;
- redémarrage automatique tenté après une erreur serveur classifiée.

---

## Installation

### Prérequis

- Git ;
- Python 3.10 ou plus récent ;
- Node.js 22 ou plus récent ;
- Qwen Code ;
- une version récente de `llama.cpp` avec `llama-server` ;
- les modèles GGUF configurés ;
- suffisamment d’espace disque pour les modèles, worktrees et journaux.

### Windows

Depuis le dossier du kit :

```powershell
.\install.ps1 -Repo "C:\chemin\vers\ton-repo"
```

Avec des modèles déjà téléchargés :

```powershell
.\install.ps1 `
  -Repo "C:\chemin\vers\ton-repo" `
  -ExistingModelsDir "D:\Models"
```

Sans installer les modèles immédiatement :

```powershell
.\install.ps1 `
  -Repo "C:\chemin\vers\ton-repo" `
  -Models no
```

### Linux ou macOS

```bash
./install.sh /chemin/vers/ton-repo
```

L’installation ajoute notamment :

- `.microagent/orchestrator.py` ;
- `.microagent/resilient_orchestrator.py` ;
- `.microagent/resilience.py` ;
- `.microagent/tool_guard.py` ;
- la console interactive ;
- les schémas JSON ;
- les launchers ;
- le routeur de modèles ;
- les tests du kit ;
- la configuration Qwen Code.

Committe l’installation initiale avant la première mission.

---

## Préparer une mission

Codex doit remplir un fichier comme :

```text
.tasks/TASK-042.md
```

Le bloc `AGENT_TASK_META` doit notamment préciser :

- le but ;
- les critères d’acceptation ;
- les invariants ;
- les chemins interdits ;
- les commandes de validation ciblées ;
- les commandes de suite complète ;
- les fichiers de contexte éventuels ;
- les documents à mettre à jour ;
- le message de commit ;
- le timeout des commandes.

Les commandes déclarées exactement dans `validation_commands` et `full_suite_commands` ont un statut particulier dans la politique d’autorisation.

---

## Lancer une mission

### Mode normal interactif

```powershell
.\agent.ps1 .tasks\TASK-042.md
```

Le même terminal affiche l’exécution et accepte les commandes humaines.

### Sans console interactive

```powershell
.\agent.ps1 .tasks\TASK-042.md -NoInteractive
```

### Sans démarrer ou vérifier le routeur

```powershell
.\agent.ps1 .tasks\TASK-042.md -NoRouter
```

### Choisir la politique d’autorisation au lancement

```powershell
.\agent.ps1 .tasks\TASK-042.md -ApprovalMode commands
```

Valeurs :

```text
auto
commands
all
```

Le mode par défaut est `commands`.

---

## Reprendre un run interrompu

Dernier run :

```powershell
.\agent.ps1 -Resume latest
```

Run précis :

```powershell
.\agent.ps1 -Resume TASK-042-20260716-153000
```

La reprise conserve :

- le run ;
- la branche ;
- le worktree ;
- le plan existant ;
- les checkpoints ;
- les micro-tâches déjà validées ;
- les interventions humaines ;
- le diff partiel éventuel.

Une micro-tâche interrompue peut être rejouée depuis son point d’entrée contrôlé avec le diff partiel fourni comme preuve. La reprise ne reprend pas au milieu exact d’une instruction interne du modèle.

---

## Affichage permanent

Le terminal affiche en continu :

- la phase ;
- la micro-tâche ;
- l’opération active ;
- le modèle ;
- la progression ;
- les summaries des appels ;
- les fichiers nouvellement modifiés ;
- les résultats de tests ;
- les retries ;
- les compactages ;
- les redémarrages du routeur ;
- les demandes d’autorisation ;
- les questions humaines ;
- les reprises et récupérations.

Les blocs internes `<think>...</think>` sont masqués.

LocalCode affiche une justification opérationnelle et des preuves utiles, pas une chaîne de pensée privée complète.

---

## Console interactive

Tape :

```text
/help
```

Commandes disponibles :

```text
/help
/status
/stats
/target M3
/target current
/file add CHEMIN
/file remove CHEMIN
/file clear
/files
/note MESSAGE
/constraint MESSAGE
/ask QUESTION
/answer Q-... MESSAGE
/review MESSAGE
/pause [RAISON]
/resume [MESSAGE]
/revise MESSAGE
/replan MESSAGE
/approval auto|commands|all
/approve A-... [once|run]
/deny A-... [RAISON]
/abort [RAISON]
/detach
```

### Texte naturel

Tout texte ne commençant pas par `/` devient une note durable attachée à la cible active au moment de l’envoi.

```text
Ne remplace pas le format public historique.
```

### Note explicite

```text
/note Vérifie aussi les anciens appelants.
```

### Contrainte dure

```text
/constraint Ne modifie jamais le format public.
```

Une note ou contrainte pertinente doit être vérifiée avant le checkpoint.

### Poser une question à l’agent

```text
/ask Pourquoi ce test est-il nécessaire ?
```

La réponse est produite en lecture seule à partir du dépôt, du run et des preuves disponibles.

### Répondre à une question de l’agent

```text
/answer Q-123 Le test est la source de vérité.
```

### Pause et reprise

```text
/pause Je dois vérifier le contrat.
```

Puis :

```text
/resume Le contrat est confirmé.
```

### Rejouer une cible

```text
/revise Reprends cette micro-tâche avec ce cas obligatoire.
```

Le système revient au checkpoint puis rejoue la cible.

### Demander une décomposition

```text
/replan Cette étape doit être séparée en deux preuves indépendantes.
```

### Quitter seulement la console

```text
/detach
```

Le run continue.

---

## Notes et contraintes humaines

Une intervention est enregistrée avec :

- le run ;
- la cible ;
- un numéro de séquence ;
- l’heure ;
- le message ;
- les fichiers joints ;
- son état de livraison.

États actuellement utilisés :

```text
queued
→ injected
→ verified
```

Le système ne possède pas un état séparé `acknowledged` entre `injected` et `verified`.

Avant un checkpoint, LocalCode applique plusieurs barrières :

1. vérification avant le staging ;
2. nouveau point sûr après le staging ;
3. vérification après la création du commit de checkpoint avant de l’adopter comme nouveau checkpoint.

Une note arrivée très tard doit donc être consommée ou provoquer une révision.

---

## Questions humaines déclenchées par l’agent

Le codeur doit d’abord utiliser le contexte local et les scouts ciblés.

Une question humaine peut être créée lorsque le besoin persiste après les tours de contexte configurés.

Limites par défaut :

```text
2 questions maximum par micro-tâche
5 questions maximum par run
```

Pendant l’attente d’une réponse, la micro-tâche concernée bloque sur la question :

```text
/answer Q-... réponse
```

---

## Autorisation des commandes et modifications

### Point important

`/approval` change la politique générale.

```text
/approval commands
```

`/approve` répond à une demande précise.

```text
/approve A-123 once
```

Ce ne sont pas les mêmes commandes.

### Mode `commands` — mode par défaut

- commande exactement déclarée dans `TASK.md` : automatique ;
- autre commande shell simple : autorisation humaine ;
- édition et création de fichiers : automatiques dans les limites du guard ;
- commande dangereuse : toujours refusée ;
- chaîne shell complexe non déclarée dans `TASK.md` : refusée directement.

### Mode `auto`

- commande connue par les préfixes sûrs ou déclarée dans `TASK.md` : automatique ;
- autre commande simple : autorisation humaine ;
- édition et création : automatiques dans les limites du guard ;
- commande dangereuse ou chaîne complexe non contractuelle : refusée.

### Mode `all`

- commande shell : autorisation humaine ;
- `edit` : autorisation humaine ;
- `write_file` : autorisation humaine ;
- opérations dangereuses : toujours refusées.

### Répondre à une demande

Autoriser une fois :

```text
/approve A-123 once
```

Autoriser la même opération exacte pour le reste du run :

```text
/approve A-123 run
```

Refuser :

```text
/deny A-123 Cette commande n’est pas nécessaire.
```

Le délai par défaut est de 900 secondes. Pendant ce délai, l’opération attend la décision. Après expiration, l’opération est refusée. Selon la phase, l’agent peut classifier ce refus, corriger son approche, replanifier ou terminer en échec.

---

## Garde-fous des outils

LocalCode applique notamment :

- lecture bornée à 160 lignes par appel ;
- recherche bornée à 24 résultats ;
- globs racine massifs refusés ;
- lectures massives refusées ;
- lectures de fichiers par shell refusées ;
- processus shell en arrière-plan refusés ;
- écrasement complet d’un fichier existant par `write_file` refusé ;
- `replace_all` refusé ;
- édition limitée à 14 000 caractères ;
- nouveau fichier limité à 26 000 caractères ;
- commandes dangereuses refusées même après approbation.

Ces règles obligent l’agent à localiser un symbole, lire une petite plage et effectuer une modification ciblée.

---

## Contexte 8K

Chaque modèle utilise une fenêtre de 8192 tokens avec des réserves distinctes.

### `qwen35-9b`

```text
contexte total : 8192
sortie réservée : 2200
outils : 2900
sécurité : 700
```

### `qwen3coder30-iq2`

```text
contexte total : 8192
sortie réservée : 2200
outils : 3600
sécurité : 700
```

### `qwen25coder14-q3`

```text
contexte total : 8192
sortie réservée : 1700
outils : 3100
sécurité : 700
```

Avant chaque appel, LocalCode :

1. estime les tokens ;
2. calcule l’entrée disponible ;
3. compacte le prompt si nécessaire ;
4. archive le prompt complet pour audit ;
5. injecte seulement une version compacte ;
6. recommence avec une compaction plus forte après une erreur de contexte.

Les anciennes preuves restent sur disque sans être réinjectées intégralement.

---

## Résilience Qwen

Les échecs sont classifiés avant décision :

- serveur ;
- contexte ;
- mémoire ;
- JSON ;
- autre erreur Qwen.

Comportement :

- deux retries automatiques au maximum, en plus de l’appel initial ;
- redémarrage du routeur après une erreur serveur ;
- compaction après une erreur de contexte ;
- modèle de secours lorsque cela est sûr ;
- extraction et réparation du JSON ;
- validation du résultat contre le schéma attendu.

### Cas spécial du codeur

Si le codeur a déjà modifié des fichiers avant de produire une sortie invalide :

1. le diff est conservé ;
2. la sortie tronquée est conservée ;
3. un modèle de récupération en lecture seule inspecte l’état ;
4. il reconstruit uniquement le résultat JSON ;
5. les modifications ne sont pas rejouées aveuglément.

---

## Modèles de secours

Configuration par défaut :

```text
qwen35-9b          → qwen25coder14-q3
qwen25coder14-q3   → qwen35-9b
qwen3coder30-iq2   → qwen35-9b
```

Pour un codeur ayant déjà produit des effets de bord, la récupération en lecture seule est prioritaire sur un remplacement aveugle du modèle.

---

## Test de capacité des modèles

Lorsque l’empreinte des modèles, de Qwen ou de la configuration change, LocalCode teste les trois alias uniques.

Les tests couvrent notamment :

- chargement ;
- JSON conforme ;
- lecture ciblée ;
- LSP ;
- édition pour le codeur ;
- absence de modifications inattendues.

Le résultat est mis en cache dans :

```text
.agent-runs/model-capabilities.json
```

---

## Verrouillage et heartbeat

Chaque run possède un verrou :

```text
.agent-runs/<run>/run.lock
```

Il contient notamment :

- le PID ;
- l’heure du heartbeat.

LocalCode refuse de piloter un run déjà contrôlé par un processus encore vivant.

Le routeur possède également sa propre protection contre les démarrages concurrents.

---

## Versions et reproductibilité

Chaque run enregistre notamment :

- plateforme ;
- version Python ;
- version Git ;
- version Qwen Code ;
- version `llama-server` si disponible ;
- commit du kit ;
- empreinte échantillonnée des fichiers GGUF ;
- hash de la configuration ;
- hash des paramètres Qwen.

Cette information améliore le diagnostic et la reproductibilité.

La V9 enregistre les versions réellement utilisées, mais l’installateur n’impose pas encore nécessairement des versions npm et llama.cpp totalement figées.

---

## Arrêt gracieux

Un `Ctrl+C` :

- marque le run comme interrompu ;
- sauvegarde l’état ;
- conserve le worktree ;
- conserve les preuves ;
- affiche la commande de reprise.

Exemple :

```text
INTERRUPTION SAUVEGARDÉE.
Reprise : .\agent.ps1 -Resume TASK-042-...
```

---

## Changement de la branche source

Le run part d’un SHA de base.

Avant le commit final, LocalCode vérifie si le dépôt source a avancé. S’il a changé, il affiche un avertissement indiquant qu’un rebase sera probablement nécessaire avant fusion.

---

## Artefacts d’un run

Dans le worktree :

```text
.agent-context/<run>/
```

Dans le dépôt principal :

```text
.agent-runs/<run>/
```

On trouve notamment :

```text
state.json
run.lock
qwen/
tests/
control/
context-mirror/
compaction/
recovery/
```

Les sorties complètes restent sur disque. Les prompts reçoivent seulement les éléments ciblés ou compactés.

---

## Contrôle depuis un second terminal

Exemples :

```powershell
.\agent-control.ps1 status
.\agent-control.ps1 stats
.\agent-control.ps1 note "Préserver le contrat historique"
.\agent-control.ps1 approve -RequestId A-123 -Scope once
.\agent-control.ps1 deny -RequestId A-123 -Message "Commande inutile"
.\agent-control.ps1 answer "Le test est la source de vérité" -RequestId Q-123
.\agent-control.ps1 pause
.\agent-control.ps1 resume "Contrôle terminé"
.\agent-control.ps1 abort
```

La console principale et `agent-control.ps1` utilisent le même canal de contrôle.

---

## Documentation pilotée par la mission

Le superviseur ne modifie pas automatiquement un README ou un changelog arbitraire.

Dans `AGENT_TASK_META`, Codex peut déclarer :

- `context_files` ;
- `documentation_updates`.

Si `documentation_updates` est vide, aucune documentation finale n’est imposée.

Les documents prévus pour la phase finale sont protégés pendant le codage des micro-tâches.

---

## Décomposition dynamique

Une micro-tâche bloquée peut être remplacée par un fragment borné.

Limites par défaut :

```text
4 enfants maximum
profondeur maximale : 2
1 révision du fragment
3 expansions dynamiques
8 tâches ajoutées au total
```

Chaque enfant doit couvrir explicitement un critère de la tâche parente.

La fonctionnalité reste présente dans le moteur. Le test end-to-end artificiel qui répétait ce scénario a été retiré de la suite, car sa fixture était non déterministe sous Windows. Les validations unitaires et les autres tests restent actifs.

---

## Validation actuelle

Au moment du passage en V9 :

```text
62 tests réussis
```

La suite couvre notamment :

- installation ;
- routeur ;
- exécution de bout en bout ;
- contexte ciblé ;
- reviews et réparations ;
- documentation ;
- contrôle humain ;
- autorisations ;
- garde-fous Windows ;
- reprise et helpers de résilience ;
- configuration V9.

Cette validation vérifie la mécanique du kit. Elle ne garantit pas la qualité intellectuelle réelle d’un modèle GGUF ni la disponibilité de tous les services d’un dépôt cible.

---

## Limites connues

### Implémenté partiellement

- La reprise conserve le run, le worktree, les checkpoints et le diff, mais elle reprend au niveau de la micro-tâche, pas au milieu exact d’une instruction Qwen.
- Les notes utilisent `queued → injected → verified`; il n’existe pas d’état `acknowledged` séparé.
- Les statistiques de tokens sont des estimations.
- L’affichage live montre les actions et summaries disponibles, mais ne garantit pas une justification détaillée pour chaque appel d’outil.
- Les versions sont enregistrées, mais toutes les dépendances de l’installateur ne sont pas nécessairement épinglées.
- La décomposition dynamique existe, mais son ancien test end-to-end Windows instable a été supprimé.

### Volontairement non ajouté dans cette version

- masquage automatique des secrets dans tous les logs ;
- politique automatique d’espace disque ;
- commande `/cleanup` ;
- suppression automatique des anciens runs.

Ne place pas de secrets dans les missions, prompts ou fichiers de contexte tant qu’un mécanisme de redaction complet n’est pas ajouté.

---

## Fichiers principaux de la V9

```text
RESILIENCE_AND_CONTROL.md
template/.microagent/resilience.py
template/.microagent/resilient_orchestrator.py
template/.microagent/tool_guard.py
template/.microagent/schemas/health.schema.json
template/.microagent/schemas/human_ack.schema.json
template/.microagent/schemas/human_answer.schema.json
```

Launchers et contrôles mis à jour :

```text
template/agent.ps1
template/agent.cmd
template/agent.sh
template/agent-control.ps1
template/start-model-router.ps1
template/start-model-router.sh
template/stop-model-router.ps1
```

---

## Première mission recommandée

Commence par une mission importante mais non critique.

Observe :

- la qualité du plan ;
- la précision du scout ;
- la consommation RAM/VRAM ;
- la fréquence des autorisations ;
- les temps de chargement ;
- la conformité JSON ;
- la qualité des reviews ;
- la taille des prompts compactés ;
- le comportement de reprise ;
- la pertinence des questions humaines.

Ajuste ensuite les budgets, timeouts, commandes autorisées et limites de fichiers dans `.microagent/config.json`.
