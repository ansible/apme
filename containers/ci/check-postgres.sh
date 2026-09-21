#!/usr/bin/env bash
# Verify reachability of the PostgreSQL service used by CI.
set -euo pipefail

for attempt in {1..30}; do
  if (echo > /dev/tcp/127.0.0.1/5432) >/dev/null 2>&1; then
    echo "PostgreSQL is reachable on 127.0.0.1:5432"
    exit 0
  fi
  echo "Waiting for PostgreSQL (attempt ${attempt}/30)..."
  sleep 2
done

echo "PostgreSQL was not reachable on 127.0.0.1:5432" >&2
exit 1