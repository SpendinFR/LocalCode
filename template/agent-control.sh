#!/usr/bin/env sh
set -eu
REPO=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$REPO/.microagent/control.py" "$@" --repo "$REPO"
