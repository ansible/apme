#!/usr/bin/env bash
# Build optional ADR-042 plugin sidecar images (not part of tox -e build).
# Run from repo root via: tox -e build-plugins
# Usage: build-plugins.sh [--no-cache]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BUILD_ARGS=()
if [[ "${1:-}" == "--no-cache" ]]; then
  BUILD_ARGS+=(--no-cache)
  echo "==> Building plugin sidecars with --no-cache"
fi

if ! podman image exists localhost/apme-base:latest; then
  echo "==> apme-base missing; building it first..."
  podman build "${BUILD_ARGS[@]}" -t localhost/apme-base:latest -f containers/base/Dockerfile .
fi

echo "==> Building apme-plugin-opa-custom:latest..."
podman build "${BUILD_ARGS[@]}" -t apme-plugin-opa-custom:latest \
  -f examples/plugins/opa-custom/Dockerfile .

echo "==> Building apme-plugin-secscan:latest..."
podman build "${BUILD_ARGS[@]}" -t apme-plugin-secscan:latest \
  -f examples/plugins/secscan/Dockerfile .

echo "Plugin images built."
echo "Uncomment the sidecar blocks in containers/podman/pod.yaml, then: tox -e down && tox -e up"
echo "Guide: docs/guides/PLUGIN_SIDECARS.md"
