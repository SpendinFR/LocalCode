# Configuration LSP

Qwen Code peut utiliser les définitions, références, diagnostics et implémentations via LSP, mais le serveur dépend du langage du dépôt.

Utilise de préférence le même serveur que ton IDE et crée `.lsp.json` à la racine du projet selon la documentation Qwen Code. Exemples de serveurs usuels :

- TypeScript/JavaScript : `typescript-language-server` + `typescript` ;
- Python : Pyright ou basedpyright ;
- Rust : rust-analyzer ;
- Go : gopls ;
- C/C++ : clangd ;
- Java : jdtls.

Sans LSP, le kit continue avec le repo map, `grep`, les imports et les tests. Ne copie pas aveuglément une configuration générique : le chemin du binaire et les arguments changent selon ton environnement.
