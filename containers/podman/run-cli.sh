#!/usr/bin/env bash
# Run CLI container on-the-fly with current directory mounted at /workspace.
# Joins the apme-pod network so it can reach Primary. Run from any directory you want to scan.
# Usage: run-cli.sh [apme-scan args...]
# Example: run-cli.sh
# Example: run-cli.sh --json .
# Example: run-cli.sh --no-native .
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Ensure image exists
podman image exists apme-cli:latest 2>/dev/null || { echo "Run containers/podman/build.sh first."; exit 1; }
# Primary is reachable at localhost:50051 when in the same pod
podman run --rm \
  --pod apme-pod \
  -v "$(pwd)":/workspace:ro,Z \
  -w /workspace \
  -e APME_PRIMARY_ADDRESS=127.0.0.1:50051 \
  apme-cli:latest \
  "${@:-.}"
