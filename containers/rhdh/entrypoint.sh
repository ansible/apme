#!/usr/bin/env bash
set -euo pipefail
cd /opt/app-root/src
exec node --no-node-snapshot packages/backend \
  --config containers/rhdh/app-config.yaml \
  --config dynamic-plugins-root/app-config.dynamic-plugins.yaml
