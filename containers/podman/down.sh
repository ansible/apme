#!/usr/bin/env bash
# Stop the APME pod and optionally wipe the database.
#
# Usage:
#   ./down.sh          # stop pod only
#   ./down.sh --wipe   # stop pod, delete gateway DB/sessions, and Abbenay secrets.json
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "Stopping apme-pod..."
podman pod stop apme-pod 2>/dev/null || true
podman pod rm  apme-pod 2>/dev/null || true
echo "Pod stopped."

if [[ "${1:-}" == "--wipe" ]]; then
  for vol in apme-sessions apme-gateway-data apme-proxy-cache; do
    if podman volume exists "$vol" 2>/dev/null; then
      podman volume rm "$vol"
      echo "Removed volume: $vol"
    else
      echo "No volume found: $vol"
    fi
  done

  # Legacy hostPath cache (pre-PVC local deployments).
  CACHE_PATH="${APME_CACHE_HOST_PATH:-${XDG_CACHE_HOME:-$HOME/.cache}/apme}"

  if [[ "$CACHE_PATH" != /* ]]; then
    echo "ERROR: cache path must be absolute (got: $CACHE_PATH)" >&2
    exit 1
  fi
  if [[ "$CACHE_PATH" == *$'\n'* ]]; then
    echo "ERROR: cache path must not contain newlines" >&2
    exit 1
  fi

  DB_FILE="$CACHE_PATH/gateway/apme.db"
  if [[ -f "$DB_FILE" ]]; then
    rm -f "$DB_FILE" "$DB_FILE-shm" "$DB_FILE-wal"
    echo "Wiped database: $DB_FILE"
  else
    echo "No database found at $DB_FILE"
  fi

  SESSIONS_DIR="$CACHE_PATH/sessions"
  if [[ -d "$SESSIONS_DIR" ]]; then
    rm -rf "$SESSIONS_DIR"
    echo "Wiped session cache: $SESSIONS_DIR"
  else
    echo "No session cache found at $SESSIONS_DIR"
  fi

  ABBENAY_SECRETS="$CACHE_PATH/abbenay/config/secrets.json"
  if [[ -f "$ABBENAY_SECRETS" ]]; then
    rm -f "$ABBENAY_SECRETS"
    echo "Wiped Abbenay file-store secrets: $ABBENAY_SECRETS"
  else
    echo "No Abbenay secrets.json found at $ABBENAY_SECRETS"
  fi
fi
