#!/usr/bin/env bash
# Start Prometheus + Grafana companion stack for APME OTel metrics.
# Prometheus TSDB persists on the host (survives down/up).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

_relabel() {
  local host_path="$1"
  if command -v chcon >/dev/null 2>&1 && [[ "$(getenforce 2>/dev/null)" == "Enforcing" ]]; then
    # Recursive: YAML/dashboard files under the path must be labeled too.
    chcon -Rt container_file_t "$host_path" 2>/dev/null || \
      echo "WARNING: could not relabel $host_path for SELinux" >&2
  fi
}

if podman pod exists apme-observability 2>/dev/null; then
  echo "Stopping existing apme-observability..."
  podman pod stop apme-observability 2>/dev/null || true
  podman pod rm -f apme-observability 2>/dev/null || true
fi

# Persist TSDB across restarts (override with APME_PROM_DATA).
PROM_DATA="${APME_PROM_DATA:-${XDG_CACHE_HOME:-$HOME/.cache}/apme/prometheus-tsdb}"
mkdir -p "$PROM_DATA"
# Official prometheus image runs as UID 65534 (nobody). Prefer podman unshare;
# never fall back to world-writable permissions.
if ! podman unshare chown -R 65534:65534 "$PROM_DATA" 2>/dev/null; then
  echo "ERROR: cannot chown $PROM_DATA to UID 65534 for Prometheus." >&2
  echo "  Fix ownership (e.g. podman unshare chown -R 65534:65534 \"$PROM_DATA\")" >&2
  echo "  or point APME_PROM_DATA at a writable directory you control." >&2
  exit 1
fi

_relabel "$ROOT/containers/observability/prometheus.yml"
_relabel "$ROOT/containers/observability/grafana/provisioning/datasources"
_relabel "$ROOT/containers/observability/grafana/provisioning/dashboards"
_relabel "$ROOT/containers/observability/grafana/dashboards"
_relabel "$PROM_DATA"

# Grafana admin password: require explicit env, or generate+persist a
# high-entropy secret (never a known default like admin/admin).
PASS_FILE="${XDG_CACHE_HOME:-$HOME/.cache}/apme/grafana-admin.password"
if [[ -z "${APME_GRAFANA_ADMIN_PASSWORD:-}" ]]; then
  if [[ -f "$PASS_FILE" ]]; then
    APME_GRAFANA_ADMIN_PASSWORD="$(tr -d '\n' <"$PASS_FILE")"
  else
    mkdir -p "$(dirname "$PASS_FILE")"
    APME_GRAFANA_ADMIN_PASSWORD="$(openssl rand -base64 32 | tr -d '/+=\n' | head -c 32)"
    (umask 077; printf '%s\n' "$APME_GRAFANA_ADMIN_PASSWORD" >"$PASS_FILE")
    echo "Generated Grafana admin password → $PASS_FILE"
  fi
fi
export APME_GRAFANA_ADMIN_PASSWORD
export APME_ROOT="$ROOT"
export APME_PROM_DATA="$PROM_DATA"
POD_YAML=$(envsubst '$APME_ROOT $APME_PROM_DATA $APME_GRAFANA_ADMIN_PASSWORD' < containers/observability/pod.yaml)
echo "$POD_YAML" | podman play kube -

echo "Observability stack started (ports bound to 127.0.0.1)."
echo "  Prometheus: http://127.0.0.1:9091"
echo "  Grafana:    http://127.0.0.1:3002  (user: admin)"
echo "  Password:   \$APME_GRAFANA_ADMIN_PASSWORD (or $PASS_FILE)"
echo "  Dashboard:  APME Scan Times"
echo "  Scrape target: host.containers.internal:8889 (otel-collector hostPort)"
echo "  TSDB data:  $PROM_DATA  (15d retention; survives down/up)"
echo "  Wipe TSDB:  ./containers/observability/down.sh --wipe"
