#!/usr/bin/env bash
set -euo pipefail
REPO="${1:-.}"
command -v python3 >/dev/null || { echo "Python 3.10+ requis" >&2; exit 1; }
command -v node >/dev/null || { echo "Node.js 22+ requis" >&2; exit 1; }
command -v ollama >/dev/null || { echo "Ollama requis" >&2; exit 1; }
npm install -g @qwen-code/qwen-code@latest
ollama pull qwen3:8b
ollama pull qwen2.5-coder:7b
PY_FILE="$(git -C "$REPO" ls-files '*.py' | head -n 1 || true)"
if [ -n "$PY_FILE" ]; then
  python3 -m pip install --user --upgrade python-lsp-server
fi
python3 "$(dirname "$0")/install_into_repo.py" "$REPO"
