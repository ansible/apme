#!/usr/bin/env bash
# Build all APME images with Podman. Run from repo root.
# Usage: ./build.sh          # engine images only
#        ./build.sh --ui     # engine + gateway + UI images
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Engine pod images (always built)
podman build -t apme-primary:latest -f containers/primary/Dockerfile .
podman build -t apme-native:latest -f containers/native/Dockerfile .
podman build -t apme-opa:latest -f containers/opa/Dockerfile .
podman build -t apme-ansible:latest -f containers/ansible/Dockerfile .
podman build -t apme-gitleaks:latest -f containers/gitleaks/Dockerfile .
podman build -t apme-galaxy-proxy:latest -f containers/galaxy-proxy/Dockerfile .
podman build -t apme-cli:latest -f containers/cli/Dockerfile .
echo "Engine images built."

# UI images (gateway + standalone SPA) — built with --ui flag
if [[ "${1:-}" == "--ui" ]]; then
  podman build -t apme-gateway:latest -f containers/gateway/Dockerfile .
  podman build -t apme-ui:latest -f containers/ui/Dockerfile .
  echo "Gateway + UI images built."
fi

echo "Done. Start pod: containers/podman/up.sh"
