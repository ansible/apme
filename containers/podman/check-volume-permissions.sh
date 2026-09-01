#!/usr/bin/env bash
# Verify UBI service images can write to mounted volume paths as UID 1001 (ADR-061).
# Also verify UID 1001 can read application source (host COPY may preserve 0600).
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

# Ensure non-root can read packaged source (editable install path).
check_app_readable() {
  local image=$1
  local probe=$2

  if ! podman image exists "$image" 2>/dev/null; then
    echo "==> Skip app-read check for $image (image not built)"
    return 0
  fi

  echo "==> App read check: $image (UID $RUNTIME_UID) -> $probe"
  podman run --rm \
    --user "${RUNTIME_UID}" \
    --entrypoint sh \
    "$image" \
    -c "test -r '${probe}'"
}

prep_dir "${PROBE_ROOT}/sessions"
prep_dir "${PROBE_ROOT}/data"
prep_dir "${PROBE_ROOT}/cache"

check_write apme-engine:latest /sessions "${PROBE_ROOT}/sessions"
check_write apme-gateway:latest /data "${PROBE_ROOT}/data"
check_write apme-galaxy-proxy:latest /cache "${PROBE_ROOT}/cache"

check_app_readable apme-engine:latest /app/src/apme_engine/__init__.py
check_app_readable apme-gateway:latest /app/src/apme_gateway/__init__.py
check_app_readable apme-galaxy-proxy:latest /app/src/galaxy_proxy/__init__.py
check_app_readable apme-opa:latest /entrypoint.sh
check_app_readable apme-ui:latest /entrypoint.sh
check_app_readable apme-ui:latest /etc/nginx/nginx.conf
check_app_readable apme-ui:latest /etc/nginx/conf.d/default.conf.template

echo "Volume permission checks passed (UID ${RUNTIME_UID})."
