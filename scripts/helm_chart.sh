#!/usr/bin/env bash
# Lint and package the APME Helm chart.
# Invoked via: tox -e helm
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="${ROOT}/deploy/helm/apme"
OUT_DIR="${ROOT}/dist/charts"
# Accept HELM_VERSION with or without a leading "v" (tarball names use "v…").
HELM_VERSION="${HELM_VERSION:-v3.16.4}"
HELM_VERSION="v${HELM_VERSION#v}"
CACHE_DIR="${ROOT}/.tox/helm-tools"
HELM_BIN="${CACHE_DIR}/helm"

download() {
  local url="$1" dest="$2"
  if ! command -v curl >/dev/null 2>&1; then
    echo "curl is required to download Helm (not found on PATH)" >&2
    exit 1
  fi
  curl -fsSL --retry 3 --retry-delay 2 \
    -o "${dest}" "${url}"
}

verify_sha256() {
  # Portable checksum check: GNU coreutils on Linux, shasum on macOS.
  local sumfile="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum -c "${sumfile}"
    return
  fi
  if command -v shasum >/dev/null 2>&1; then
    local expected actual filename
    # Helm publishes "<hash>  <filename>" (two spaces) or "<hash> *<filename>".
    # Use field splitting so consecutive whitespace does not drop the filename.
    expected="$(awk '{print $1; exit}' "${sumfile}")"
    filename="$(awk '{print $2; exit}' "${sumfile}")"
    filename="${filename#\*}"
    if [[ -z "${expected}" || -z "${filename}" ]]; then
      echo "Unable to parse checksum file: ${sumfile}" >&2
      exit 1
    fi
    actual="$(shasum -a 256 "${filename}" | awk '{print $1}')"
    if [[ "${actual}" != "${expected}" ]]; then
      echo "Checksum mismatch for ${filename}" >&2
      echo "  expected: ${expected}" >&2
      echo "  actual:   ${actual}" >&2
      exit 1
    fi
    echo "${filename}: OK"
    return
  fi
  echo "Neither sha256sum nor shasum found; cannot verify Helm download" >&2
  exit 1
}

ensure_helm() {
  # Prefer a cached binary matching HELM_VERSION so CI/local stay aligned.
  if [[ -x "${HELM_BIN}" ]] \
    && "${HELM_BIN}" version --short 2>/dev/null | grep -Fq "${HELM_VERSION#v}"; then
    return
  fi
  if command -v helm >/dev/null 2>&1; then
    local found
    found="$(command -v helm)"
    if "${found}" version --short 2>/dev/null | grep -Fq "${HELM_VERSION#v}"; then
      HELM_BIN="${found}"
      return
    fi
  fi
  mkdir -p "${CACHE_DIR}"
  local os arch tarball sumfile
  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"
  case "${arch}" in
    x86_64) arch="amd64" ;;
    aarch64 | arm64) arch="arm64" ;;
    *)
      echo "Unsupported architecture: ${arch}" >&2
      exit 1
      ;;
  esac
  tarball="helm-${HELM_VERSION}-${os}-${arch}.tar.gz"
  sumfile="${tarball}.sha256sum"
  echo "Downloading Helm ${HELM_VERSION}..."
  download "https://get.helm.sh/${tarball}" "${CACHE_DIR}/${tarball}"
  download "https://get.helm.sh/${sumfile}" "${CACHE_DIR}/${sumfile}"
  (
    cd "${CACHE_DIR}"
    verify_sha256 "${sumfile}"
  )
  tar -xzf "${CACHE_DIR}/${tarball}" -C "${CACHE_DIR}" "${os}-${arch}/helm"
  mv "${CACHE_DIR}/${os}-${arch}/helm" "${HELM_BIN}"
  rm -rf "${CACHE_DIR}/${os}-${arch}" "${CACHE_DIR}/${tarball}" "${CACHE_DIR}/${sumfile}"
  chmod +x "${HELM_BIN}"
}

ensure_helm

echo "==> helm lint ${CHART_DIR}"
"${HELM_BIN}" lint "${CHART_DIR}"

mkdir -p "${OUT_DIR}"
echo "==> helm package ${CHART_DIR} -> ${OUT_DIR}"
"${HELM_BIN}" package "${CHART_DIR}" -d "${OUT_DIR}"

echo "OK: packaged chart(s) in ${OUT_DIR}"
ls -la "${OUT_DIR}"
