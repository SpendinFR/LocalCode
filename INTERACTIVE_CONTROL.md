# Intervention humaine dans le même PowerShell

`agent.ps1` ouvre une console interactive pendant le run. Tout texte normal devient une `note` sur la tâche courante.

```text
Vérifie aussi le cas stop/go
/status
/pause je modifie le worktree
/resume modification terminée
/review contrôle les appelants historiques
/revise reprends avec ce scénario obligatoire
/replan décompose seulement la tâche active
/abort
```

`/target M3` cible une tâche future ; `/target current` revient à la cible active. `/file add`, `/file remove`, `/file clear` et `/files` gèrent les fichiers de contexte. `/help` décrit toutes les commandes. L'ancien `agent-control.ps1` reste utilisable depuis un second terminal.
