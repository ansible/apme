#!/usr/bin/env bash
# Lint and package the APME Helm chart.
# Invoked via: tox -e helm
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="${ROOT}/deploy/helm/apme"
OUT_DIR="${ROOT}/dist/charts"
HELM_VERSION="${HELM_VERSION:-v3.16.4}"
CACHE_DIR="${ROOT}/.tox/helm-tools"
HELM_BIN="${CACHE_DIR}/helm"

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
  curl -fsSL "https://get.helm.sh/${tarball}" -o "${CACHE_DIR}/${tarball}"
  curl -fsSL "https://get.helm.sh/${sumfile}" -o "${CACHE_DIR}/${sumfile}"
  (
    cd "${CACHE_DIR}"
    sha256sum -c "${sumfile}"
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
