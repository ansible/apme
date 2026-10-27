#!/usr/bin/env bash
set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${REPOSITORY:?REPOSITORY must be owner/repo}"
: "${EVENT_KIND:?EVENT_KIND must be push or release}"

if [[ "${EVENT_KIND}" != "push" && "${EVENT_KIND}" != "release" ]]; then
  echo "::error::Unsupported event kind: ${EVENT_KIND}"
  exit 1
fi

if [[ "${EVENT_KIND}" == "release" ]]; then
  RELEASE_TAG="${RELEASE_TAG:-}"
  if [[ ! "${RELEASE_TAG}" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
    echo "Skipping non-app release tag: ${RELEASE_TAG}"
    exit 0
  fi
fi

TAG="$(
  gh api --paginate "repos/${REPOSITORY}/releases?per_page=100" \
    --jq '.[] | select(.draft == false and .prerelease == false and (.tag_name | test("^v(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)\\.(0|[1-9][0-9]*)$"))) | .tag_name' \
    | LC_ALL=C sort -V \
    | tail -n 1
)"

if [[ -z "${TAG}" || ! "${TAG}" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  echo "::error::No stable vX.Y.Z APME release tag found"
  exit 1
fi

ACTION_CONTENT="$(gh api "repos/${REPOSITORY}/contents/action.yml?ref=${TAG}" --jq '.content' | base64 --decode)"
if ! grep -q '^  engine-address:' <<< "${ACTION_CONTENT}"; then
  echo "::error::${TAG} does not expose the engine-address action input"
  exit 1
fi

TARGET_SHA="$(gh api "repos/${REPOSITORY}/commits/${TAG}" --jq '.sha')"
REF_PATH="repos/${REPOSITORY}/git/ref/tags/v1"
CURRENT_SHA="$(gh api "${REF_PATH}" --jq '.object.sha' 2>/dev/null || true)"

if [[ "${CURRENT_SHA}" == "${TARGET_SHA}" ]]; then
  echo "v1 already points to ${TAG} (${TARGET_SHA})"
elif [[ -n "${CURRENT_SHA}" ]]; then
  gh api --method PATCH "repos/${REPOSITORY}/git/refs/tags/v1" \
    -f sha="${TARGET_SHA}" -F force=true >/dev/null
  echo "Updated v1 to ${TAG} (${TARGET_SHA})"
else
  gh api --method POST "repos/${REPOSITORY}/git/refs" \
    -f ref=refs/tags/v1 -f sha="${TARGET_SHA}" >/dev/null
  echo "Created v1 at ${TAG} (${TARGET_SHA})"
fi
