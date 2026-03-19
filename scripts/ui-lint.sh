#!/usr/bin/env bash
# Pre-commit hook: lint and test frontend code.
# Skips gracefully if pnpm or node_modules aren't available.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
UI_DIR="$REPO_ROOT/ui"

# Find pnpm: PATH first, then repo-local .local/bin
if command -v pnpm &>/dev/null; then
    PNPM="pnpm"
elif [ -x "$REPO_ROOT/.local/bin/pnpm" ]; then
    PNPM="$REPO_ROOT/.local/bin/pnpm"
else
    echo "⏭  Skipping UI lint — pnpm not found (install via PATH or .local/bin)"
    exit 0
fi

if [ ! -d "$UI_DIR/node_modules" ]; then
    echo "⏭  Skipping UI lint — run 'cd ui && pnpm install' first"
    exit 0
fi

cd "$UI_DIR"
echo "Running ESLint on UI..."
"$PNPM" lint
echo "Running Vitest..."
"$PNPM" test
