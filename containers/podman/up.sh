#!/usr/bin/env bash
# Start the APME pod (Engine, Native, OPA, Ansible, Gitleaks, Galaxy Proxy,
# Collection Health, Dep Audit, Gateway, UI, Abbenay; optional OTel Collector).
# Run from repo root.
# CLI is not part of the pod; use run-cli.sh to run a scan with CWD mounted.
#
# Cache host path: default is XDG cache (${XDG_CACHE_HOME:-$HOME/.cache}/apme).
# Override: APME_CACHE_HOST_PATH=/my/cache ./up.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Return SELinux mode: disabled, Permissive, Enforcing, or unknown (fail closed).
# A bare /sys/fs/selinux directory without enforce is a stub (common in nested
# containers / Podman-in-Podman) — treat as disabled, not unknown.
_selinux_mode() {
  if [[ ! -e /sys/fs/selinux/enforce ]]; then
    echo "disabled"
    return 0
  fi
  local mode
  if mode=$(getenforce 2>/dev/null) && [[ -n "$mode" ]]; then
    echo "$mode"
    return 0
  fi
  # Fallback when getenforce is missing but selinuxfs is mounted.
  local enforce
  if ! enforce=$(cat /sys/fs/selinux/enforce 2>/dev/null); then
    echo "unknown"
    return 1
  fi
  case "$enforce" in
    1) echo "Enforcing" ;;
    0) echo "Permissive" ;;
    *)
      echo "unknown"
      return 1
      ;;
  esac
  return 0
}

# Relabel a host file so rootless Podman containers can read it under SELinux.
# Use -l s0 to clear MCS categories; a stale category set blocks pod containers
# even when the file mode is world-readable (EACCES on open).
_relabel_host_path_for_podman() {
  local host_path="$1"
  local mode
  mode=$(_selinux_mode) || {
    echo "ERROR: SELinux is active but state could not be determined; refusing to start without relabel verification" >&2
    return 1
  }
  if [[ "$mode" != "Enforcing" ]]; then
    return 0
  fi
  if ! command -v chcon >/dev/null 2>&1; then
    echo "ERROR: SELinux is Enforcing but chcon is not available; cannot relabel $host_path" >&2
    return 1
  fi
  # Directories must be labeled recursively so files created after mkdir
  # (e.g. seeded config.yaml) are readable inside the container.
  if [[ -d "$host_path" ]]; then
    if ! chcon -R -l s0 -t container_file_t "$host_path" 2>/dev/null; then
      echo "ERROR: could not recursively relabel $host_path for SELinux" >&2
      return 1
    fi
  elif ! chcon -l s0 -t container_file_t "$host_path" 2>/dev/null; then
    echo "ERROR: could not relabel $host_path for SELinux" >&2
    return 1
  fi
}

# Return 0 when the path already has container_file_t:s0 (no MCS categories).
_selinux_mountpoint_ok() {
  local path="$1"
  local ctx
  ctx=$(stat -c '%C' "$path" 2>/dev/null) || return 1
  [[ "$ctx" =~ :container_file_t:s0$ ]]
}

_SELINUX_REPAIR_MARKER=".apme-selinux-repair-v1"

_selinux_repair_marker_path() {
  echo "$1/${_SELINUX_REPAIR_MARKER}"
}

_selinux_marker_exists() {
  [[ -f "$(_selinux_repair_marker_path "$1")" ]]
}

# Return 0 when Podman is running rootless.
_podman_is_rootless() {
  podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null | grep -qx true
}

# Create a file after volume chown. Rootless mounts are often mode 1755 owned by
# the subordinate UID for container 1001, so the host user cannot touch(1) them.
_touch_in_volume_namespace() {
  local path="$1"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    touch "$path" 2>/dev/null || return 0
  elif _podman_is_rootless; then
    podman unshare touch "$path" 2>/dev/null || return 1
  elif ! touch "$path" 2>/dev/null; then
    return 1
  fi
}

_write_selinux_marker() {
  local mountpoint="$1"
  local marker
  marker=$(_selinux_repair_marker_path "$mountpoint")
  if ! _touch_in_volume_namespace "$marker"; then
    return 1
  fi
  chcon -l s0 -t container_file_t "$marker" 2>/dev/null || true
}

_CHOWN_REPAIR_MARKER=".apme-chown-repair-v1"

_chown_repair_marker_path() {
  echo "$1/${_CHOWN_REPAIR_MARKER}"
}

_chown_marker_exists() {
  [[ -f "$(_chown_repair_marker_path "$1")" ]]
}

_write_chown_marker() {
  local mountpoint="$1"
  local marker
  marker=$(_chown_repair_marker_path "$mountpoint")
  if ! _touch_in_volume_namespace "$marker"; then
    return 1
  fi
  chcon -l s0 -t container_file_t "$marker" 2>/dev/null || true
}

# Chown a host path so a container UID can write it.
# Rootless: podman unshare (host subordinate UIDs). Rootful: direct chown.
# Darwin/macOS: Podman Machine manages volume perms in the VM; no-op.
_chown_for_container_uid() {
  local uid_gid="$1"
  local path="$2"
  [[ "$(uname -s)" == "Darwin" ]] && return 0
  if _podman_is_rootless; then
    podman unshare chown -R "$uid_gid" "$path"
  else
    chown -R "$uid_gid" "$path"
  fi
}

# Return 0 when path is owned by the given container UID.
# Darwin/macOS: always return 0 (Podman Machine manages ownership in VM).
_owned_by_container_uid() {
  local uid="$1"
  local path="$2"
  [[ "$(uname -s)" == "Darwin" ]] && return 0
  local actual
  if _podman_is_rootless; then
    actual=$(podman unshare stat -c '%u' "$path" 2>/dev/null) || return 1
  else
    actual=$(stat -c '%u' "$path" 2>/dev/null) || return 1
  fi
  [[ "$actual" == "$uid" ]]
}

# Host UID that corresponds to a container UID (rootless subordinate map).
# Resolve from the live user-namespace uid_map: rootless Podman maps container
# UID 0 to the host user and UID 1..N onto /etc/subuid, so container 1001 is
# subuid_start+1000 — not subuid_start+1001. Arithmetic on /etc/subuid alone
# is off-by-one for non-zero container UIDs.
_host_uid_for_container_uid() {
  local container_uid="$1"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    echo "$container_uid"
    return 0
  fi
  if ! _podman_is_rootless; then
    echo "$container_uid"
    return 0
  fi
  local mapped
  mapped=$(podman unshare awk -v uid="$container_uid" '
    uid >= $1 && uid < $1 + $3 {
      print $2 + uid - $1
      found = 1
      exit
    }
    END { exit !found }
  ' /proc/self/uid_map) || return 1
  if [[ -z "$mapped" ]]; then
    return 1
  fi
  echo "$mapped"
}

# Return 0 when container UID can open path (rootless user-namespace probe).
# Darwin/macOS: always return 0 (virtiofs sharing handles access).
_container_uid_can_read() {
  local container_uid="$1"
  local path="$2"
  [[ "$(uname -s)" == "Darwin" ]] && return 0
  podman unshare python3 -c '
import os, sys
uid = int(sys.argv[1])
path = sys.argv[2]
os.setgid(uid)
os.setuid(uid)
with open(path, "rb"):
    pass
' "$container_uid" "$path"
}

# Grant execute-only (traversal) ACL to a mapped UID on each ancestor directory
# between $HOME and the given path that lacks world-execute or an existing ACL
# for that UID.  Prevents EACCES on mode-700 home directories (#528).
# Only grants 'x' — never read or write — to minimise exposure.
_grant_ancestor_traversal() {
  local mapped_uid="$1"
  local target_path="$2"
  local home_real
  home_real=$(cd "$HOME" && pwd -P)
  target_path=$(realpath -m "$target_path")
  # If the target is outside $HOME, the container accesses it via a Podman
  # volume mount — no host-filesystem traversal ACLs are needed.
  if [[ "$target_path" != "$home_real" && "$target_path" != "$home_real"/* ]]; then
    return 0
  fi
  local current
  current=$(dirname "$target_path")
  local -a ancestors=()
  # Walk up from the target's parent to (and including) $HOME.
  while [[ "$current" != "/" && "$current" != "$home_real" ]]; do
    ancestors=("$current" "${ancestors[@]}")
    current=$(dirname "$current")
  done
  # Include $HOME itself at the front.
  if [[ "$current" == "$home_real" ]]; then
    ancestors=("$home_real" "${ancestors[@]}")
  fi
  for dir in "${ancestors[@]}"; do
    # Skip if world-executable — traversal already possible.
    local perms
    perms=$(stat -c '%a' "$dir" 2>/dev/null) || continue
    if (( (8#$perms & 8#001) != 0 )); then
      continue
    fi
    # Skip if the mapped UID already has effective execute via ACL.
    # -e: effective perms (ACL mask); -n: numeric UID so name lookup cannot miss.
    if getfacl -enp "$dir" 2>/dev/null | grep -q "^user:${mapped_uid}:.*x"; then
      continue
    fi
    setfacl -m "u:${mapped_uid}:x" "$dir" || {
      echo "WARNING: could not grant traversal ACL on $dir for UID $mapped_uid" >&2
      return 1
    }
    # Record for later revocation by down.sh --wipe.
    echo "$dir" >> "${APME_TRAVERSAL_STATE_FILE:-/dev/null}"
  done
}

# Make Abbenay cache config usable by the host user and container UID 1001.
# Rootless: keep host ownership; grant a POSIX ACL to the subordinate UID so
# the next tox -e up can still chmod/seed and developers can edit config.yaml.
# Rootful: chown to 1001:1001. Migrates caches that were previously chowned
# exclusively to the subordinate UID (which locked the host user out).
_ensure_abbenay_config_access() {
  local path="$1"
  local cuid=1001

  if _podman_is_rootless; then
    if [[ "$(uname -s)" != "Darwin" ]] && [[ -d "$path" ]] && [[ ! -w "$path" ]]; then
      if _owned_by_container_uid "$cuid" "$path"; then
        echo "Restoring host ownership of Abbenay config cache (prior rootless chown)..."
        # Inside the user namespace, the host user is UID 0.
        podman unshare chown -R 0:0 "$path" || return 1
      else
        echo "ERROR: Abbenay config cache is not writable: $path" >&2
        return 1
      fi
    fi
    if [[ "$(uname -s)" == "Darwin" ]]; then
      # macOS: setfacl unavailable; virtiofs presents the host user as owner
      # and container UID 1001 as other. config.yaml uses 0644 so UID 1001
      # can read the seed. Do not chmod secrets.json: 0644 is other-readable
      # and still not other-writable, so file-store persist cannot work.
      # Named volume / keep-id: #562.
      chmod 755 "$path"
      [[ -f "$path/config.yaml" ]] && chmod 644 "$path/config.yaml"
      if [[ -f "$path/secrets.json" ]]; then
        echo "NOTE: Abbenay file secret store is unsupported on macOS virtiofs hostPath until #562. Leave secrets.json owner-only; use secret_store: env or memory. See https://github.com/ansible/apme/issues/562"
      fi
      return 0
    fi
    if ! command -v setfacl >/dev/null 2>&1; then
      echo "ERROR: setfacl is required for rootless Abbenay config (host edit + UID 1001)" >&2
      return 1
    fi
    local mapped
    mapped=$(_host_uid_for_container_uid "$cuid") || {
      echo "ERROR: could not resolve subordinate UID for container UID $cuid" >&2
      return 1
    }
    # Grant execute-only traversal on ancestor directories between $HOME and the
    # config path so the subordinate UID can reach the target.  Without this,
    # hosts with mode 700 on $HOME or $HOME/.cache block access at the first
    # path component (see #528).
    _grant_ancestor_traversal "$mapped" "$path" || return 1
    setfacl -m "u:${mapped}:rwx" "$path" || return 1
    setfacl -d -m "u:${mapped}:rwx" "$path" || return 1
    if [[ -f "$path/config.yaml" ]]; then
      setfacl -m "u:${mapped}:rw" "$path/config.yaml" || return 1
      if ! _container_uid_can_read "$cuid" "$path/config.yaml"; then
        echo "ERROR: container UID $cuid cannot read $path/config.yaml after ACL grant" >&2
        return 1
      fi
    fi
    if [[ -f "$path/secrets.json" ]]; then
      setfacl -m "u:${mapped}:rw" "$path/secrets.json" || return 1
      if ! _container_uid_can_read "$cuid" "$path/secrets.json"; then
        echo "ERROR: container UID $cuid cannot read $path/secrets.json after ACL grant" >&2
        return 1
      fi
    fi
    return 0
  fi

  if ! _chown_for_container_uid "${cuid}:${cuid}" "$path" \
    || ! _owned_by_container_uid "$cuid" "$path"; then
    return 1
  fi
}

# Recursive chown only when the mountpoint needs migration or lacks a marker.
# Large session/gateway volumes must not be walked on every tox -e up.
_ensure_volume_owned_by_container_uid() {
  local uid_gid="$1"
  local uid="${uid_gid%%:*}"
  local path="$2"
  if _owned_by_container_uid "$uid" "$path" && _chown_marker_exists "$path"; then
    return 0
  fi
  if ! _chown_for_container_uid "$uid_gid" "$path"; then
    return 1
  fi
  if ! _owned_by_container_uid "$uid" "$path"; then
    return 1
  fi
  if ! _write_chown_marker "$path"; then
    return 1
  fi
}

# Relabel named Podman volume mountpoints so pod containers can read/write under SELinux.
# Standalone `podman run -v vol:path:Z` probes can stamp MCS categories the pod
# cannot access, breaking gateway DB writes (project creation, scans, etc.).
# Also ensure UID 1001 owns the mountpoint — pvc.yaml annotations are not always
# applied to pre-existing volumes or rootful Podman (mode 1755 root-owned → EACCES).
# Only relabel/chown when needed; avoid recursive walks on every startup (large
# apme-sessions trees). Set APME_SELINUX_FULL_RELABEL=1 for a one-time recursive
# SELinux repair of an existing volume.
_relabel_podman_volumes() {
  local vol mountpoint uid_gid
  for vol in apme-sessions apme-postgres-data apme-proxy-cache; do
    if ! podman volume exists "$vol" 2>/dev/null; then
      continue
    fi
    mountpoint=$(podman volume inspect "$vol" --format '{{.Mountpoint}}')
    if [[ -z "$mountpoint" || ! -d "$mountpoint" ]]; then
      continue
    fi
    case "$vol" in
      apme-sessions) uid_gid="1001:0" ;;
      apme-postgres-data) uid_gid="999:999" ;;
      apme-proxy-cache) uid_gid="1001:0" ;;
      *) continue ;;
    esac
    if ! _ensure_volume_owned_by_container_uid "$uid_gid" "$mountpoint"; then
      echo "ERROR: could not chown volume $vol ($mountpoint) to $uid_gid" >&2
      return 1
    fi
    local mode
    mode=$(_selinux_mode) || {
      echo "ERROR: SELinux is active but state could not be determined; refusing to start without relabel verification" >&2
      return 1
    }
    if [[ "$mode" != "Enforcing" ]]; then
      continue
    fi
    if ! command -v chcon >/dev/null 2>&1; then
      echo "ERROR: SELinux is Enforcing but chcon is not available; cannot relabel volume $vol" >&2
      return 1
    fi
    if [[ "${APME_SELINUX_FULL_RELABEL:-}" == "1" ]]; then
      if ! chcon -R -l s0 -t container_file_t "$mountpoint" 2>/dev/null; then
        echo "ERROR: could not recursively relabel volume $vol ($mountpoint) for SELinux" >&2
        return 1
      fi
      if ! _write_selinux_marker "$mountpoint"; then
        echo "ERROR: could not write SELinux repair marker for volume $vol ($mountpoint)" >&2
        return 1
      fi
      continue
    fi
    if _selinux_mountpoint_ok "$mountpoint" && _selinux_marker_exists "$mountpoint"; then
      continue
    fi
    if ! _selinux_mountpoint_ok "$mountpoint"; then
      if ! chcon -l s0 -t container_file_t "$mountpoint" 2>/dev/null; then
        echo "ERROR: could not relabel volume $vol ($mountpoint) for SELinux" >&2
        return 1
      fi
    fi
    if ! chcon -R -l s0 -t container_file_t "$mountpoint" 2>/dev/null; then
      echo "ERROR: could not recursively relabel volume $vol ($mountpoint) for SELinux" >&2
      return 1
    fi
    if ! _write_selinux_marker "$mountpoint"; then
      echo "ERROR: could not write SELinux repair marker for volume $vol ($mountpoint)" >&2
      return 1
    fi
  done
}

# Default: XDG cache dir (persists across reboots); override with APME_CACHE_HOST_PATH
CACHE_PATH="${APME_CACHE_HOST_PATH:-${XDG_CACHE_HOME:-$HOME/.cache}/apme}"
APME_TRAVERSAL_STATE_FILE="$CACHE_PATH/.traversal-acls"

if [[ "$CACHE_PATH" != /* ]]; then
  echo "ERROR: APME_CACHE_HOST_PATH must be an absolute path (got: $CACHE_PATH)" >&2
  exit 1
fi

if [[ "$CACHE_PATH" == *$'\n'* ]]; then
  echo "ERROR: APME_CACHE_HOST_PATH must not contain newlines" >&2
  exit 1
fi

mkdir -p "$CACHE_PATH"
# Reset traversal state for this run; down.sh --wipe reads this to undo ACLs.
: > "$APME_TRAVERSAL_STATE_FILE"

# Load Abbenay secrets (.env) if present.
ABBENAY_ENV="$ROOT/containers/abbenay/.env"
if [[ -f "$ABBENAY_ENV" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ABBENAY_ENV"
  set +a
fi
OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}"
VERTEX_ANTHROPIC_API_KEY="${VERTEX_ANTHROPIC_API_KEY:-}"
ABBENAY_GCP_CREDENTIALS="${ABBENAY_GCP_CREDENTIALS:-}"
GOOGLE_VERTEX_PROJECT="${GOOGLE_VERTEX_PROJECT:-}"
GOOGLE_VERTEX_LOCATION="${GOOGLE_VERTEX_LOCATION:-us-east5}"
APME_AI_MODEL="${APME_AI_MODEL:-}"
APME_FEEDBACK_ENABLED="${APME_FEEDBACK_ENABLED:-true}"
APME_FEEDBACK_GITHUB_REPO="${APME_FEEDBACK_GITHUB_REPO:-}"
APME_FEEDBACK_GITHUB_TOKEN="${APME_FEEDBACK_GITHUB_TOKEN:-}"

# Optional: CA bundle for outbound HTTPS clients that need an internal or
# self-signed trust anchor. Set ABBENAY_CA_BUNDLE to the absolute path of a
# PEM CA bundle file.
ABBENAY_CA_BUNDLE="${ABBENAY_CA_BUNDLE:-}"
if [[ -n "$ABBENAY_CA_BUNDLE" ]]; then
  if [[ "$ABBENAY_CA_BUNDLE" != /* ]]; then
    echo "ERROR: ABBENAY_CA_BUNDLE must be an absolute path (got: $ABBENAY_CA_BUNDLE)" >&2
    exit 1
  fi
  if [[ "$ABBENAY_CA_BUNDLE" == *$'\n'* ]]; then
    echo "ERROR: ABBENAY_CA_BUNDLE must not contain newlines" >&2
    exit 1
  fi
  if [[ ! -f "$ABBENAY_CA_BUNDLE" ]]; then
    echo "ERROR: ABBENAY_CA_BUNDLE points to a file that does not exist: $ABBENAY_CA_BUNDLE" >&2
    exit 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: ABBENAY_CA_BUNDLE requires python3 to patch the pod YAML, but python3 was not found in PATH" >&2
    exit 1
  fi
fi

# Tear down any existing pod so we get a clean start.
if podman pod exists apme-pod 2>/dev/null; then
  echo "Stopping existing apme-pod..."
  podman pod stop apme-pod 2>/dev/null || true
  podman pod rm apme-pod 2>/dev/null || true
fi

# Pod YAML cannot use env vars; we inject values via envsubst.
export OPENROUTER_API_KEY VERTEX_ANTHROPIC_API_KEY APME_AI_MODEL APME_ROOT="$ROOT"
export APME_FEEDBACK_ENABLED APME_FEEDBACK_GITHUB_REPO APME_FEEDBACK_GITHUB_TOKEN

POD_YAML=$(envsubst '$OPENROUTER_API_KEY $VERTEX_ANTHROPIC_API_KEY $APME_AI_MODEL $APME_ROOT $APME_FEEDBACK_ENABLED $APME_FEEDBACK_GITHUB_REPO $APME_FEEDBACK_GITHUB_TOKEN' \
  < containers/podman/pod.yaml)

# When a CA bundle is provided, inject the standard CA env vars and mounts for
# the containers that make outbound HTTPS requests (gateway, abbenay, galaxy-proxy).
if [[ -n "$ABBENAY_CA_BUNDLE" ]]; then
  CA_MOUNT_PATH="/etc/ssl/certs/custom-ca-bundle.pem"
  POD_YAML=$(python3 -c "
import json, sys, os
yaml = sys.stdin.read()
ca_path = os.environ['ABBENAY_CA_BUNDLE']
mount = '$CA_MOUNT_PATH'
ca_path_yaml = json.dumps(ca_path)
mount_yaml = json.dumps(mount)
abbenay_env_marker = '        - name: XDG_RUNTIME_DIR'
# Writable config dir has no readOnly; shared runtime dir precedes galaxy-proxy.
abbenay_vol_marker = (
    '          mountPath: /home/abbenay/.config/abbenay\n'
    '        - name: abbenay-run\n'
    '          mountPath: /tmp/abbenay-run\n'
    '    - name: galaxy-proxy'
)
gateway_env_marker = '        - name: APME_FEEDBACK_GITHUB_TOKEN'
gateway_vol_marker = '      volumeMounts:\n        - name: abbenay-run'
galaxy_marker = '    - name: galaxy-proxy\n      image: apme-galaxy-proxy:latest'
galaxy_vol_marker = '      volumeMounts:\n        - name: proxy-cache'
if (
    abbenay_env_marker not in yaml
    or abbenay_vol_marker not in yaml
    or gateway_env_marker not in yaml
    or gateway_vol_marker not in yaml
    or galaxy_marker not in yaml
    or galaxy_vol_marker not in yaml
):
    print('ERROR: pod.yaml markers not found; CA bundle injection failed', file=sys.stderr)
    sys.exit(1)
yaml = yaml.replace(
    abbenay_env_marker,
    '        - name: NODE_EXTRA_CA_CERTS\n'
    '          value: ' + mount_yaml + '\n'
    '        ' + abbenay_env_marker.lstrip())
yaml = yaml.replace(
    abbenay_vol_marker,
    '          mountPath: /home/abbenay/.config/abbenay\n'
    '        - name: abbenay-run\n'
    '          mountPath: /tmp/abbenay-run\n'
    '        - name: abbenay-ca-bundle\n'
    '          mountPath: ' + mount_yaml + '\n'
    '          readOnly: true\n'
    '    - name: galaxy-proxy')
yaml = yaml.replace(
    gateway_env_marker,
    (
        '        - name: SSL_CERT_FILE\n'
        '          value: ' + mount_yaml + '\n'
        '        - name: REQUESTS_CA_BUNDLE\n'
        '          value: ' + mount_yaml + '\n'
        '        - name: CURL_CA_BUNDLE\n'
        '          value: ' + mount_yaml + '\n'
        '        - name: GIT_SSL_CAINFO\n'
        '          value: ' + mount_yaml + '\n'
        + gateway_env_marker
    ))
yaml = yaml.replace(
    gateway_vol_marker,
    '      volumeMounts:\n'
    '        - name: gateway-ca-bundle\n'
    '          mountPath: ' + mount_yaml + '\n'
    '          readOnly: true\n'
    '        - name: abbenay-run')
# Galaxy Proxy: add env section + CA env vars
yaml = yaml.replace(
    galaxy_marker,
    (
        galaxy_marker + '\n'
        '      env:\n'
        '        - name: SSL_CERT_FILE\n'
        '          value: ' + mount_yaml + '\n'
        '        - name: REQUESTS_CA_BUNDLE\n'
        '          value: ' + mount_yaml + '\n'
        '        - name: CURL_CA_BUNDLE\n'
        '          value: ' + mount_yaml + '\n'
        '        - name: GIT_SSL_CAINFO\n'
        '          value: ' + mount_yaml
    ),
)
# Galaxy Proxy: add CA volume mount
yaml = yaml.replace(
    galaxy_vol_marker,
    '      volumeMounts:\n'
    '        - name: galaxy-ca-bundle\n'
    '          mountPath: ' + mount_yaml + '\n'
    '          readOnly: true\n'
    '        - name: proxy-cache')
yaml = yaml.rstrip() + '\n' \
    '    - name: abbenay-ca-bundle\n' \
    '      hostPath:\n' \
    '        path: ' + ca_path_yaml + '\n' \
    '        type: File\n' \
    '    - name: gateway-ca-bundle\n' \
    '      hostPath:\n' \
    '        path: ' + ca_path_yaml + '\n' \
    '        type: File\n' \
    '    - name: galaxy-ca-bundle\n' \
    '      hostPath:\n' \
    '        path: ' + ca_path_yaml + '\n' \
    '        type: File\n'
print(yaml)
" <<< "$POD_YAML")
  echo "CA bundle enabled for gateway/abbenay/galaxy-proxy: $ABBENAY_CA_BUNDLE -> $CA_MOUNT_PATH (inside container)"
fi

# Optional: GCP service account / ADC JSON for direct Vertex AI (vertex-anthropic engine).
# Set ABBENAY_GCP_CREDENTIALS to the absolute host path of the credentials file and
# GOOGLE_VERTEX_PROJECT (plus optional GOOGLE_VERTEX_LOCATION) in containers/abbenay/.env.
# See docs/guides/ABBENAY_AI.md — mirrors the Helm chart mount at
# /var/run/secrets/gcp/service-account-key.json.
if [[ -n "$ABBENAY_GCP_CREDENTIALS" ]]; then
  if [[ "$ABBENAY_GCP_CREDENTIALS" != /* ]]; then
    echo "ERROR: ABBENAY_GCP_CREDENTIALS must be an absolute path (got: $ABBENAY_GCP_CREDENTIALS)" >&2
    exit 1
  fi
  if [[ "$ABBENAY_GCP_CREDENTIALS" == *$'\n'* ]]; then
    echo "ERROR: ABBENAY_GCP_CREDENTIALS must not contain newlines" >&2
    exit 1
  fi
  if [[ ! -f "$ABBENAY_GCP_CREDENTIALS" ]]; then
    echo "ERROR: ABBENAY_GCP_CREDENTIALS points to a file that does not exist: $ABBENAY_GCP_CREDENTIALS" >&2
    exit 1
  fi
  if [[ -z "$GOOGLE_VERTEX_PROJECT" ]]; then
    echo "ERROR: GOOGLE_VERTEX_PROJECT is required when ABBENAY_GCP_CREDENTIALS is set" >&2
    exit 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: ABBENAY_GCP_CREDENTIALS requires python3 to patch the pod YAML, but python3 was not found in PATH" >&2
    exit 1
  fi
  GCP_CREDENTIALS_MOUNT="/var/run/secrets/gcp/service-account-key.json"
  GCP_CREDS_CACHE="$CACHE_PATH/abbenay/gcp-credentials.json"
  mkdir -p "$(dirname "$GCP_CREDS_CACHE")"
  # Stage credentials into the cache dir with mode 0644 so the non-root abbenay
  # user in the pod can read the mount (rootless Podman cannot read host 0600 files).
  install -m 0644 "$ABBENAY_GCP_CREDENTIALS" "$GCP_CREDS_CACHE"
  _relabel_host_path_for_podman "$GCP_CREDS_CACHE"
  export ABBENAY_GCP_CREDENTIALS="$GCP_CREDS_CACHE"
  export GOOGLE_VERTEX_PROJECT GOOGLE_VERTEX_LOCATION
  POD_YAML=$(python3 -c "
import json, sys, os
yaml = sys.stdin.read()
cred_path = os.environ['ABBENAY_GCP_CREDENTIALS']
gcp_project = os.environ['GOOGLE_VERTEX_PROJECT']
gcp_location = os.environ['GOOGLE_VERTEX_LOCATION']
mount = '$GCP_CREDENTIALS_MOUNT'
cred_path_yaml = json.dumps(cred_path)
mount_yaml = json.dumps(mount)
project_yaml = json.dumps(gcp_project)
location_yaml = json.dumps(gcp_location)
abbenay_env_marker = '        - name: XDG_RUNTIME_DIR'
abbenay_config_mount_marker = (
    '          mountPath: /home/abbenay/.config/abbenay'
)
if abbenay_env_marker not in yaml or abbenay_config_mount_marker not in yaml:
    print('ERROR: pod.yaml markers not found; GCP credentials injection failed', file=sys.stderr)
    sys.exit(1)
yaml = yaml.replace(
    abbenay_env_marker,
    '        - name: GOOGLE_APPLICATION_CREDENTIALS\n'
    '          value: ' + mount_yaml + '\n'
    '        - name: GOOGLE_VERTEX_PROJECT\n'
    '          value: ' + project_yaml + '\n'
    '        - name: GOOGLE_VERTEX_LOCATION\n'
    '          value: ' + location_yaml + '\n'
    '        ' + abbenay_env_marker.lstrip())
yaml = yaml.replace(
    abbenay_config_mount_marker,
    abbenay_config_mount_marker + '\n'
    '        - name: abbenay-gcp-credentials\n'
    '          mountPath: ' + mount_yaml + '\n'
    '          readOnly: true')
yaml = yaml.rstrip() + '\n' \
    '    - name: abbenay-gcp-credentials\n' \
    '      hostPath:\n' \
    '        path: ' + cred_path_yaml + '\n' \
    '        type: File\n'
print(yaml)
" <<< "$POD_YAML")
  echo "Vertex AI credentials enabled for abbenay: $ABBENAY_GCP_CREDENTIALS -> $GCP_CREDENTIALS_MOUNT (inside container)"
  echo "Vertex AI project: $GOOGLE_VERTEX_PROJECT (location: $GOOGLE_VERTEX_LOCATION)"
fi

# Set up writable Abbenay config directory (seed from repo / legacy files).
# The hostPath Directory mount replaces the old read-only File mount so that
# runtime admin writes (POST /api/v1/ai/provider/.../configure) survive restarts.
# Never chown the git checkout: rootless Podman maps UID 1001 to a subordinate
# host UID and would lock the developer out of containers/abbenay/config.
# Runtime config always lives under CACHE_PATH (mode 0700/0600; credentials).
# Rootless: host keeps ownership; container UID 1001 gets a POSIX ACL.
# Rootful: chown the cache copy to 1001:1001.
ABBENAY_CONFIG_SEED="$ROOT/containers/abbenay/config"
ABBENAY_CONFIG_REPO_PATH="$ABBENAY_CONFIG_SEED"
ABBENAY_CONFIG_DIR="$CACHE_PATH/abbenay/config"
if ! command -v podman >/dev/null 2>&1; then
  echo "ERROR: podman is required to set Abbenay config ownership (UID 1001)" >&2
  exit 1
fi
# Migrate prior exclusive subordinate-UID ownership before host chmod/seed.
if [[ -d "$ABBENAY_CONFIG_DIR" ]] && [[ ! -w "$ABBENAY_CONFIG_DIR" ]]; then
  if ! _ensure_abbenay_config_access "$ABBENAY_CONFIG_DIR"; then
    echo "ERROR: could not restore host access to Abbenay config cache $ABBENAY_CONFIG_DIR" >&2
    exit 1
  fi
fi
mkdir -p "$ABBENAY_CONFIG_DIR"
chmod 0700 "$ABBENAY_CONFIG_DIR"
if [[ ! -f "$ABBENAY_CONFIG_DIR/config.yaml" ]]; then
  if [[ -f "$ABBENAY_CONFIG_SEED/config.yaml" ]]; then
    cp "$ABBENAY_CONFIG_SEED/config.yaml" "$ABBENAY_CONFIG_DIR/config.yaml"
    echo "Seeded Abbenay config from containers/abbenay/config/config.yaml"
  elif [[ -f "$ROOT/containers/abbenay/config.yaml" ]]; then
    cp "$ROOT/containers/abbenay/config.yaml" "$ABBENAY_CONFIG_DIR/config.yaml"
    echo "Seeded Abbenay config from containers/abbenay/config.yaml (legacy location)"
  elif [[ -f "$ROOT/containers/abbenay/config.yaml.example" ]]; then
    cp "$ROOT/containers/abbenay/config.yaml.example" "$ABBENAY_CONFIG_DIR/config.yaml"
    echo "Seeded Abbenay config from containers/abbenay/config.yaml.example"
  fi
fi
if [[ -f "$ABBENAY_CONFIG_DIR/config.yaml" ]]; then
  chmod 0600 "$ABBENAY_CONFIG_DIR/config.yaml"
fi
_relabel_host_path_for_podman "$ABBENAY_CONFIG_DIR"
if ! _ensure_abbenay_config_access "$ABBENAY_CONFIG_DIR"; then
  echo "ERROR: could not grant Abbenay container UID 1001 access to $ABBENAY_CONFIG_DIR" >&2
  exit 1
fi
# POD_YAML still has the repo hostPath from envsubst; always mount the cache copy.
if ! POD_YAML=$(ABBENAY_CONFIG_REPO_PATH="$ABBENAY_CONFIG_REPO_PATH" \
  ABBENAY_CONFIG_DIR="$ABBENAY_CONFIG_DIR" python3 -c '
import os, sys
yaml = sys.stdin.read()
old = "path: " + os.environ["ABBENAY_CONFIG_REPO_PATH"]
new = "path: " + os.environ["ABBENAY_CONFIG_DIR"]
if old not in yaml:
    print("ERROR: abbenay-config hostPath not found in pod YAML", file=sys.stderr)
    sys.exit(1)
print(yaml.replace(old, new, 1), end="")
' <<< "$POD_YAML"); then
  echo "ERROR: could not retarget Abbenay config hostPath in pod YAML" >&2
  exit 1
fi
echo "Abbenay config hostPath: $ABBENAY_CONFIG_DIR (seed sources remain under containers/abbenay/)"
_relabel_host_path_for_podman "$ROOT/containers/otel-collector/config.yaml"
if [[ -n "$ABBENAY_CA_BUNDLE" ]]; then
  _relabel_host_path_for_podman "$ABBENAY_CA_BUNDLE"
fi

podman kube play containers/podman/pvc.yaml
_relabel_podman_volumes
echo "$POD_YAML" | podman play kube -

echo "Pod apme-pod started (volumes: apme-sessions, apme-postgres-data, apme-proxy-cache). Run a scan: containers/podman/run-cli.sh"
echo "Abbenay UI: http://127.0.0.1:8787 (localhost only; HTTP auth disabled for dev)"
echo "OTel Prometheus metrics: http://localhost:8889/metrics (companion stack: containers/observability/up.sh)"

if [[ -n "$APME_FEEDBACK_GITHUB_REPO" && -n "$APME_FEEDBACK_GITHUB_TOKEN" ]]; then
  echo "Issue reporting enabled (repo: $APME_FEEDBACK_GITHUB_REPO)"
else
  echo "Issue reporting disabled. To enable, export APME_FEEDBACK_GITHUB_REPO and APME_FEEDBACK_GITHUB_TOKEN."
fi
