# Configuration LSP

Le superviseur lance automatiquement tous les agents Qwen avec `--experimental-lsp`.

Pour un dépôt Python, l’installation installe `python-lsp-server`, crée `.lsp.json` sans écraser une configuration existante et demande aux agents d’utiliser l’outil `lsp` avant les recherches textuelles.

Le LSP indexe localement les symboles et répond aux recherches de définitions, références, implémentations et diagnostics sans appel LLM supplémentaire.

Pour les autres langages, configure `.lsp.json` avec le serveur adapté : `typescript-language-server`, `rust-analyzer`, `gopls`, `clangd` ou `jdtls`.

Sans serveur compatible, le kit continue avec le repo map, `grep`, les imports et les tests.
