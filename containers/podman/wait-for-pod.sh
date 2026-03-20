#!/usr/bin/env bash
# Wait for apme-pod to be Running (not Degraded), then optionally run health-check.
# Run from repo root. Usage: wait-for-pod.sh [--health-check]
set -e
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
RUN_HEALTH_CHECK=
case "${1:-}" in
  --health-check) RUN_HEALTH_CHECK=1 ;;
  "") ;;
  *) echo "Usage: $0 [--health-check]"; exit 1 ;;
esac

echo "Waiting for apme-pod to be Running..."
MAX=60
for i in $(seq 1 "$MAX"); do
  STATUS=$(podman pod list --filter name=apme-pod --format "{{.Status}}" 2>/dev/null || true)
  if [[ "$STATUS" == "Running" ]]; then
    echo "Pod is Running."
    if [[ -n "$RUN_HEALTH_CHECK" ]]; then
      echo "Running health-check..."
      podman run --rm --pod apme-pod -e APME_PRIMARY_ADDRESS=127.0.0.1:50051 \
        --entrypoint apme-scan apme-cli:latest health-check
    fi
    exit 0
  fi
  if [[ $i -eq $MAX ]]; then
    echo "Timeout waiting for pod (status: ${STATUS:-none})."
    exit 1
  fi
  sleep 2
done
