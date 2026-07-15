#!/usr/bin/env bash
# Merge per-arch APME image tags into multi-arch consumer tags (ADR-063).
#
# Expects arch-specific images already pushed to GHCR:
#   ghcr.io/<owner>/apme-<name>:<git-sha>-amd64
#   ghcr.io/<owner>/apme-<name>:<git-sha>-arm64
#
# Image names are read from containers/ci/images.txt (single source of truth).
#
# Usage:
#   merge-manifests.sh --owner ansible --sha "$GITHUB_SHA" --tags "sha-abc1234,latest"
#   merge-manifests.sh --owner ansible --sha "$GITHUB_SHA" --tags-file /tmp/tags.txt
#   merge-manifests.sh ... --quay-ns ansible   # also push final tags to quay.io
#   MERGE_PARALLELISM=6 merge-manifests.sh ... # concurrent imagetools (default 6)
#
# Locally runnable (lean CI): logic lives here, not only in workflow YAML.
set -euo pipefail

OWNER=""
SHA=""
TAGS_CSV=""
TAGS_FILE=""
QUAY_NS=""
GHCR_REGISTRY="${GHCR_REGISTRY:-ghcr.io}"
QUAY_REGISTRY="${QUAY_REGISTRY:-quay.io}"
ENGINE="${CONTAINER_ENGINE:-docker}"
# Cap concurrent registry ops to avoid rate limits while speeding merge.
MERGE_PARALLELISM="${MERGE_PARALLELISM:-6}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGES_FILE="${IMAGES_FILE:-${SCRIPT_DIR}/images.txt}"

usage() {
  sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
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

assert_multiarch() {
  local ref="$1"
  local raw arches
  raw="$("${ENGINE}" buildx imagetools inspect --raw "${ref}")"
  arches="$(printf '%s' "$raw" | jq -r '
    if .manifests then
      [.manifests[] | select(.platform.os == "linux") | .platform.architecture] | unique | .[]
    elif .architecture then
      .architecture
    else
      empty
    end
  ')"
  if ! grep -qx 'amd64' <<<"$arches"; then
    echo "ERROR: ${ref} missing linux/amd64 (arches: ${arches//$'\n'/ })" >&2
    return 1
  fi
  if ! grep -qx 'arm64' <<<"$arches"; then
    echo "ERROR: ${ref} missing linux/arm64 (arches: ${arches//$'\n'/ })" >&2
    return 1
  fi
  echo "OK multi-arch: ${ref}"
}

# Run up to MERGE_PARALLELISM background jobs; fail if any child fails.
run_parallel() {
  local -a pids=()
  local -a labels=()
  local fail=0
  local pid label done_label item cmd i

  if [[ $# -eq 0 ]]; then
    echo "run_parallel: no commands" >&2
    return 1
  fi

  # Each arg is: "label<TAB>command string"
  for item in "$@"; do
    label="${item%%$'\t'*}"
    cmd="${item#*$'\t'}"
    # Throttle: wait for a slot when at capacity.
    while [[ ${#pids[@]} -ge $MERGE_PARALLELISM ]]; do
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
      eval "$cmd"
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

preflight_one() {
  local name="$1"
  local src_amd src_arm
  src_amd="${GHCR_REGISTRY}/${OWNER}/apme-${name}:${SHA}-amd64"
  src_arm="${GHCR_REGISTRY}/${OWNER}/apme-${name}:${SHA}-arm64"
  "${ENGINE}" buildx imagetools inspect "${src_amd}" >/dev/null
  "${ENGINE}" buildx imagetools inspect "${src_arm}" >/dev/null
  echo "OK sources: apme-${name}"
}

preflight_sources() {
  local name
  local -a tasks=()
  echo "==> Preflight: verifying per-arch sources exist (parallelism=${MERGE_PARALLELISM})"
  for name in "${IMAGES[@]}"; do
    tasks+=("preflight:${name}"$'\t'"preflight_one $(printf '%q' "$name")")
  done
  run_parallel "${tasks[@]}"
}

merge_one_image() {
  local name="$1"
  shift
  local -a tags=("$@")
  local tag src_amd src_arm dest
  local -a tag_args=()

  src_amd="${GHCR_REGISTRY}/${OWNER}/apme-${name}:${SHA}-amd64"
  src_arm="${GHCR_REGISTRY}/${OWNER}/apme-${name}:${SHA}-arm64"
  for tag in "${tags[@]}"; do
    tag="$(trim "$tag")"
    [[ -z "$tag" ]] && continue
    tag_args+=(-t "${GHCR_REGISTRY}/${OWNER}/apme-${name}:${tag}")
    if [[ -n "$QUAY_NS" ]]; then
      tag_args+=(-t "${QUAY_REGISTRY}/${QUAY_NS}/apme-${name}:${tag}")
    fi
  done
  if [[ ${#tag_args[@]} -eq 0 ]]; then
    echo "No tags left after trimming for ${name}" >&2
    return 1
  fi
  echo "==> imagetools create apme-${name} tags=${tags[*]}"
  "${ENGINE}" buildx imagetools create "${tag_args[@]}" "${src_amd}" "${src_arm}"
  dest="${GHCR_REGISTRY}/${OWNER}/apme-${name}:$(trim "${tags[0]}")"
  assert_multiarch "${dest}"
}

merge_tags_for_all_images() {
  local -a tags=("$@")
  local name
  local -a tasks=()
  local tag_q name_q

  # Quote tag list once for embedding in eval'd child commands.
  tag_q=""
  for name in "${tags[@]}"; do
    tag_q+=" $(printf '%q' "$name")"
  done

  for name in "${IMAGES[@]}"; do
    name_q=$(printf '%q' "$name")
    tasks+=("merge:${name}"$'\t'"merge_one_image ${name_q}${tag_q}")
  done
  run_parallel "${tasks[@]}"
}

# Subshells from run_parallel inherit functions defined above (no export -f).

while [[ $# -gt 0 ]]; do
  case "$1" in
    --owner)
      OWNER="${2:-}"
      shift 2
      ;;
    --sha)
      SHA="${2:-}"
      shift 2
      ;;
    --tags)
      TAGS_CSV="${2:-}"
      shift 2
      ;;
    --tags-file)
      TAGS_FILE="${2:-}"
      shift 2
      ;;
    --quay-ns)
      QUAY_NS="${2:-}"
      shift 2
      ;;
    --parallelism)
      MERGE_PARALLELISM="${2:-}"
      shift 2
      ;;
    -h | --help)
      usage
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      ;;
  esac
done

if [[ -z "$OWNER" || -z "$SHA" ]]; then
  echo "--owner and --sha are required" >&2
  usage
fi

if ! [[ "$MERGE_PARALLELISM" =~ ^[1-9][0-9]*$ ]]; then
  echo "MERGE_PARALLELISM must be a positive integer (got: ${MERGE_PARALLELISM})" >&2
  exit 1
fi

TAGS=()
if [[ -n "$TAGS_FILE" ]]; then
  if [[ ! -f "$TAGS_FILE" ]]; then
    echo "Tags file not found: $TAGS_FILE" >&2
    exit 1
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line//$'\r'/}"
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    TAGS+=("$(trim "$line")")
  done <"$TAGS_FILE"
elif [[ -n "$TAGS_CSV" ]]; then
  IFS=',' read -r -a TAGS <<<"$TAGS_CSV"
else
  echo "Provide --tags or --tags-file" >&2
  usage
fi

# Drop empties after trim.
_filtered=()
for tag in "${TAGS[@]}"; do
  tag="$(trim "$tag")"
  [[ -n "$tag" ]] && _filtered+=("$tag")
done
TAGS=("${_filtered[@]}")

if [[ ${#TAGS[@]} -eq 0 ]]; then
  echo "No consumer tags to publish" >&2
  exit 1
fi

load_images

echo "==> Merging multi-arch manifests for owner=${OWNER} sha=${SHA}"
echo "==> Images (${#IMAGES[@]}): ${IMAGES[*]}"
echo "==> Consumer tags: ${TAGS[*]}"
echo "==> Parallelism: ${MERGE_PARALLELISM}"
if [[ -n "$QUAY_NS" ]]; then
  echo "==> Also publishing to ${QUAY_REGISTRY}/${QUAY_NS}"
fi

preflight_sources

# Phase 1: immutable sha-* tags for every image (consistent set before floating tags).
SHA_TAGS=()
FLOAT_TAGS=()
for tag in "${TAGS[@]}"; do
  if [[ "$tag" == sha-* ]]; then
    SHA_TAGS+=("$tag")
  else
    FLOAT_TAGS+=("$tag")
  fi
done

if [[ ${#SHA_TAGS[@]} -eq 0 ]]; then
  echo "ERROR: expected at least one sha-* consumer tag in: ${TAGS[*]}" >&2
  exit 1
fi

echo "==> Phase 1: immutable tags (${SHA_TAGS[*]})"
merge_tags_for_all_images "${SHA_TAGS[@]}"

if [[ ${#FLOAT_TAGS[@]} -gt 0 ]]; then
  echo "==> Phase 2: floating tags (${FLOAT_TAGS[*]})"
  merge_tags_for_all_images "${FLOAT_TAGS[@]}"
fi

echo "==> Multi-arch merge complete"
