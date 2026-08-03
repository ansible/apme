#!/usr/bin/env bash
# Build the Python wheel and emit a CycloneDX 1.5 SBOM (issue #203).
#
# Usage:
#   generate-python-sbom.sh --output-dir dist/sbom
#
# Locally runnable (lean CI): complements container image SBOMs from supply-chain.sh.
set -euo pipefail

OUTPUT_DIR=""

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  local code="${1:-1}"
  sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'
  exit "$code"
}

# shellcheck source=install-syft.sh
source "${SCRIPT_DIR}/install-syft.sh"

ensure_syft() {
  install_syft
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    -h | --help)
      usage 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      ;;
  esac
done

if [[ -z "$OUTPUT_DIR" ]]; then
  echo "--output-dir is required" >&2
  usage
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required to build the Python wheel" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

wheel_dir="$(mktemp -d)"
trap 'rm -rf "${wheel_dir}"' EXIT

echo "==> Building Python wheel"
uv build --wheel --out-dir "${wheel_dir}"

wheel="$(ls -1 "${wheel_dir}"/*.whl 2>/dev/null | head -1 || true)"
if [[ -z "$wheel" ]]; then
  echo "No wheel found under ${wheel_dir}/" >&2
  exit 1
fi

ensure_syft
sbom_file="${OUTPUT_DIR}/$(basename "${wheel}").cdx.json"
echo "==> Generating CycloneDX SBOM for ${wheel}"
"${SYFT_BIN}" "file:${wheel}" -o cyclonedx-json@1.5 >"${sbom_file}"
echo "OK SBOM: ${sbom_file}"
