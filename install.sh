#!/usr/bin/env bash
set -euo pipefail
REPO="${1:-.}"
MODELS="${LOCALCODE_MODELS:-ask}"
MODELS_DIR="${LOCALCODE_MODELS_DIR:-$HOME/.local/share/localcode/models}"
command -v python3 >/dev/null || { echo "Python 3.10+ requis" >&2; exit 1; }
command -v node >/dev/null || { echo "Node.js 22+ requis" >&2; exit 1; }
command -v git >/dev/null || { echo "Git requis" >&2; exit 1; }
if [ "$MODELS" = ask ]; then
  printf 'Installer/configurer les 3 modèles recommandés ? [O/n] '
  read -r answer || true
  case "${answer:-o}" in [Nn]*) MODELS=no ;; *) MODELS=yes ;; esac
fi
npm install -g @qwen-code/qwen-code@latest
PY_FILE="$(git -C "$REPO" ls-files '*.py' | head -n 1 || true)"
if [ -n "$PY_FILE" ]; then python3 -m pip install --user --upgrade python-lsp-server; fi
python3 "$(dirname "$0")/install_into_repo.py" "$REPO"
if [ "$MODELS" = yes ]; then
  command -v llama-server >/dev/null || { echo "llama-server requis; installe une version récente de llama.cpp" >&2; exit 1; }
  python3 -m pip install --user --upgrade huggingface_hub
  python3 "$(dirname "$0")/setup_models.py" --repo "$REPO" --models-dir "$MODELS_DIR" \
    --existing-dir "$HOME/Downloads" --existing-dir "$HOME/.cache/lm-studio/models"
fi
echo "Installation terminée. Lance ./agent.sh .tasks/TASK-XXX.md"
