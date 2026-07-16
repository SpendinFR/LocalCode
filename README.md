# Local Codex Adaptive Micro-Agent V8 — RTX 3070

Ce kit prend une mission précise créée par Codex et la fait exécuter localement par plusieurs modèles spécialisés, jusqu’à un commit unique, dans une branche et un worktree isolés.

Il est conçu pour une machine disposant d’environ :

* 8 Go de VRAM ;
* 16 Go de RAM ;
* une carte graphique de type RTX 3070 ;
* plusieurs modèles GGUF chargés successivement avec `llama.cpp`.

## Objectif réel

Le but n’est pas d’empiler des agents ni de faire travailler plusieurs gros modèles simultanément.

Le système réduit la difficulté intellectuelle de chaque étape et attribue chaque rôle au modèle le plus adapté :

```text
Codex senior écrit TASK.md
→ planner local découpe en micro-tâches
→ deux audits indépendants contrôlent le plan
→ un juge filtre les critiques inutiles
→ scout fournit seulement les fichiers, symboles et plages utiles
→ codeur réalise une micro-tâche en boucle live
→ l’humain peut intervenir directement depuis le même terminal
→ superviseur relance réellement les tests
→ deux reviewers cherchent les défauts
→ juge filtre les faux positifs
→ ticket de réparation immuable + scout ciblé
→ codeur corrige puis double revue
→ micro-tâche suivante
→ validation globale
→ documentation demandée par Codex uniquement
→ commit unique
```

Les modèles ne sont jamais chargés simultanément. Le routeur `llama-server` conserve au maximum un modèle en mémoire et remplace automatiquement le modèle chargé lorsque le rôle change.

## Répartition des modèles recommandée

La configuration par défaut privilégie la précision et l’indépendance des reviewers.

### Planner, scout et juge

```text
Qwen3.5-9B-Q4_K_M.gguf
```

Alias utilisé :

```text
qwen35-9b
```

Ce modèle est chargé pour :

* comprendre la mission ;
* créer ou corriger le plan ;
* explorer le dépôt en lecture seule ;
* arbitrer les audits ;
* filtrer les findings ;
* trier les erreurs d’environnement, de commande ou de code.

### Codeur principal

```text
Qwen3-Coder-30B-A3B-Instruct-UD-IQ2_XXS.gguf
```

Alias utilisé :

```text
qwen3coder30-iq2
```

Il est utilisé uniquement pour :

* modifier le code ;
* créer les tests demandés ;
* exécuter les commandes autorisées ;
* corriger ses erreurs ;
* traiter les tickets de réparation.

Sa quantification très compressée permet de l’utiliser sur une machine limitée, avec une partie du modèle placée en RAM.

### Reviewers indépendants

```text
Qwen2.5-Coder-14B-Instruct Q3_K_M
```

Alias utilisé :

```text
qwen25coder14-q3
```

Il est utilisé pour :

* la revue logique et architecturale ;
* la revue d’intégration et d’exécution ;
* la vérification des contrats ;
* la recherche de faux positifs dans les tests ;
* la compatibilité plateforme.

Le reviewer n’est volontairement pas le même modèle que le codeur. Cela réduit le risque que le modèle valide ses propres erreurs ou reproduise exactement le même raisonnement incorrect.

## Routeur multi-modèles llama.cpp

Le kit utilise `llama-server` comme fournisseur OpenAI-compatible.

Qwen Code envoie chaque requête vers :

```text
http://127.0.0.1:8080/v1
```

Le champ `model` sélectionne automatiquement l’alias correspondant :

```text
qwen35-9b
qwen3coder30-iq2
qwen25coder14-q3
```

Le routeur est lancé avec une limite d’un seul modèle chargé :

```text
--models-max 1
```

Cela signifie que :

* un seul processus `llama-server` reste actif ;
* un seul modèle occupe la VRAM et la RAM à un instant donné ;
* les modèles sont chargés seulement lorsqu’un rôle les demande ;
* deux reviewers utilisant le même modèle réutilisent le modèle déjà chargé ;
* il n’est pas nécessaire de relancer manuellement le serveur entre chaque étape ;
* Ollama n’est plus nécessaire pour l’inférence des modèles du kit.

Le changement physique de modèle reste inévitable avec seulement 8 Go de VRAM, mais le routeur évite les redémarrages inutiles et garde le serveur disponible entre plusieurs missions.

## Boucle adaptative du codeur

Dans une même session Qwen Code, le codeur peut :

1. localiser un symbole avec le LSP ou une recherche ciblée ;
2. lire une petite plage de code ;
3. modifier uniquement le bloc nécessaire ;
4. lancer un test ;
5. lire `stdout` et `stderr` ;
6. distinguer une erreur de commande, d’environnement ou de code ;
7. corriger ;
8. relancer le test.

S’il manque une information, il retourne une demande précise au superviseur.

Un scout neuf recherche alors uniquement les éléments demandés et fournit au codeur un petit paquet de contexte supplémentaire.

Le codeur n’est jamais obligé de rester dans une liste figée de fichiers. Il peut découvrir une dépendance, mais il doit la localiser par symbole, import, référence ou appel direct.

Un dépassement du plafond de fichiers déclenche une replanification au lieu d’une modification massive.

## LSP automatique

Tous les agents Qwen sont lancés avec :

```text
--experimental-lsp
```

Pour un dépôt Python, l’installation :

* installe `python-lsp-server` ;
* crée `.lsp.json` si aucun fichier n’existe déjà ;
* conserve une configuration LSP personnalisée existante ;
* demande aux agents d’utiliser le LSP avant les recherches textuelles.

Le LSP permet notamment de rechercher localement :

* les définitions ;
* les références ;
* les implémentations ;
* les diagnostics ;
* les symboles du dépôt.

Sans serveur LSP compatible, le kit continue avec le repo map, les imports, les recherches ciblées et les tests.

Pour un langage autre que Python, configure `.lsp.json` avec le serveur adapté, par exemple :

* TypeScript ou JavaScript : `typescript-language-server` ;
* Rust : `rust-analyzer` ;
* Go : `gopls` ;
* C ou C++ : `clangd` ;
* Java : `jdtls`.

## Circuit de review sans « téléphone arabe »

Les remarques ne sont pas reformulées de modèle en modèle.

Le circuit est le suivant :

1. le reviewer fournit le fichier, le symbole, le scénario, l’attendu, l’observé, la preuve, le test requis et le lien avec la mission ;
2. le juge vérifie le finding dans le vrai code ;
3. le superviseur attribue un `finding_id` stable ;
4. le superviseur crée un ticket contenant les findings acceptés, le diff, les revues originales et les résultats des tests ;
5. un scout localise uniquement les causes et appels associés ;
6. le codeur reçoit le ticket et les sources originales ;
7. le codeur doit déclarer les `finding_id` résolus ou encore bloqués ;
8. les tests et les reviewers sont rejoués sur le diff complet de la micro-tâche.

Si le même finding survit à une réparation, le système n’enchaîne pas les corrections aveugles. Il revient au checkpoint et demande au planner de redécouper le travail.

## Documentation pilotée uniquement par Codex

Le superviseur n’impose aucun nom de document.

Dans `AGENT_TASK_META`, Codex peut déclarer :

* `context_files` : documents utiles pendant le plan, le scout ou la revue ;
* `documentation_updates` : fichiers à modifier à la fin, avec instruction, marqueurs, autorisation de création et obligation de changement.

Si `documentation_updates` est vide, aucune documentation n’est modifiée.

Les documents prévus pour la phase finale sont protégés pendant le codage des micro-tâches.

## Décomposition dynamique bornée

Le plan initial contient des tâches petites et vérifiables :

```text
M1
M2
M3
```

Si les preuves montrent que `M2` ne peut pas être validée telle quelle, le planner peut remplacer uniquement cette tâche par un fragment :

```text
M2a → M2b
```

Chaque enfant doit :

* être nécessaire à la validation de la tâche parente ;
* couvrir explicitement un ou plusieurs critères d’acceptation ;
* rester petit ;
* avoir des tests ciblés ;
* conserver les objectifs suivants inchangés.

La liberté est bornée :

* 4 enfants maximum par expansion ;
* profondeur maximale de 2 ;
* une révision du fragment ;
* 3 expansions dynamiques ;
* 8 tâches ajoutées au total.

Les tâches déjà validées et les objectifs en aval ne peuvent pas être réécrits.

## Protection du contexte et du temps

Les règles par défaut sont notamment :

* `read_many_files` interdit ;
* `read_file` limité à 180 lignes par appel ;
* `grep_search` limité à 30 résultats ;
* globs racine massifs interdits ;
* lectures massives par shell bloquées ;
* scout limité à 10 fichiers ;
* micro-tâche prévue pour environ 1 à 4 fichiers ;
* 6 fichiers probables maximum ;
* replanification si plus de 8 fichiers sont modifiés ;
* deux demandes de contexte complémentaires maximum ;
* deux réparations maximum avant blocage ou replanification ;
* un seul modèle chargé à la fois ;
* 100 interventions humaines enregistrables par run ;
* 12 résumés humains injectés à la fois ;
* 12 000 caractères humains maximum dans le prompt ;
* 3 révisions humaines maximum par cible ;
* 3 replans humains maximum ;
* 3 reprises globales maximum.

Les tests, builds, linters et typecheckers restent exécutables par shell lorsqu’ils sont autorisés par la mission.

## Installation

### Prérequis

Le kit nécessite :

* Git ;
* Python 3.10 ou plus récent ;
* Node.js 22 ou plus récent ;
* Qwen Code ;
* une version récente de `llama.cpp` contenant `llama-server` ;
* suffisamment d’espace disque pour les GGUF.

L’installation peut installer automatiquement Qwen Code, llama.cpp, le LSP Python et les modèles recommandés.

### Windows PowerShell

Extrais le kit puis ouvre PowerShell dans son dossier :

```powershell
Expand-Archive .\local-codex-adaptive-microagent-v8.zip
cd .\local-codex-adaptive-microagent-v8
```

Installe le kit dans ton dépôt :

```powershell
.\install.ps1 -Repo "C:\chemin\vers\ton-repo"
```

Par défaut, l’installateur propose d’installer ou de configurer les modèles recommandés.

Valide avec Entrée pour accepter le choix par défaut.

### Utiliser des modèles déjà téléchargés

Si tes GGUF sont déjà présents dans un dossier :

```powershell
.\install.ps1 `
  -Repo "C:\chemin\vers\ton-repo" `
  -ExistingModelsDir "D:\Models"
```

Pour indiquer directement ton fichier Qwen3.5 :

```powershell
.\install.ps1 `
  -Repo "C:\chemin\vers\ton-repo" `
  -Models yes `
  -Qwen35Path "D:\Models\Qwen3.5-9B-Q4_K_M.gguf"
```

L’installateur recherche également les modèles déjà présents dans les dossiers habituels, notamment `Downloads`.

### Ne pas installer les modèles maintenant

```powershell
.\install.ps1 `
  -Repo "C:\chemin\vers\ton-repo" `
  -Models no
```

Le kit est alors installé sans télécharger les gros fichiers GGUF. Ils pourront être configurés plus tard.

### Linux ou macOS

```bash
unzip local-codex-adaptive-microagent-v8.zip
cd local-codex-adaptive-microagent-v8
./install.sh /chemin/vers/ton-repo
```

L’installation ajoute notamment :

* le superviseur ;
* la configuration Qwen Code ;
* les launchers ;
* le modèle de mission ;
* la console interactive ;
* le contrôle humain externe ;
* le routeur de modèles ;
* la configuration LSP ;
* les tests du kit.

Committe une fois le kit installé dans ton dépôt avant la première mission.

## Lancer le routeur de modèles

Le routeur peut être démarré séparément :

```powershell
.\start-model-router.ps1
```

Il reste actif entre les missions.

Pour vérifier les modèles disponibles :

```powershell
Invoke-RestMethod `
  "http://127.0.0.1:8080/v1/models" `
  -Headers @{ Authorization = "Bearer local" } |
  ConvertTo-Json -Depth 8
```

Pour arrêter le routeur :

```powershell
.\stop-model-router.ps1
```

En utilisation normale, `agent.ps1` vérifie le routeur et le démarre automatiquement s’il n’est pas déjà disponible.

## Utilisation en une commande

Après que Codex a créé une mission, par exemple :

```text
.tasks/TASK-042.md
```

lance depuis la racine du dépôt :

```powershell
.\agent.ps1 .tasks\TASK-042.md
```

Sous `cmd.exe` :

```bat
agent.cmd .tasks\TASK-042.md
```

Sous Linux ou macOS :

```bash
./agent.sh .tasks/TASK-042.md
```

La commande :

1. vérifie l’environnement ;
2. vérifie ou démarre le routeur ;
3. crée une branche et un worktree isolés ;
4. crée le plan ;
5. exécute les micro-tâches ;
6. relance les tests ;
7. réalise les reviews ;
8. crée les réparations nécessaires ;
9. effectue la validation globale ;
10. produit un commit final.

Elle ne fusionne et ne pousse rien automatiquement.

En cas de succès, elle affiche notamment :

* la branche ;
* le worktree ;
* le SHA final ;
* le dossier contenant les preuves.

En cas d’échec, le worktree, l’état, les prompts, les diffs, les tests et les preuves sont conservés dans :

```text
.agent-runs/
```

## Console interactive dans le même PowerShell

Le terminal utilisé pour lancer `agent.ps1` devient également une console de contrôle humain.

Tape :

```text
/help
```

pour afficher les commandes disponibles et leur effet.

### Texte naturel

Tout texte ne commençant pas par `/` devient une note durable visant la tâche ou la phase courante.

Exemple :

```text
Vérifie aussi le cas stop/go et conserve le comportement historique.
```

Cette phrase est enregistrée comme une intervention humaine puis injectée dans le contexte des agents concernés.

L’agent prend donc cette instruction en compte lors de ses prochains points de contrôle.

Ce fonctionnement n’est pas un chat conversationnel complet :

* le texte est une instruction persistante ;
* l’agent ne répond pas nécessairement immédiatement ;
* il peut accuser réception indirectement dans ses événements ou dans le résultat de la tâche ;
* une note ne crée aucune nouvelle tâche ;
* une note ne force pas une interruption.

Il s’agit d’un canal d’intervention one-shot persistant, comparable à une instruction ajoutée en direct, et non d’un dialogue libre permanent.

## Commandes interactives

### Afficher l’aide

```text
/help
```

### Afficher l’état actuel

```text
/status
```

Affiche notamment :

* le run ;
* la phase ;
* le scope actif ;
* la micro-tâche active ;
* l’opération active ;
* le statut du contrôle ;
* le chemin du worktree.

### Ajouter explicitement une note

```text
/note Vérifie aussi les appelants historiques.
```

Un texte sans `/` produit le même résultat.

### Mettre en pause

```text
/pause
```

Ou avec une explication :

```text
/pause Je vais modifier manuellement le worktree.
```

Le sous-processus actif est interrompu proprement, tandis que le worktree, l’état et les preuves restent conservés.

### Reprendre

```text
/resume
```

Ou :

```text
/resume J’ai corrigé manuellement la configuration Windows.
```

La reprise conserve les modifications actuellement présentes dans le worktree.

### Imposer une revue supplémentaire

```text
/review Contrôle obligatoirement les anciens appelants et la compatibilité Windows.
```

Un reviewer supplémentaire doit alors être exécuté avant que la cible puisse être validée.

### Rejouer la cible depuis le checkpoint

```text
/revise Reprends cette micro-tâche avec le scénario stop/go obligatoire.
```

Cette commande :

* capture le diff actuel ;
* revient au dernier checkpoint ;
* rejoue la cible ;
* injecte l’instruction humaine.

Utilise `revise` lorsque tu veux repartir proprement plutôt que conserver le diff en cours.

### Demander une décomposition bornée

```text
/replan Décompose uniquement cette étape car elle ne peut pas être validée atomiquement.
```

Le système revient au checkpoint et permet uniquement la décomposition contrôlée de la tâche active.

### Arrêter le run

```text
/abort
```

Ou :

```text
/abort Le service externe requis est indisponible.
```

Le run est arrêté, mais le worktree, l’état et les preuves sont conservés.

## Cibler une tâche future

Par défaut, les interventions visent :

```text
current
```

Pour cibler une future tâche :

```text
/target M3
```

Les prochaines notes et commandes viseront `M3`.

Pour revenir à la tâche active :

```text
/target current
```

Une intervention visant une tâche future est mise en attente puis déclenchée lorsque cette tâche devient active.

Pour cibler la validation globale :

```text
/target global
```

## Ajouter un fichier de contexte

Pour joindre un fichier aux prochaines interventions :

```text
/file add docs\architecture.md
```

Pour afficher les fichiers actuellement joints :

```text
/files
```

Pour retirer un fichier :

```text
/file remove docs\architecture.md
```

Pour vider la liste :

```text
/file clear
```

Le chemin doit être relatif au dépôt et exister dans le dépôt principal ou dans le worktree.

## Contrôle depuis un second terminal

L’ancien mécanisme reste disponible.

Depuis un second PowerShell ouvert à la racine du dépôt :

```powershell
.\agent-control.ps1 status
.\agent-control.ps1 note "Vérifie aussi le cas stop/go"
.\agent-control.ps1 review "Contrôle les appelants historiques"
.\agent-control.ps1 pause
.\agent-control.ps1 resume "Modification manuelle terminée"
.\agent-control.ps1 revise "Reprends avec ce scénario obligatoire"
.\agent-control.ps1 replan "Décompose seulement la tâche active"
.\agent-control.ps1 abort
```

Tu peux également cibler une autre tâche :

```powershell
.\agent-control.ps1 note "Préserver ce contrat" -Target M3
.\agent-control.ps1 review "Vérifier Windows" -Target global
.\agent-control.ps1 note "Lire aussi ce fichier" -File docs\architecture.md
```

Les deux interfaces utilisent le même canal de contrôle et produisent les mêmes enregistrements.

## Sémantique des interventions

* `note` ajoute une instruction durable sans interrompre le travail ;
* `review` impose un reviewer supplémentaire ;
* `pause` interrompt le sous-processus actif et attend `resume` ;
* `resume` reprend avec le diff actuel ;
* `revise` revient au checkpoint puis rejoue la cible ;
* `replan` revient au checkpoint puis autorise une décomposition bornée ;
* `abort` arrête le run en conservant son état ;
* `status` affiche l’état actuel.

Les interventions sont enregistrées dans :

```text
.agent-runs/<run>/control/
```

Elles sont :

* recopiées dans le paquet de contexte ;
* conservées dans `state.json` ;
* horodatées ;
* associées à une cible ;
* limitées pour éviter une croissance incontrôlée des prompts.

## Modifier manuellement le code pendant un run

Procédure recommandée :

```text
/status
/pause Je vais modifier manuellement le worktree.
```

Ouvre ensuite le chemin `Worktree` affiché par `/status`, effectue ta modification, puis :

```text
/resume J’ai ajouté la correction manuelle demandée.
```

Le système inspectera le diff déjà présent avant de continuer.

Pour abandonner le diff manuel et repartir du checkpoint :

```text
/revise Ignore le diff précédent et reprends avec cette instruction.
```

## Préparer la mission Codex

Utilise :

```text
CODEX_PROMPT_TASK.md
```

Codex doit analyser le dépôt et remplir :

```text
.tasks/TASK_TEMPLATE.md
```

sans placeholder.

La mission doit notamment contenir :

* le but exact ;
* les critères d’acceptation ;
* les invariants ;
* les chemins interdits ;
* les commandes de tests ciblés ;
* les commandes de lint ;
* le typecheck ;
* le build ;
* la suite complète ;
* les fichiers de contexte éventuels ;
* les documents éventuellement à mettre à jour.

## Revue finale Codex

Le kit local termine sur un commit prêt à être jugé.

Le fichier :

```text
CODEX_PROMPT_FINAL_REVIEW.md
```

fournit un prompt de revue senior.

Cette étape n’est pas lancée automatiquement, car elle dépend de l’installation et de l’authentification Codex.

Une revue refusée nécessite également une décision humaine :

* nouvelle passe locale ;
* nouvelle mission ;
* modification du plan ;
* correction manuelle ;
* abandon du run.

## Ce qui reste à ajuster sur chaque dépôt

À régler dès la première mission réelle :

* les commandes exactes de la stack ;
* les timeouts ;
* les services externes nécessaires ;
* les fichiers de contexte ;
* les mises à jour documentaires ;
* la configuration LSP des langages non-Python ;
* les fichiers générés à ignorer ;
* le plafond de fichiers par micro-tâche ;
* le contexte 8K selon la mémoire disponible ;
* le nombre de couches GPU de chaque modèle ;
* la fiabilité JSON réelle de chaque GGUF ;
* les performances réelles du routeur sur la machine.

Les valeurs `n-gpu-layers` fournies sont des points de départ prudents. Elles peuvent être augmentées progressivement si la VRAM reste stable.

## Validation du kit

La mécanique du kit couvre notamment :

* installation ;
* configuration multi-modèles ;
* routeur `llama.cpp` ;
* un seul modèle chargé à la fois ;
* plan audité ;
* exécution complète ;
* demande de scout ;
* replanification ;
* ticket de finding ;
* réparation ;
* double revue ;
* documentation ;
* commit unique ;
* canal de contrôle atomique ;
* console interactive ;
* texte naturel transformé en note ;
* pause et reprise ;
* note injectée ;
* révision avec rollback ;
* replan humain borné ;
* reviewer humain obligatoire ;
* configuration LSP automatique.

Cela valide la mécanique, pas le niveau intellectuel réel des modèles.

La première mission sur un nouveau dépôt doit être importante mais non critique, afin de mesurer :

* la qualité du plan ;
* la précision des modifications ;
* la conformité JSON ;
* la durée des chargements ;
* la consommation VRAM et RAM ;
* la pertinence des reviewers ;
* la qualité des tests ;
* la taille optimale des micro-tâches.
