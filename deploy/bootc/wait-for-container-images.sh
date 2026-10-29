#!/usr/bin/env bash
# Wait for the tag's multi-architecture manifests before resolving image digests.
set -euo pipefail

if [[ $# -ne 1 || ! "$1" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
  echo "Usage: $0 vX.Y.Z" >&2
  exit 2
fi
: "${GH_TOKEN:?GH_TOKEN must be set for GitHub Actions access}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must be set}"

tag="$1"
tag_sha="$(git rev-parse HEAD)"
# Container Images allows 120 minutes for each build stage and 30 for manifest
# merging; leave another 30 minutes for status polling and startup overhead.
deadline=$((SECONDS + 18000))

while (( SECONDS < deadline )); do
  runs="$(gh run list \
    --repo "${GITHUB_REPOSITORY}" \
    --workflow container-images.yml \
    --commit "${tag_sha}" \
    --event push \
    --json databaseId,headBranch,status,conclusion \
    --limit 100)"
  run="$(jq -c --arg tag "${tag}" 'map(select(.headBranch == $tag)) | first // empty' <<<"${runs}")"

  if [[ -n "${run}" ]]; then
    run_id="$(jq -r '.databaseId' <<<"${run}")"
    run_status="$(jq -r '.status' <<<"${run}")"
    run_conclusion="$(jq -r '.conclusion // ""' <<<"${run}")"
    merge_job="$(gh run view "${run_id}" \
      --repo "${GITHUB_REPOSITORY}" \
      --json jobs \
      --jq '.jobs[] | select(.name == "merge-manifests") | [.status, (.conclusion // "")] | @tsv' \
      || true)"

    if [[ -n "${merge_job}" ]]; then
      IFS=$'\t' read -r job_status job_conclusion <<<"${merge_job}"
      if [[ "${job_status}" == completed ]]; then
        if [[ "${job_conclusion}" == success ]]; then
          echo "Release image manifests are ready for ${tag}."
          exit 0
        fi
        echo "::error::Container Images merge-manifests job ended with ${job_conclusion:-no conclusion}."
        exit 1
      fi
    fi

    if [[ "${run_status}" == completed ]]; then
      echo "::error::Container Images run ended with ${run_conclusion:-no conclusion} before release manifests were ready."
      exit 1
    fi
  fi

  sleep 30
done

echo "::error::Timed out waiting for Container Images manifests for ${tag}."
exit 1
