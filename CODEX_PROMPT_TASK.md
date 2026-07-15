# Prompt à donner à Codex pour préparer une mission locale

Analyse le dépôt réel et crée `.tasks/TASK-XXX.md` à partir de `.tasks/TASK_TEMPLATE.md`.

La mission sera exécutée par plusieurs petits modèles locaux sous supervision. Elle doit être précise, vérifiée dans le dépôt, mais ne doit pas imposer des numéros de ligne fragiles ni pré-découper artificiellement toutes les micro-tâches.

Fournis obligatoirement :

- objectif observable et comportement actuel ;
- faits prouvés dans le dépôt, en distinguant clairement les hypothèses ;
- carte du flux multifichier : entrée, appels, état, sortie et erreurs ;
- fichiers et symboles probablement concernés, avec leurs relations ;
- invariants métier et techniques ;
- ordre logique recommandé, sans rédiger le code ;
- cas limites, concurrence, sécurité et compatibilité pertinents ;
- critères d'acceptation stables, précis et vérifiables ;
- commandes exactes réellement exécutables dans ce dépôt : tests ciblés, typecheck, lint, build et suite complète selon ce qui existe ;
- chemins interdits et modifications hors périmètre ;
- message de commit final.

Dans `AGENT_TASK_META` :

- remplis `context_files` uniquement avec les documents réellement utiles à lire ;
- remplis `documentation_updates` uniquement si la mission exige une mise à jour documentaire ;
- pour chaque document demandé, indique le chemin, une instruction précise, des marqueurs vérifiables si possible, `must_change` et `allow_create` ;
- laisse ces deux listes vides si aucune lecture ou mise à jour documentaire particulière n'est nécessaire ;
- ne laisse aucun placeholder dans les commandes.

Les chemins cités sont des pistes : le planner local doit pouvoir découvrir des dépendances supplémentaires. Évite de figer toi-même une longue liste de sous-tâches ; donne plutôt les objectifs, relations, invariants et preuves permettant au planner local de produire des unités `M1`, `M2`, `M3` petites et vérifiables.

Chaque invariant et critère important doit avoir un libellé stable afin que les reviewers puissent le citer sans reformulation.
