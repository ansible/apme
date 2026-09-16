#!/usr/bin/env bash
# Run the host-side integration suite with a disposable PostgreSQL service.
set -euo pipefail

CONTAINER_NAME="${APME_INTEGRATION_POSTGRES_CONTAINER:-apme-integration-postgres}"
POSTGRES_IMAGE="${APME_POSTGRES_IMAGE:-docker.io/library/postgres:16}"
POSTGRES_PORT="${APME_POSTGRES_PORT:-5432}"
POSTGRES_USER="${APME_POSTGRES_USER:-apme}"
POSTGRES_PASSWORD="${APME_POSTGRES_PASSWORD:-apme}"
POSTGRES_DB="${APME_POSTGRES_DB:-apme_test}"

if ! command -v podman >/dev/null 2>&1; then
  echo "ERROR: podman is required for integration-local" >&2
  exit 1
fi

cleanup() {
  podman rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

cleanup
podman run --detach \
  --name "$CONTAINER_NAME" \
  --publish "${POSTGRES_PORT}:5432" \
  --env "POSTGRES_USER=${POSTGRES_USER}" \
  --env "POSTGRES_PASSWORD=${POSTGRES_PASSWORD}" \
  --env "POSTGRES_DB=${POSTGRES_DB}" \
  "$POSTGRES_IMAGE" >/dev/null

for attempt in {1..30}; do
  if podman exec "$CONTAINER_NAME" pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB" >/dev/null 2>&1; then
    break
  fi
  if [[ "$attempt" == 30 ]]; then
    echo "ERROR: PostgreSQL did not become ready" >&2
    podman logs "$CONTAINER_NAME" >&2 || true
    exit 1
  fi
  sleep 2
done

export APME_TEST_DATABASE_URL="${APME_TEST_DATABASE_URL:-postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@127.0.0.1:${POSTGRES_PORT}/${POSTGRES_DB}}"
tox -e integration -- "$@"