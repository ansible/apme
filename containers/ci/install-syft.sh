#!/usr/bin/env bash
# Install a pinned syft release with checksum verification.
#
# Usage (source from other containers/ci scripts):
#   source "$(dirname "${BASH_SOURCE[0]}")/install-syft.sh"
#   install_syft
#
# Environment:
#   SYFT_VERSION      Release version without leading v (default: 1.21.0)
#   SYFT_INSTALL_DIR  Install directory (default: ${HOME}/.cache/apme/bin)
set -euo pipefail

_INSTALL_SYFT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SYFT_VERSION="${SYFT_VERSION:-1.21.0}"
SYFT_INSTALL_DIR="${SYFT_INSTALL_DIR:-${HOME}/.cache/apme/bin}"
SYFT_BIN="${SYFT_INSTALL_DIR}/syft"
SYFT_CHECKSUMS_FILE="${_INSTALL_SYFT_DIR}/syft-release-checksums.txt"

sha256_file() {
  local file="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "${file}" | awk '{ print $1 }'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "${file}" | awk '{ print $1 }'
  else
    echo "Neither sha256sum nor shasum is available for checksum verification" >&2
    return 1
  fi
}

sha256_string() {
  local data="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    printf '%s' "${data}" | sha256sum | awk '{ print $1 }'
  elif command -v shasum >/dev/null 2>&1; then
    printf '%s' "${data}" | shasum -a 256 | awk '{ print $1 }'
  else
    echo "Neither sha256sum nor shasum is available for checksum verification" >&2
    return 1
  fi
}

lookup_committed_checksum() {
  local version="$1"
  local os="$2"
  local arch="$3"
  awk -v ver="${version}" -v os="${os}" -v arch="${arch}" \
    '$1 == ver && $2 == os && $3 == arch { print $4; exit }' "${SYFT_CHECKSUMS_FILE}"
}

install_syft() (
  local os arch archive version url tmpdir expected actual

  if [[ -x "${SYFT_BIN}" ]]; then
    return 0
  fi

  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  arch="$(uname -m)"
  case "$arch" in
    x86_64) arch=amd64 ;;
    aarch64 | arm64) arch=arm64 ;;
    *)
      echo "Unsupported architecture for syft install: ${arch}" >&2
      return 1
      ;;
  esac

  if [[ ! -f "${SYFT_CHECKSUMS_FILE}" ]]; then
    echo "Syft checksum file not found: ${SYFT_CHECKSUMS_FILE}" >&2
    return 1
  fi

  expected="$(lookup_committed_checksum "${SYFT_VERSION}" "${os}" "${arch}")"
  if [[ -z "$expected" ]]; then
    echo "Unsupported syft release/platform: ${SYFT_VERSION} (${os}/${arch})" >&2
    echo "Add a committed checksum to ${SYFT_CHECKSUMS_FILE} after verifying the release." >&2
    return 1
  fi

  version="v${SYFT_VERSION}"
  archive="syft_${SYFT_VERSION}_${os}_${arch}.tar.gz"
  url="https://github.com/anchore/syft/releases/download/${version}/${archive}"
  tmpdir="$(mktemp -d)"
  trap 'rm -rf -- "${tmpdir}"' EXIT

  echo "==> Installing syft ${version} (${os}/${arch})"
  curl -sSfL -o "${tmpdir}/${archive}" "${url}"

  actual="$(sha256_file "${tmpdir}/${archive}")"
  if [[ "$actual" != "$expected" ]]; then
    echo "Checksum mismatch for ${archive}" >&2
    return 1
  fi

  tar -xzf "${tmpdir}/${archive}" -C "${tmpdir}" syft
  install -d "${SYFT_INSTALL_DIR}"
  install -m 0755 "${tmpdir}/syft" "${SYFT_BIN}"
)
