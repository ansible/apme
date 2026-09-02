#!/usr/bin/env bash
# Stop the APME pod and optionally wipe local state (Gateway DB, sessions,
# Abbenay secrets.json).
#
# Usage:
#   ./down.sh          # stop pod only
#   ./down.sh --wipe   # stop pod, delete gateway DB/sessions, and Abbenay secrets.json
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Host UID for a container UID (rootless uid_map: ns_uid host_uid count).
_host_uid_for_container_uid() {
  local cuid="$1"
  local mapped
  mapped=$(podman unshare awk -v cuid="$cuid" '
    BEGIN { found = 0 }
    {
      inside_ns = $1; outside_ns = $2; count = $3
      if (cuid >= inside_ns && cuid < inside_ns + count) {
        print outside_ns + (cuid - inside_ns)
        found = 1
        exit
      }
    }
    END { exit !found }
  ' /proc/self/uid_map) || return 1
  echo "$mapped"
}

# Revoke traversal ACLs recorded by up.sh. Keep the state file (and fail) if
# any entry still has the mapped-UID ACL after this pass.
_revoke_traversal_acls() {
  local state_file="$1"
  local mapped="$2"
  local dir acl failed=0 tmp
  tmp="${state_file}.tmp"
  : > "$tmp"
  while IFS= read -r dir; do
    [[ -n "$dir" ]] || continue
    if ! acl=$(getfacl -np "$dir" 2>/dev/null); then
      echo "ERROR: could not read ACL on $dir" >&2
      printf '%s\n' "$dir" >> "$tmp"
      failed=1
      continue
    fi
    if ! grep -q "^user:${mapped}:" <<<"$acl"; then
      continue
    fi
    if setfacl -x "u:${mapped}" "$dir" 2>/dev/null; then
      echo "Revoked traversal ACL for UID $mapped on $dir"
    else
      echo "ERROR: could not revoke traversal ACL for UID $mapped on $dir" >&2
      printf '%s\n' "$dir" >> "$tmp"
      failed=1
    fi
  done < "$state_file"
  if (( failed )); then
    mv "$tmp" "$state_file"
    return 1
  fi
  rm -f "$tmp" "$state_file"
}

echo "Stopping apme-pod..."
podman pod stop apme-pod 2>/dev/null || true
podman pod rm  apme-pod 2>/dev/null || true
echo "Pod stopped."

if [[ "${1:-}" == "--wipe" ]]; then
  for vol in apme-sessions apme-postgres-data apme-gateway-data apme-proxy-cache; do
    if podman volume exists "$vol" 2>/dev/null; then
      podman volume rm "$vol"
      echo "Removed volume: $vol"
    else
      echo "No volume found: $vol"
    fi
  done

  # Legacy hostPath cache (pre-PVC local deployments).
  CACHE_PATH="${APME_CACHE_HOST_PATH:-${XDG_CACHE_HOME:-$HOME/.cache}/apme}"

  if [[ "$CACHE_PATH" != /* ]]; then
    echo "ERROR: cache path must be absolute (got: $CACHE_PATH)" >&2
    exit 1
  fi
  if [[ "$CACHE_PATH" == *$'\n'* ]]; then
    echo "ERROR: cache path must not contain newlines" >&2
    exit 1
  fi

  DB_FILE="$CACHE_PATH/gateway/apme.db"
  if [[ -f "$DB_FILE" ]]; then
    rm -f "$DB_FILE" "$DB_FILE-shm" "$DB_FILE-wal"
    echo "Wiped database: $DB_FILE"
  else
    echo "No database found at $DB_FILE"
  fi

  SESSIONS_DIR="$CACHE_PATH/sessions"
  if [[ -d "$SESSIONS_DIR" ]]; then
    rm -rf "$SESSIONS_DIR"
    echo "Wiped session cache: $SESSIONS_DIR"
  else
    echo "No session cache found at $SESSIONS_DIR"
  fi

  ABBENAY_SECRETS="$CACHE_PATH/abbenay/config/secrets.json"
  if [[ -f "$ABBENAY_SECRETS" ]]; then
    rm -f "$ABBENAY_SECRETS"
    echo "Wiped Abbenay file-store secrets: $ABBENAY_SECRETS"
  else
    echo "No Abbenay secrets.json found at $ABBENAY_SECRETS"
  fi

  # Revoke traversal ACLs granted on $HOME ancestors for rootless Abbenay
  # config access (see up.sh _grant_ancestor_traversal / #528).
  if command -v podman >/dev/null 2>&1 \
    && command -v setfacl >/dev/null 2>&1 \
    && command -v getfacl >/dev/null 2>&1; then
    mapped=$(_host_uid_for_container_uid 1001 2>/dev/null) || mapped=""
    state_file="$CACHE_PATH/.traversal-acls"
    if [[ -n "$mapped" && -f "$state_file" ]]; then
      if ! _revoke_traversal_acls "$state_file" "$mapped"; then
        echo "ERROR: traversal ACL cleanup incomplete; kept $state_file" >&2
        exit 1
      fi
    fi
  fi
fi
