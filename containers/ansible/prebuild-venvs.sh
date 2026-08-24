#!/usr/bin/env bash
# Pre-warm UV cache for ansible-core versions at image build time.
# Session-scoped venvs are created by the Engine,
# but UV caches the wheels so venv creation is near-instant.
set -e

VERSIONS="2.18 2.19 2.20"
UV_CACHE="${UV_CACHE_DIR:-/opt/uv-cache}"

if ! command -v uv &>/dev/null; then
    echo "uv not found, installing..."
    pip install uv
fi

export UV_CACHE_DIR="$UV_CACHE"

for ver in $VERSIONS; do
    echo "==> Pre-warming UV cache for ansible-core ~=${ver}.0"
    TMP_VENV=$(mktemp -d)
    uv venv "$TMP_VENV"
    uv pip install --python "$TMP_VENV/bin/python" "ansible-core~=${ver}.0"
    echo "    Cached: ansible-core~=${ver}.0"
    rm -rf "$TMP_VENV"
done

echo "UV cache populated at ${UV_CACHE_DIR}"
echo "Cache size: $(du -sh "${UV_CACHE_DIR}" 2>/dev/null | cut -f1)"
