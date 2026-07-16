#!/usr/bin/env sh
set -eu
REPO=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PRESET="$REPO/.microagent/models.ini"
PID_FILE="$REPO/.microagent/llama-router.pid"
LOG_DIR="$REPO/.agent-runs/router"
if curl -fsS -H 'Authorization: Bearer local' http://127.0.0.1:8080/models >/dev/null 2>&1; then
  echo "Routeur llama.cpp déjà actif."
  exit 0
fi
command -v llama-server >/dev/null || { echo "llama-server introuvable" >&2; exit 2; }
[ -f "$PRESET" ] || { echo "Preset absent: $PRESET" >&2; exit 2; }
mkdir -p "$LOG_DIR"
nohup llama-server --models-preset "$PRESET" --models-max 1 --models-autoload \
  --host 127.0.0.1 --port 8080 --api-key local \
  >"$LOG_DIR/llama-router.out.log" 2>"$LOG_DIR/llama-router.err.log" &
echo $! > "$PID_FILE"
for _ in $(seq 1 180); do
  if curl -fsS -H 'Authorization: Bearer local' http://127.0.0.1:8080/models >/dev/null 2>&1; then
    echo "Routeur llama.cpp prêt."
    exit 0
  fi
  sleep 0.5
done
echo "Routeur non prêt; consulte $LOG_DIR/llama-router.err.log" >&2
exit 2
