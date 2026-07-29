#!/usr/bin/env bash
# Stop the Prometheus + Grafana companion stack.
# Usage: ./down.sh [--wipe]   # --wipe also deletes persisted TSDB
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WIPE=0
for arg in "$@"; do
  case "$arg" in
    --wipe) WIPE=1 ;;
    *) echo "Unknown arg: $arg (expected --wipe)" >&2; exit 1 ;;
  esac
done

if podman pod exists apme-observability 2>/dev/null; then
  podman pod stop apme-observability 2>/dev/null || true
  podman pod rm -f apme-observability
  echo "apme-observability stopped."
else
  echo "apme-observability not running."
fi

if [[ "$WIPE" -eq 1 ]]; then
  PROM_DATA="${APME_PROM_DATA:-${XDG_CACHE_HOME:-$HOME/.cache}/apme/prometheus-tsdb}"
  if [[ -d "$PROM_DATA" ]]; then
    rm -rf "$PROM_DATA"
    echo "Wiped Prometheus TSDB: $PROM_DATA"
  else
    echo "No TSDB dir to wipe at $PROM_DATA"
  fi
fi
