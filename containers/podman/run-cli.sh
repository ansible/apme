#!/usr/bin/env bash
# Run CLI container on-the-fly with current directory mounted at /workspace.
# Joins the apme-pod network so it can reach Primary. Run from any directory you want to scan.
# Usage: run-cli.sh [subcommand] [args...]
# Example: run-cli.sh                     # scan . (default)
# Example: run-cli.sh scan --json .       # scan with JSON output
# Example: run-cli.sh fix --check .       # dry-run fix
# Example: run-cli.sh fix .              # apply Tier 1 fixes
# Example: run-cli.sh health-check       # check all services
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# Ensure image exists
podman image exists apme-cli:latest 2>/dev/null || { echo "Run containers/podman/build.sh first."; exit 1; }
# Primary is reachable at localhost:50051 when in the same pod.
# Avoid --rm: podman has a race condition removing pod-joined containers
# ("cannot remove container ... as it is running"). Instead, create with a
# name, capture the exit code, then force-remove.
# --restart=no prevents pod's "Always" restart policy from re-running the
# CLI after it exits, which would cause phantom duplicate scans.
CLI_NAME="apme-cli-$$"
trap 'podman rm -f "$CLI_NAME" >/dev/null 2>&1 || true' EXIT
# Default to "scan ." when no args provided (any arg overrides Dockerfile CMD)
if [ $# -eq 0 ]; then
  set -- scan .
fi
rc=0
podman run \
  --name "$CLI_NAME" \
  --pod apme-pod \
  --restart=no \
  -v "$(pwd)":/workspace:Z \
  -w /workspace \
  -e APME_PRIMARY_ADDRESS=127.0.0.1:50051 \
  apme-cli:latest \
  "$@" || rc=$?
exit "$rc"
