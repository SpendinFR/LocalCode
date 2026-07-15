#!/usr/bin/env sh
set -eu
if [ "$#" -ne 1 ]; then
  echo "Usage: ./agent.sh .tasks/TASK-XXX.md" >&2
  exit 2
fi
REPO=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python3 "$REPO/.microagent/doctor.py" "$1" --repo "$REPO"
exec python3 "$REPO/.microagent/orchestrator.py" "$1" --repo "$REPO"
