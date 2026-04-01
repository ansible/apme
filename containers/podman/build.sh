#!/usr/bin/env bash
# Build all APME images with Podman. Run from repo root.
# Builds a shared base image first so pip dependencies are resolved once.
# Usage: build.sh [--no-cache]
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

BUILD_ARGS=()
if [[ "$1" == "--no-cache" ]]; then
  BUILD_ARGS+=(--no-cache)
  echo "==> Building with --no-cache"
fi

echo "==> Building base image (shared dependencies)..."
podman build "${BUILD_ARGS[@]}" -t localhost/apme-base:latest -f containers/base/Dockerfile .

echo "==> Pulling Abbenay AI image..."
podman pull ghcr.io/redhat-developer/abbenay:2026.3.8-alpha

echo "==> Building service images..."
podman build "${BUILD_ARGS[@]}" -t apme-primary:latest -f containers/primary/Dockerfile .
podman build "${BUILD_ARGS[@]}" -t apme-native:latest -f containers/native/Dockerfile .
podman build "${BUILD_ARGS[@]}" -t apme-opa:latest -f containers/opa/Dockerfile .
podman build "${BUILD_ARGS[@]}" -t apme-ansible:latest -f containers/ansible/Dockerfile .
podman build "${BUILD_ARGS[@]}" -t apme-gitleaks:latest -f containers/gitleaks/Dockerfile .
podman build "${BUILD_ARGS[@]}" -t apme-galaxy-proxy:latest -f containers/galaxy-proxy/Dockerfile .
podman build "${BUILD_ARGS[@]}" -t apme-gateway:latest -f containers/gateway/Dockerfile .
podman build "${BUILD_ARGS[@]}" -t apme-cli:latest -f containers/cli/Dockerfile .
podman build "${BUILD_ARGS[@]}" -t apme-ui:latest -f containers/ui/Dockerfile .

echo "Images built. Start with: tox -e up"
