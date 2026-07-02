#!/usr/bin/env bash
# Start the APME pod (Primary, Native, Ansible, OPA, Gitleaks, Galaxy Proxy). Run from repo root.
# CLI is not part of the pod; use run-cli.sh to run a scan with CWD mounted.
#
# Cache host path: default is XDG cache (${XDG_CACHE_HOME:-$HOME/.cache}/apme).
# Override: APME_CACHE_HOST_PATH=/my/cache ./up.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Relabel a host file so rootless Podman containers can read it under SELinux.
_relabel_host_path_for_podman() {
  local host_path="$1"
  if command -v chcon >/dev/null 2>&1 && [[ "$(getenforce 2>/dev/null)" == "Enforcing" ]]; then
    if ! chcon -t container_file_t "$host_path" 2>/dev/null; then
      echo "WARNING: could not relabel $host_path for SELinux; the container may not read this mount" >&2
    fi
  fi
}

# Default: XDG cache dir (persists across reboots); override with APME_CACHE_HOST_PATH
CACHE_PATH="${APME_CACHE_HOST_PATH:-${XDG_CACHE_HOME:-$HOME/.cache}/apme}"

if [[ "$CACHE_PATH" != /* ]]; then
  echo "ERROR: APME_CACHE_HOST_PATH must be an absolute path (got: $CACHE_PATH)" >&2
  exit 1
fi

if [[ "$CACHE_PATH" == *$'\n'* ]]; then
  echo "ERROR: APME_CACHE_HOST_PATH must not contain newlines" >&2
  exit 1
fi

mkdir -p "$CACHE_PATH"

# Load Abbenay secrets (.env) if present.
ABBENAY_ENV="$ROOT/containers/abbenay/.env"
if [[ -f "$ABBENAY_ENV" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ABBENAY_ENV"
  set +a
fi
OPENROUTER_API_KEY="${OPENROUTER_API_KEY:-}"
VERTEX_ANTHROPIC_API_KEY="${VERTEX_ANTHROPIC_API_KEY:-}"
ABBENAY_GCP_CREDENTIALS="${ABBENAY_GCP_CREDENTIALS:-}"
GOOGLE_VERTEX_PROJECT="${GOOGLE_VERTEX_PROJECT:-}"
GOOGLE_VERTEX_LOCATION="${GOOGLE_VERTEX_LOCATION:-us-east5}"
APME_AI_MODEL="${APME_AI_MODEL:-}"
APME_FEEDBACK_ENABLED="${APME_FEEDBACK_ENABLED:-true}"
APME_FEEDBACK_GITHUB_REPO="${APME_FEEDBACK_GITHUB_REPO:-}"
APME_FEEDBACK_GITHUB_TOKEN="${APME_FEEDBACK_GITHUB_TOKEN:-}"

# Optional: CA bundle for outbound HTTPS clients that need an internal or
# self-signed trust anchor. Set ABBENAY_CA_BUNDLE to the absolute path of a
# PEM CA bundle file.
ABBENAY_CA_BUNDLE="${ABBENAY_CA_BUNDLE:-}"
if [[ -n "$ABBENAY_CA_BUNDLE" ]]; then
  if [[ "$ABBENAY_CA_BUNDLE" != /* ]]; then
    echo "ERROR: ABBENAY_CA_BUNDLE must be an absolute path (got: $ABBENAY_CA_BUNDLE)" >&2
    exit 1
  fi
  if [[ "$ABBENAY_CA_BUNDLE" == *$'\n'* ]]; then
    echo "ERROR: ABBENAY_CA_BUNDLE must not contain newlines" >&2
    exit 1
  fi
  if [[ ! -f "$ABBENAY_CA_BUNDLE" ]]; then
    echo "ERROR: ABBENAY_CA_BUNDLE points to a file that does not exist: $ABBENAY_CA_BUNDLE" >&2
    exit 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: ABBENAY_CA_BUNDLE requires python3 to patch the pod YAML, but python3 was not found in PATH" >&2
    exit 1
  fi
fi

# Tear down any existing pod so we get a clean start.
if podman pod exists apme-pod 2>/dev/null; then
  echo "Stopping existing apme-pod..."
  podman pod stop apme-pod 2>/dev/null || true
  podman pod rm apme-pod 2>/dev/null || true
fi

# Pod YAML cannot use env vars; we inject values via envsubst.
# CACHE_PATH is escaped for sed since it may contain special chars;
# everything else goes through envsubst so secrets stay out of argv.
ESCAPED_PATH=$(printf '%s\n' "$CACHE_PATH" | sed -e 's/\\/\\\\/g' -e 's/[&|]/\\&/g')
export OPENROUTER_API_KEY VERTEX_ANTHROPIC_API_KEY APME_AI_MODEL APME_ROOT="$ROOT"
export APME_FEEDBACK_ENABLED APME_FEEDBACK_GITHUB_REPO APME_FEEDBACK_GITHUB_TOKEN

# Build the pod YAML: substitute cache path and env vars.
POD_YAML=$(sed "s|path: __APME_CACHE_PATH__|path: ${ESCAPED_PATH}|" containers/podman/pod.yaml \
  | envsubst '$OPENROUTER_API_KEY $VERTEX_ANTHROPIC_API_KEY $APME_AI_MODEL $APME_ROOT $APME_FEEDBACK_ENABLED $APME_FEEDBACK_GITHUB_REPO $APME_FEEDBACK_GITHUB_TOKEN')

# When a CA bundle is provided, inject the standard CA env vars and mounts for
# the containers that make outbound HTTPS requests (gateway, abbenay, galaxy-proxy).
if [[ -n "$ABBENAY_CA_BUNDLE" ]]; then
  CA_MOUNT_PATH="/etc/ssl/certs/custom-ca-bundle.pem"
  POD_YAML=$(python3 -c "
import json, sys, os
yaml = sys.stdin.read()
ca_path = os.environ['ABBENAY_CA_BUNDLE']
mount = '$CA_MOUNT_PATH'
ca_path_yaml = json.dumps(ca_path)
mount_yaml = json.dumps(mount)
abbenay_env_marker = '        - name: XDG_RUNTIME_DIR'
abbenay_vol_marker = '          readOnly: true\n    - name: galaxy-proxy'
gateway_env_marker = '        - name: APME_FEEDBACK_GITHUB_TOKEN'
gateway_vol_marker = '      volumeMounts:\n        - name: gateway-data'
galaxy_marker = '    - name: galaxy-proxy\n      image: apme-galaxy-proxy:latest'
galaxy_vol_marker = '      volumeMounts:\n        - name: proxy-cache'
if (
    abbenay_env_marker not in yaml
    or abbenay_vol_marker not in yaml
    or gateway_env_marker not in yaml
    or gateway_vol_marker not in yaml
    or galaxy_marker not in yaml
    or galaxy_vol_marker not in yaml
):
    print('ERROR: pod.yaml markers not found; CA bundle injection failed', file=sys.stderr)
    sys.exit(1)
yaml = yaml.replace(
    abbenay_env_marker,
    '        - name: NODE_EXTRA_CA_CERTS\n'
    '          value: ' + mount_yaml + '\n'
    '        ' + abbenay_env_marker.lstrip())
yaml = yaml.replace(
    abbenay_vol_marker,
    '          readOnly: true\n'
    '        - name: abbenay-ca-bundle\n'
    '          mountPath: ' + mount_yaml + '\n'
    '          readOnly: true\n'
    '    - name: galaxy-proxy')
yaml = yaml.replace(
    gateway_env_marker,
    (
        '        - name: SSL_CERT_FILE\n'
        '          value: ' + mount_yaml + '\n'
        '        - name: REQUESTS_CA_BUNDLE\n'
        '          value: ' + mount_yaml + '\n'
        '        - name: CURL_CA_BUNDLE\n'
        '          value: ' + mount_yaml + '\n'
        '        - name: GIT_SSL_CAINFO\n'
        '          value: ' + mount_yaml + '\n'
        + gateway_env_marker
    ))
yaml = yaml.replace(
    gateway_vol_marker,
    '      volumeMounts:\n'
    '        - name: gateway-ca-bundle\n'
    '          mountPath: ' + mount_yaml + '\n'
    '          readOnly: true\n'
    '        - name: gateway-data')
# Galaxy Proxy: add env section + CA env vars
yaml = yaml.replace(
    galaxy_marker,
    (
        galaxy_marker + '\n'
        '      env:\n'
        '        - name: SSL_CERT_FILE\n'
        '          value: ' + mount_yaml + '\n'
        '        - name: REQUESTS_CA_BUNDLE\n'
        '          value: ' + mount_yaml + '\n'
        '        - name: CURL_CA_BUNDLE\n'
        '          value: ' + mount_yaml + '\n'
        '        - name: GIT_SSL_CAINFO\n'
        '          value: ' + mount_yaml
    ),
)
# Galaxy Proxy: add CA volume mount
yaml = yaml.replace(
    galaxy_vol_marker,
    '      volumeMounts:\n'
    '        - name: galaxy-ca-bundle\n'
    '          mountPath: ' + mount_yaml + '\n'
    '          readOnly: true\n'
    '        - name: proxy-cache')
yaml = yaml.rstrip() + '\n' \
    '    - name: abbenay-ca-bundle\n' \
    '      hostPath:\n' \
    '        path: ' + ca_path_yaml + '\n' \
    '        type: File\n' \
    '    - name: gateway-ca-bundle\n' \
    '      hostPath:\n' \
    '        path: ' + ca_path_yaml + '\n' \
    '        type: File\n' \
    '    - name: galaxy-ca-bundle\n' \
    '      hostPath:\n' \
    '        path: ' + ca_path_yaml + '\n' \
    '        type: File\n'
print(yaml)
" <<< "$POD_YAML")
  echo "CA bundle enabled for gateway/abbenay/galaxy-proxy: $ABBENAY_CA_BUNDLE -> $CA_MOUNT_PATH (inside container)"
fi

# Optional: GCP service account / ADC JSON for direct Vertex AI (vertex-anthropic engine).
# Set ABBENAY_GCP_CREDENTIALS to the absolute host path of the credentials file and
# GOOGLE_VERTEX_PROJECT (plus optional GOOGLE_VERTEX_LOCATION) in containers/abbenay/.env.
# See docs/guides/ABBENAY_AI.md — mirrors the Helm chart mount at
# /var/run/secrets/gcp/service-account-key.json.
if [[ -n "$ABBENAY_GCP_CREDENTIALS" ]]; then
  if [[ "$ABBENAY_GCP_CREDENTIALS" != /* ]]; then
    echo "ERROR: ABBENAY_GCP_CREDENTIALS must be an absolute path (got: $ABBENAY_GCP_CREDENTIALS)" >&2
    exit 1
  fi
  if [[ "$ABBENAY_GCP_CREDENTIALS" == *$'\n'* ]]; then
    echo "ERROR: ABBENAY_GCP_CREDENTIALS must not contain newlines" >&2
    exit 1
  fi
  if [[ ! -f "$ABBENAY_GCP_CREDENTIALS" ]]; then
    echo "ERROR: ABBENAY_GCP_CREDENTIALS points to a file that does not exist: $ABBENAY_GCP_CREDENTIALS" >&2
    exit 1
  fi
  if [[ -z "$GOOGLE_VERTEX_PROJECT" ]]; then
    echo "ERROR: GOOGLE_VERTEX_PROJECT is required when ABBENAY_GCP_CREDENTIALS is set" >&2
    exit 1
  fi
  if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: ABBENAY_GCP_CREDENTIALS requires python3 to patch the pod YAML, but python3 was not found in PATH" >&2
    exit 1
  fi
  GCP_CREDENTIALS_MOUNT="/var/run/secrets/gcp/service-account-key.json"
  GCP_CREDS_CACHE="$CACHE_PATH/abbenay/gcp-credentials.json"
  mkdir -p "$(dirname "$GCP_CREDS_CACHE")"
  # Stage credentials into the cache dir with mode 0644 so the non-root abbenay
  # user in the pod can read the mount (rootless Podman cannot read host 0600 files).
  install -m 0644 "$ABBENAY_GCP_CREDENTIALS" "$GCP_CREDS_CACHE"
  _relabel_host_path_for_podman "$GCP_CREDS_CACHE"
  export ABBENAY_GCP_CREDENTIALS="$GCP_CREDS_CACHE"
  export GOOGLE_VERTEX_PROJECT GOOGLE_VERTEX_LOCATION
  POD_YAML=$(python3 -c "
import json, sys, os
yaml = sys.stdin.read()
cred_path = os.environ['ABBENAY_GCP_CREDENTIALS']
gcp_project = os.environ['GOOGLE_VERTEX_PROJECT']
gcp_location = os.environ['GOOGLE_VERTEX_LOCATION']
mount = '$GCP_CREDENTIALS_MOUNT'
cred_path_yaml = json.dumps(cred_path)
mount_yaml = json.dumps(mount)
project_yaml = json.dumps(gcp_project)
location_yaml = json.dumps(gcp_location)
abbenay_env_marker = '        - name: XDG_RUNTIME_DIR'
abbenay_config_mount_marker = (
    '          mountPath: /home/abbenay/.config/abbenay/config.yaml\n'
    '          readOnly: true'
)
if abbenay_env_marker not in yaml or abbenay_config_mount_marker not in yaml:
    print('ERROR: pod.yaml markers not found; GCP credentials injection failed', file=sys.stderr)
    sys.exit(1)
yaml = yaml.replace(
    abbenay_env_marker,
    '        - name: GOOGLE_APPLICATION_CREDENTIALS\n'
    '          value: ' + mount_yaml + '\n'
    '        - name: GOOGLE_VERTEX_PROJECT\n'
    '          value: ' + project_yaml + '\n'
    '        - name: GOOGLE_VERTEX_LOCATION\n'
    '          value: ' + location_yaml + '\n'
    '        ' + abbenay_env_marker.lstrip())
yaml = yaml.replace(
    abbenay_config_mount_marker,
    abbenay_config_mount_marker + '\n'
    '        - name: abbenay-gcp-credentials\n'
    '          mountPath: ' + mount_yaml + '\n'
    '          readOnly: true')
yaml = yaml.rstrip() + '\n' \
    '    - name: abbenay-gcp-credentials\n' \
    '      hostPath:\n' \
    '        path: ' + cred_path_yaml + '\n' \
    '        type: File\n'
print(yaml)
" <<< "$POD_YAML")
  echo "Vertex AI credentials enabled for abbenay: $ABBENAY_GCP_CREDENTIALS -> $GCP_CREDENTIALS_MOUNT (inside container)"
  echo "Vertex AI project: $GOOGLE_VERTEX_PROJECT (location: $GOOGLE_VERTEX_LOCATION)"
fi

_relabel_host_path_for_podman "$ROOT/containers/abbenay/config.yaml"
if [[ -n "$ABBENAY_CA_BUNDLE" ]]; then
  _relabel_host_path_for_podman "$ABBENAY_CA_BUNDLE"
fi

echo "$POD_YAML" | podman play kube -

echo "Pod apme-pod started (cache: $CACHE_PATH). Run a scan: containers/podman/run-cli.sh"

if [[ -n "$APME_FEEDBACK_GITHUB_REPO" && -n "$APME_FEEDBACK_GITHUB_TOKEN" ]]; then
  echo "Issue reporting enabled (repo: $APME_FEEDBACK_GITHUB_REPO)"
else
  echo "Issue reporting disabled. To enable, export APME_FEEDBACK_GITHUB_REPO and APME_FEEDBACK_GITHUB_TOKEN."
fi
