#!/usr/bin/env bash
# Verify UBI service images can write to mounted volume paths as UID 1001 (ADR-061).
# Run after image build; invoked from containers/podman/build.sh.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

RUNTIME_UID="${APME_UBI_RUNTIME_UID:-1001}"
PROBE_ROOT="$(mktemp -d)"
cleanup() { rm -rf "$PROBE_ROOT"; }
trap cleanup EXIT

prep_dir() {
  local dir=$1
  mkdir -p "$dir"
  if ! chown "${RUNTIME_UID}:0" "$dir" 2>/dev/null; then
    chmod 1777 "$dir"
  fi
}

check_write() {
  local image=$1
  local mount_path=$2
  local host_dir=$3

  if ! podman image exists "$image" 2>/dev/null; then
    echo "==> Skip volume check for $image (image not built)"
    return 0
  fi

  echo "==> Volume write check: $image -> $mount_path (UID $RUNTIME_UID)"
  podman run --rm \
    --user "${RUNTIME_UID}" \
    --entrypoint sh \
    -v "${host_dir}:${mount_path}:Z" \
    "$image" \
    -c "touch '${mount_path}/.write-test' && test -f '${mount_path}/.write-test' && rm -f '${mount_path}/.write-test'"
}

prep_dir "${PROBE_ROOT}/sessions"
prep_dir "${PROBE_ROOT}/data"
prep_dir "${PROBE_ROOT}/cache"

check_write apme-primary:latest /sessions "${PROBE_ROOT}/sessions"
check_write apme-gateway:latest /data "${PROBE_ROOT}/data"
check_write apme-galaxy-proxy:latest /cache "${PROBE_ROOT}/cache"

echo "Volume permission checks passed (UID ${RUNTIME_UID})."
