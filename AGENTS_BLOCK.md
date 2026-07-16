<!-- LOCAL_MICROAGENT_V7_BEGIN -->
## Règles de l’agent local micro-tâches

1. Travailler uniquement dans le worktree fourni par le superviseur.
2. Lire la mission active, la micro-tâche et le paquet du scout avant toute recherche supplémentaire.
3. Quand l’outil `lsp` est disponible, l’utiliser en priorité pour localiser les symboles, définitions, références, implémentations et diagnostics ; utiliser `grep_search` en complément ou si le LSP ne retourne rien.
4. Localiser avec `grep_search`/`glob`, puis lire uniquement de petites plages avec `read_file`.
5. Pour un fichier existant, utiliser `edit` avec un `old_string` unique contenant le contexte voisin ; `write_file` sert uniquement à créer un fichier.
6. Ne jamais aspirer le dépôt, lire des fichiers via le shell ou utiliser `replace_all`.
7. Si le contexte manque, retourner 1 à 3 demandes précises ; le superviseur appellera un scout ciblé.
8. Faire un patch minimal et relancer immédiatement le test ciblé.
9. Après une erreur, lire `stdout`/`stderr`, distinguer commande/environnement/code/périmètre, puis adapter et retester.
10. Ne jamais affaiblir, supprimer ou contourner un test pour obtenir du vert.
11. Ne modifier que la micro-tâche active. Une décomposition M2a/M2b doit être nécessaire à la validation de M2 et validée par le superviseur.
12. Ne jamais modifier `.agent-runs/`, `.agent-context/`, `.microagent/`, `.qwen/`, `.lsp.json`, la mission ou les documents déclarés pour la phase finale.
13. Pour une réparation de review, lire le ticket et les artefacts sources ; traiter seulement les `finding_id` indiqués.
14. Lire et appliquer les artefacts d'intervention humaine fournis par le superviseur ; ne jamais les modifier ou les ignorer.
15. Ne jamais committer : seul le superviseur crée les checkpoints puis le commit final.
<!-- LOCAL_MICROAGENT_V7_END -->
