#!/usr/bin/env bash
# Start the APME pod (Primary, validators, cache maintainer). Run from repo root.
# CLI is not part of the pod; use run-cli.sh to run a scan with CWD mounted.
# With --ui: also start the gateway and standalone UI containers outside the pod.
#
# Cache host path: default is XDG cache (${XDG_CACHE_HOME:-$HOME/.cache}/apme).
# Override: APME_CACHE_HOST_PATH=/my/cache ./up.sh
# Usage: ./up.sh            # engine pod only
#        ./up.sh --ui       # engine pod + gateway + UI containers
#
# Port overrides: APME_GATEWAY_PORT (default 8080), APME_UI_PORT (default 8081)
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Source .env if present (port overrides, cache paths, etc.)
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

START_UI=
case "${1:-}" in
  --ui) START_UI=1 ;;
  "") ;;
  *) echo "Usage: $0 [--ui]"; exit 1 ;;
esac

# Default: XDG cache dir (persists across reboots); override with APME_CACHE_HOST_PATH
CACHE_PATH="${APME_CACHE_HOST_PATH:-${XDG_CACHE_HOME:-$HOME/.cache}/apme}"

if [[ "$CACHE_PATH" != /* ]]; then
  echo "ERROR: APME_CACHE_HOST_PATH must be an absolute path (got: $CACHE_PATH)" >&2
  exit 1
fi

if [[ "$CACHE_PATH" == *$'\n'* ]]; then
  echo "ERROR: APME_CACHE_HOST_PATH must not contain newlines" >&2
  exit 1
fi

mkdir -p "$CACHE_PATH"

GATEWAY_PORT="${APME_GATEWAY_PORT:-8080}"
UI_PORT="${APME_UI_PORT:-8081}"

# Pod YAML cannot use env vars; we always inject the resolved path.
# Escape \, &, and the | delimiter so sed substitution is safe.
ESCAPED_PATH=$(printf '%s\n' "$CACHE_PATH" | sed -e 's/\\/\\\\/g' -e 's/[&|]/\\&/g')
sed "s|path: __APME_CACHE_PATH__|path: ${ESCAPED_PATH}|" containers/podman/pod.yaml \
  | podman play kube -

echo "Pod apme-pod started (cache: $CACHE_PATH)."

# Start gateway + UI containers outside the pod (--ui)
if [[ -n "$START_UI" ]]; then
  # Create a Podman network so gateway and UI can talk to each other
  # and the gateway can reach Primary on the host network.
  podman network exists apme-ui-net 2>/dev/null || podman network create apme-ui-net

  # Stop previous instances if running
  podman rm -f apme-gateway 2>/dev/null || true
  podman rm -f apme-ui 2>/dev/null || true

  # Gateway container — connects to Primary via host port 50051
  GATEWAY_DATA="${APME_GATEWAY_DATA:-${XDG_DATA_HOME:-$HOME/.local/share}/apme/gateway}"
  mkdir -p "$GATEWAY_DATA"

  podman run -d \
    --name apme-gateway \
    --network apme-ui-net \
    -p "$GATEWAY_PORT":8080 \
    -v "$GATEWAY_DATA":/data:Z \
    -e APME_PRIMARY_ADDRESS=host.containers.internal:50051 \
    -e APME_DATABASE_URL="sqlite+aiosqlite:////data/apme.db" \
    -e APME_GATEWAY_HOST=0.0.0.0 \
    -e APME_GATEWAY_PORT=8080 \
    apme-gateway:latest

  # UI container — serves SPA, proxies /api to gateway
  podman run -d \
    --name apme-ui \
    --network apme-ui-net \
    -p "$UI_PORT":8081 \
    apme-ui:latest

  echo "Gateway running on http://localhost:$GATEWAY_PORT (API)"
  echo "UI running on http://localhost:$UI_PORT (Dashboard)"
fi

echo "Run a scan: containers/podman/run-cli.sh"
