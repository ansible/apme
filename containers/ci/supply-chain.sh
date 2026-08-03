#!/usr/bin/env bash
# Sign published multi-arch images and generate CycloneDX SBOMs (issues #203, #204).
#
# Expects consumer tags already merged on GHCR:
#   ghcr.io/<owner>/apme-<name>:<tag>
#
# Image names are read from containers/ci/images.txt (single source of truth).
#
# Usage:
#   supply-chain.sh --owner ansible --tags-file /tmp/tags.txt --output-dir /tmp/sboms
#   supply-chain.sh ... --quay-ns ansible          # also sign Quay copies when published
#   supply-chain.sh ... --skip-sign                  # SBOM generation only (no cosign)
#   supply-chain.sh ... --list-subjects /tmp/subjects.json  # write provenance subjects
#
# Locally runnable (lean CI): logic lives here, not only in workflow YAML.
# Requires: docker buildx, cosign (for signing), syft (installed automatically if missing).
set -euo pipefail

OWNER=""
TAGS_FILE=""
OUTPUT_DIR=""
QUAY_NS=""
SKIP_SIGN=0
LIST_SUBJECTS=""
GHCR_REGISTRY="${GHCR_REGISTRY:-ghcr.io}"
QUAY_REGISTRY="${QUAY_REGISTRY:-quay.io}"
ENGINE="${CONTAINER_ENGINE:-docker}"
# Cap concurrent registry ops (sign + SBOM per image/tag).
SUPPLY_CHAIN_PARALLELISM="${SUPPLY_CHAIN_PARALLELISM:-4}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGES_FILE="${IMAGES_FILE:-${SCRIPT_DIR}/images.txt}"

usage() {
  local code="${1:-1}"
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
  exit "$code"
}

load_images() {
  local line
  IMAGES=()
  if [[ ! -f "$IMAGES_FILE" ]]; then
    echo "Images file not found: $IMAGES_FILE" >&2
    exit 1
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line//$'\r'/}"
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    IMAGES+=("$line")
  done <"$IMAGES_FILE"
  if [[ ${#IMAGES[@]} -eq 0 ]]; then
    echo "No images listed in ${IMAGES_FILE}" >&2
    exit 1
  fi
}

trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  s="${s%"${s##*[![:space:]]}"}"
  printf '%s' "$s"
}

# shellcheck source=install-syft.sh
source "${SCRIPT_DIR}/install-syft.sh"

ensure_syft() {
  install_syft
}

ref_slug() {
  local ref="$1"
  local slug hash
  # Readable prefix plus hash of full ref avoids collisions (e.g. foo/bar_baz vs foo_bar/baz).
  slug="$(printf '%s' "${ref%:*}" | tr '/:.' '_')"
  hash="$(sha256_string "${ref}" | awk '{ print substr($1, 1, 8) }')"
  printf '%s_%s' "${slug}" "${hash}"
}

image_digest() {
  local ref="$1"
  "${ENGINE}" buildx imagetools inspect "${ref}" --format '{{.Manifest.Digest}}'
}

registry_refs() {
  local name="$1"
  local tag="$2"
  local -a refs=()
  refs+=("${GHCR_REGISTRY}/${OWNER}/apme-${name}:${tag}")
  if [[ -n "$QUAY_NS" ]]; then
    refs+=("${QUAY_REGISTRY}/${QUAY_NS}/apme-${name}:${tag}")
  fi
  printf '%s\n' "${refs[@]}"
}

# Run up to SUPPLY_CHAIN_PARALLELISM background jobs; fail if any child fails.
run_parallel() {
  local -a pids=()
  local -a labels=()
  local fail=0
  local pid label done_label item fn i
  local -a parts=()
  local -a fn_args=()

  if [[ $# -eq 0 ]]; then
    echo "run_parallel: no tasks" >&2
    return 1
  fi

  for item in "$@"; do
    parts=()
    fn_args=()
    IFS=$'\t' read -r -a parts <<<"$item"
    if [[ ${#parts[@]} -lt 2 ]]; then
      echo "ERROR: malformed parallel task (need label\\tfunction[\\targs...]): ${item}" >&2
      return 1
    fi
    label="${parts[0]}"
    fn="${parts[1]}"
    if [[ ${#parts[@]} -gt 2 ]]; then
      fn_args=("${parts[@]:2}")
    fi
    case "$fn" in
      process_image_tag | process_ref) ;;
      *)
        echo "ERROR: refused unknown parallel function: ${fn}" >&2
        return 1
        ;;
    esac

    while [[ ${#pids[@]} -ge $SUPPLY_CHAIN_PARALLELISM ]]; do
      pid="${pids[0]}"
      done_label="${labels[0]}"
      pids=("${pids[@]:1}")
      labels=("${labels[@]:1}")
      if ! wait "$pid"; then
        echo "ERROR: parallel task failed: ${done_label}" >&2
        fail=1
      fi
    done
    (
      set -euo pipefail
      "$fn" "${fn_args[@]}"
    ) &
    pids+=("$!")
    labels+=("$label")
  done

  for i in "${!pids[@]}"; do
    pid="${pids[$i]}"
    done_label="${labels[$i]}"
    if ! wait "$pid"; then
      echo "ERROR: parallel task failed: ${done_label}" >&2
      fail=1
    fi
  done

  if [[ "$fail" -ne 0 ]]; then
    return 1
  fi
}

process_ref() {
  local ref="$1"
  local name="$2"
  local tag="$3"
  local digest sbom_file safe_tag
  digest="$(image_digest "${ref}")"
  safe_tag="${tag//\//_}"
  sbom_file="${OUTPUT_DIR}/$(ref_slug "${ref}")-${safe_tag}.cdx.json"

  echo "==> ${ref} @ ${digest}"

  if [[ "$SKIP_SIGN" -eq 0 ]]; then
    cosign sign --yes "${ref}@${digest}"
  fi

  "${SYFT_BIN}" "${ref}@${digest}" -o cyclonedx-json@1.5 >"${sbom_file}"
  echo "OK SBOM: ${sbom_file}"

  if [[ "$SKIP_SIGN" -eq 0 ]]; then
    cosign attest --yes \
      --predicate "${sbom_file}" \
      --type cyclonedx \
      "${ref}@${digest}"
  fi

  if [[ -n "$LIST_SUBJECTS" ]]; then
    # subject-name for attest-build-provenance excludes the tag.
    local subject_name="${ref%:*}"
    printf '%s\t%s\n' "${subject_name}" "${digest}" \
      >"${LIST_SUBJECTS}.d/$(ref_slug "${ref}")-${safe_tag}.row"
  fi
}

process_image_tag() {
  local name="$1"
  local tag="$2"
  local ref
  while IFS= read -r ref; do
    [[ -z "$ref" ]] && continue
    process_ref "${ref}" "${name}" "${tag}"
  done < <(registry_refs "${name}" "${tag}")
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --owner)
      OWNER="${2:-}"
      shift 2
      ;;
    --tags-file)
      TAGS_FILE="${2:-}"
      shift 2
      ;;
    --output-dir)
      OUTPUT_DIR="${2:-}"
      shift 2
      ;;
    --quay-ns)
      QUAY_NS="${2:-}"
      shift 2
      ;;
    --skip-sign)
      SKIP_SIGN=1
      shift
      ;;
    --list-subjects)
      LIST_SUBJECTS="${2:-}"
      shift 2
      ;;
    --parallelism)
      SUPPLY_CHAIN_PARALLELISM="${2:-}"
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

if [[ -z "$OWNER" || -z "$TAGS_FILE" || -z "$OUTPUT_DIR" ]]; then
  echo "--owner, --tags-file, and --output-dir are required" >&2
  usage
fi

if [[ ! -f "$TAGS_FILE" ]]; then
  echo "Tags file not found: $TAGS_FILE" >&2
  exit 1
fi

if ! [[ "$SUPPLY_CHAIN_PARALLELISM" =~ ^[1-9][0-9]*$ ]]; then
  echo "SUPPLY_CHAIN_PARALLELISM must be a positive integer (got: ${SUPPLY_CHAIN_PARALLELISM})" >&2
  exit 1
fi

if [[ "$SKIP_SIGN" -eq 0 ]] && ! command -v cosign >/dev/null 2>&1; then
  echo "cosign is required for signing (use --skip-sign to generate SBOMs only)" >&2
  exit 1
fi

mkdir -p "$OUTPUT_DIR"

TAGS=()
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line//$'\r'/}"
  [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
  TAGS+=("$(trim "$line")")
done <"$TAGS_FILE"

if [[ ${#TAGS[@]} -eq 0 ]]; then
  echo "No consumer tags to process" >&2
  exit 1
fi

load_images
ensure_syft

echo "==> Supply chain for owner=${OWNER} images=${#IMAGES[@]} tags=${#TAGS[@]}"
echo "==> Output: ${OUTPUT_DIR}"
echo "==> Parallelism: ${SUPPLY_CHAIN_PARALLELISM}"
if [[ -n "$QUAY_NS" ]]; then
  echo "==> Also processing ${QUAY_REGISTRY}/${QUAY_NS}"
fi
if [[ "$SKIP_SIGN" -eq 1 ]]; then
  echo "==> Signing disabled (--skip-sign)"
fi

if [[ -n "$LIST_SUBJECTS" ]]; then
  rm -f "${LIST_SUBJECTS}.tmp"
  rm -rf "${LIST_SUBJECTS}.d"
  mkdir -p "${LIST_SUBJECTS}.d"
fi

tasks=()
for name in "${IMAGES[@]}"; do
  for tag in "${TAGS[@]}"; do
    tasks+=("supply:${name}:${tag}"$'\t'"process_image_tag"$'\t'"${name}"$'\t'"${tag}")
  done
done
run_parallel "${tasks[@]}"

if [[ -n "$LIST_SUBJECTS" ]]; then
  find "${LIST_SUBJECTS}.d" -maxdepth 1 -name '*.row' -exec cat {} + >"${LIST_SUBJECTS}.tmp"
  rm -rf "${LIST_SUBJECTS}.d"
  # shellcheck source=write-provenance-subjects.sh
  source "${SCRIPT_DIR}/write-provenance-subjects.sh"
  write_provenance_subjects "${LIST_SUBJECTS}"
fi

echo "==> Supply chain complete"
