# Modèles locaux et routeur llama.cpp

Configuration recommandée pour 8 Go de VRAM et 16 Go de RAM :

- `qwen35-9b` — `Qwen3.5-9B-Q4_K_M.gguf` : planner, scout et juge ;
- `qwen3coder30-iq2` — `Qwen3-Coder-30B-A3B-Instruct-UD-IQ2_XXS.gguf` : codeur ;
- `qwen25coder14-q3` — Qwen2.5-Coder-14B-Instruct Q3_K_M : reviewers indépendants.

L'installation demande `Installer/configurer ces modèles maintenant ? [O/n]`. Entrée seule choisit Oui. Pour automatiser :

```powershell
.\install.ps1 -Repo C:\projet -Models yes
.\install.ps1 -Repo C:\projet -Models no
```

Le fichier Qwen3.5 existant est recherché dans Downloads, LM Studio et les chemins passés via `-ExistingModelsDir`. Tu peux donner son chemin exact avec `-Qwen35Path`.

`start-model-router.ps1` lance `llama-server` en mode routeur avec `--models-max 1`. Le processus reste actif entre les missions ; seul le modèle demandé est chargé. `stop-model-router.ps1` l'arrête.

Les valeurs `n-gpu-layers` sont prudentes et modifiables dans `.microagent/models.ini`.
