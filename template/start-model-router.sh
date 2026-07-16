#!/usr/bin/env sh
set -eu
REPO=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PRESET="$REPO/.microagent/models.ini"
LOG_DIR="$REPO/.agent-runs/router"
PID_FILE="$LOG_DIR/llama-router.pid"
LOCK_DIR="$LOG_DIR/start.lock"
if curl -fsS -H 'Authorization: Bearer local' http://127.0.0.1:8080/models >/dev/null 2>&1; then
  echo "Routeur llama.cpp déjà actif."
  exit 0
fi
command -v llama-server >/dev/null || { echo "llama-server introuvable" >&2; exit 2; }
[ -f "$PRESET" ] || { echo "Preset absent: $PRESET" >&2; exit 2; }
mkdir -p "$LOG_DIR"
for _ in $(seq 1 360); do
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    break
  fi
  if curl -fsS -H 'Authorization: Bearer local' http://127.0.0.1:8080/models >/dev/null 2>&1; then
    echo "Routeur démarré par un autre processus."
    exit 0
  fi
  if [ -d "$LOCK_DIR" ] && find "$LOCK_DIR" -maxdepth 0 -mmin +3 -print | grep -q .; then
    rmdir "$LOCK_DIR" 2>/dev/null || true
  fi
  sleep 0.25
done
[ -d "$LOCK_DIR" ] || { echo "Verrou de démarrage du routeur indisponible" >&2; exit 2; }
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT INT TERM
if curl -fsS -H 'Authorization: Bearer local' http://127.0.0.1:8080/models >/dev/null 2>&1; then
  echo "Routeur déjà actif après verrouillage."
  exit 0
fi
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
