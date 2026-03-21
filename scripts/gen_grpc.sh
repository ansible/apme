#!/usr/bin/env bash
# Generate Python gRPC stubs from proto/apme/v1/*.proto.
# Run from repo root. Requires: pip install grpcio-tools

set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PROTO_ROOT="$ROOT/proto"
PY_OUT="$ROOT/src"
PROTOS=(
  apme/v1/common.proto
  apme/v1/primary.proto
  apme/v1/ansible.proto
  apme/v1/validate.proto
)
if [ -d "$ROOT/.venv" ]; then
  PY="${ROOT}/.venv/bin/python"
else
  PY=python
fi
"$PY" -m grpc_tools.protoc \
  -I "$PROTO_ROOT" \
  --python_out="$PY_OUT" \
  --grpc_python_out="$PY_OUT" \
  "${PROTOS[@]}"
# Ensure packages are importable
mkdir -p "$PY_OUT/apme" "$PY_OUT/apme/v1"
touch "$PY_OUT/apme/__init__.py" "$PY_OUT/apme/v1/__init__.py"
echo "Generated Python stubs in $PY_OUT/apme/v1/"
