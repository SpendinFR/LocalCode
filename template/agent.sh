#!/usr/bin/env sh
set -eu
REPO=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if [ "$#" -eq 0 ]; then
  echo "Usage: ./agent.sh .tasks/TASK-XXX.md | ./agent.sh --resume latest" >&2
  exit 2
fi
if [ "${MICROAGENT_SKIP_ROUTER_CHECK:-0}" != "1" ] && [ "${MICROAGENT_SKIP_OLLAMA_CHECK:-0}" != "1" ]; then
  "$REPO/start-model-router.sh"
fi
if [ "$1" = "--resume" ]; then
  [ "$#" -ge 2 ] || { echo "Identifiant de run requis" >&2; exit 2; }
  exec python3 "$REPO/.microagent/interactive.py" --repo "$REPO" --resume "$2"
fi
python3 "$REPO/.microagent/doctor.py" "$1" --repo "$REPO"
exec python3 "$REPO/.microagent/interactive.py" "$1" --repo "$REPO"
