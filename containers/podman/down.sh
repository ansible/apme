#!/usr/bin/env bash
# Stop everything: engine pod + gateway + UI containers.
# Usage: ./down.sh [--wipe]
#   --wipe  Also delete the gateway SQLite database
set -euo pipefail

WIPE=""
for arg in "$@"; do
  case "$arg" in
    --wipe) WIPE=1 ;;
  esac
done

echo "Stopping UI containers..."
podman rm -f apme-gateway apme-ui 2>/dev/null || true

echo "Stopping engine pod..."
podman pod stop apme-pod 2>/dev/null || true
podman pod rm -f apme-pod 2>/dev/null || true

if [[ -n "$WIPE" ]]; then
  GATEWAY_DATA="${APME_GATEWAY_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/apme/gateway}"
  if [[ -f "$GATEWAY_DATA/apme.db" ]]; then
    rm -f "$GATEWAY_DATA/apme.db"
    echo "Wiped gateway database: $GATEWAY_DATA/apme.db"
  else
    echo "No database to wipe at $GATEWAY_DATA/apme.db"
  fi
fi

echo "All APME containers stopped."
