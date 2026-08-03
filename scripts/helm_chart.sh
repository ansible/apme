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

assert_template_contains() {
  local desc="$1" haystack="$2" needle="$3"
  if [[ "${haystack}" != *"${needle}"* ]]; then
    echo "FAIL: ${desc}: expected to find: ${needle}" >&2
    exit 1
  fi
}

assert_template_lacks() {
  local desc="$1" haystack="$2" needle="$3"
  if [[ "${haystack}" == *"${needle}"* ]]; then
    echo "FAIL: ${desc}: did not expect: ${needle}" >&2
    exit 1
  fi
}

assert_fail_message() {
  local desc="$1" err="$2" needle="$3"
  if [[ "${err}" != *"${needle}"* ]]; then
    echo "FAIL: ${desc}: expected error containing: ${needle}" >&2
    echo "got: ${err}" >&2
    exit 1
  fi
}

echo "==> helm template assertions (ADR-069 Simple)"
RENDER="$("${HELM_BIN}" template test-release "${CHART_DIR}" \
  --set abbenay.enabled=true \
  --set abbenay.token=test-token \
  --set abbenay.providers.openrouter.engine=openrouter \
  --set abbenay.providers.openrouter.apiKey=sk-test \
  --set 'abbenay.providers.openrouter.models.anthropic/claude-sonnet-4={}')"

DEPLOY_COUNT="$(grep -c '^kind: Deployment$' <<<"${RENDER}" || true)"
if [[ "${DEPLOY_COUNT}" != "1" ]]; then
  echo "FAIL: expected exactly 1 Deployment, got ${DEPLOY_COUNT}" >&2
  exit 1
fi

assert_template_contains "gateway sidecar" "${RENDER}" $'- name: gateway\n'
assert_template_contains "ui sidecar" "${RENDER}" $'- name: ui\n'
assert_template_contains "abbenay sidecar" "${RENDER}" $'- name: abbenay\n'
assert_template_contains "Abbenay loopback" "${RENDER}" "--grpc-host"
assert_template_contains "Abbenay loopback host" "${RENDER}" "127.0.0.1"
assert_template_contains "Abbenay HTTP web (ADR-070)" "${RENDER}" '"web"'
assert_template_contains "Abbenay HTTP port (ADR-070)" "${RENDER}" '"8787"'
assert_template_contains "Abbenay HTTP host loopback (ADR-070)" "${RENDER}" $'--host"\n            - "127.0.0.1"'
assert_template_contains "Gateway Abbenay HTTP URL (ADR-070)" "${RENDER}" 'value: "http://127.0.0.1:8787"'
assert_template_lacks "no Abbenay HTTP Service port" "${RENDER}" "name: abbenay-http"
assert_template_lacks "no Abbenay HTTP hostPort" "${RENDER}" $'containerPort: 8787\n          hostPort:'
assert_template_contains "reporting localhost" "${RENDER}" 'value: "127.0.0.1:50060"'
assert_template_contains "abbenay addr localhost" "${RENDER}" 'value: "127.0.0.1:50057"'
assert_template_contains "gateway primary addr localhost" "${RENDER}" 'value: "127.0.0.1:50051"'
assert_template_contains "gateway Service" "${RENDER}" "name: test-release-apme-gateway"
assert_template_contains "engine Deployment" "${RENDER}" "name: test-release-apme-engine"

PORTAL_RENDER="$("${HELM_BIN}" template test-release "${CHART_DIR}" \
  -f "${CHART_DIR}/values-portal.yaml")"
assert_template_lacks "portal: no ui container" "${PORTAL_RENDER}" $'- name: ui\n'
assert_template_contains "portal: gateway present" "${PORTAL_RENDER}" $'- name: gateway\n'

REPLICAS_ERR="$("${HELM_BIN}" template test-release "${CHART_DIR}" \
  --set engine.replicas=2 2>&1 >/dev/null)" && {
  echo "FAIL: expected helm template to fail when engine.replicas=2" >&2
  exit 1
}
assert_fail_message "replicas>1" "${REPLICAS_ERR}" "engine.replicas must be 1"

HPA_ERR="$("${HELM_BIN}" template test-release "${CHART_DIR}" \
  --set autoscaling.enabled=true 2>&1 >/dev/null)" && {
  echo "FAIL: expected helm template to fail when autoscaling.enabled=true" >&2
  exit 1
}
assert_fail_message "HPA rejected" "${HPA_ERR}" "autoscaling.enabled must be false"

# Confirm Service selectors + no Abbenay Service / extra Deployments
RENDER_FILE="$(mktemp)"
trap 'rm -f "${RENDER_FILE}"' EXIT
printf '%s' "${RENDER}" >"${RENDER_FILE}"
python3 - "${RENDER_FILE}" <<'PY'
import sys
from pathlib import Path

text = Path(sys.argv[1]).read_text(encoding="utf-8")
docs = [d for d in text.split("---") if d.strip()]

deployments = [d for d in docs if "\nkind: Deployment" in d or d.lstrip().startswith("kind: Deployment")]
if len(deployments) != 1:
    raise SystemExit(f"FAIL: expected 1 Deployment doc, got {len(deployments)}")

services = [d for d in docs if "\nkind: Service" in d or d.lstrip().startswith("kind: Service")]
svc_names = []
for d in services:
    for line in d.splitlines():
        stripped = line.strip()
        if stripped.startswith("name: test-release-apme-"):
            svc_names.append(stripped.split("name: ", 1)[1])
            break
if any(n == "test-release-apme-abbenay" for n in svc_names):
    raise SystemExit(f"FAIL: Abbenay Service must not be rendered: {svc_names}")
if "test-release-apme-gateway" not in svc_names:
    raise SystemExit(f"FAIL: gateway Service missing: {svc_names}")

for d in services:
    if "name: test-release-apme-gateway" in d or "name: test-release-apme-ui" in d:
        sel = d.split("selector:", 1)[-1]
        if "app.kubernetes.io/component: engine" not in sel:
            raise SystemExit("FAIL: gateway/ui Service must select component: engine")

if "--grpc-host" not in text or "127.0.0.1" not in text:
    raise SystemExit("FAIL: Abbenay must bind 127.0.0.1")
# Gateway gRPC stays pod-local (Primary → 127.0.0.1:50060); Service exposes HTTP only.
for d in services:
    if "port: 50060" in d:
        raise SystemExit(
            "FAIL: Gateway Service must not expose gRPC 50060 (ADR-069 localhost)"
        )
print("OK: Simple topology template checks passed")
PY
mkdir -p "${OUT_DIR}"
echo "==> helm package ${CHART_DIR} -> ${OUT_DIR}"
"${HELM_BIN}" package "${CHART_DIR}" -d "${OUT_DIR}"

echo "OK: packaged chart(s) in ${OUT_DIR}"
ls -la "${OUT_DIR}"
