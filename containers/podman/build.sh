#!/usr/bin/env bash
# Build all APME images with Podman. Run from repo root.
# Everything builds inside containers — no Node.js, pnpm, or Python needed on the host.
#
# Usage:
#   ./build.sh              # Build backend + gateway (API-only, no UI)
#   ./build.sh --with-ui    # Build backend + gateway with standalone UI bundled
#
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# --- Backend services ---
podman build -t apme-primary:latest -f containers/primary/Dockerfile .
podman build -t apme-native:latest -f containers/native/Dockerfile .
podman build -t apme-opa:latest -f containers/opa/Dockerfile .
podman build -t apme-ansible:latest -f containers/ansible/Dockerfile .
podman build -t apme-gitleaks:latest -f containers/gitleaks/Dockerfile .
podman build -t apme-cache-maintainer:latest -f containers/cache-maintainer/Dockerfile .
podman build -t apme-cli:latest -f containers/cli/Dockerfile .

# --- API Gateway (multi-stage: Node builds UI, Python runs gateway) ---
BUILD_UI_ARG="0"
if [[ "${1:-}" == "--with-ui" ]]; then
  BUILD_UI_ARG="1"
  echo "Building gateway with standalone UI (multi-stage container build)..."
else
  echo "Building gateway (API-only, no UI)..."
fi
podman build \
  --build-arg BUILD_UI="$BUILD_UI_ARG" \
  -t apme-gateway:latest \
  -f containers/gateway/Dockerfile .

echo ""
echo "All images built successfully."
echo "  Start pod:  ./up.sh"
echo "  Run a scan: ./run-cli.sh"
echo "  API docs:   http://localhost:50050/api/v1/docs"
if [[ "${1:-}" == "--with-ui" ]]; then
  echo "  Dashboard:  http://localhost:50050"
fi
