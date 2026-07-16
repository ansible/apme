#!/usr/bin/env bash
# Publish Helm values profiles to the GitHub Pages chart repository root so
# operators can pass them with -f against https://ansible.github.io/apme/.
#
# Invoked via: tox -e helm-pages-profiles
#
# Default (local): copy profiles into dist/helm-pages/ for inspection.
# CI / publish:    HELM_PAGES_PUSH=1 pushes to the gh-pages branch.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHART_DIR="${ROOT}/deploy/helm/apme"
OUT_DIR="${ROOT}/dist/helm-pages"
PAGES_BRANCH="${HELM_PAGES_BRANCH:-gh-pages}"
REMOTE="${HELM_PAGES_REMOTE:-origin}"
PROFILES=(values-portal.yaml values-standalone.yaml)

for f in "${PROFILES[@]}"; do
  if [[ ! -f "${CHART_DIR}/${f}" ]]; then
    echo "error: missing ${CHART_DIR}/${f}" >&2
    exit 1
  fi
done

mkdir -p "${OUT_DIR}"
for f in "${PROFILES[@]}"; do
  cp "${CHART_DIR}/${f}" "${OUT_DIR}/${f}"
done
echo "==> staged profiles in ${OUT_DIR}"
ls -la "${OUT_DIR}"

if [[ "${HELM_PAGES_PUSH:-}" != "1" ]]; then
  echo "Dry-run only. Set HELM_PAGES_PUSH=1 to publish to ${REMOTE}/${PAGES_BRANCH}."
  echo "Target URLs (after publish):"
  for f in "${PROFILES[@]}"; do
    echo "  https://ansible.github.io/apme/${f}"
  done
  exit 0
fi

if ! git -C "${ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: ${ROOT} is not a git work tree" >&2
  exit 1
fi

# Ensure identity exists (CI configures this before calling tox).
if [[ -z "$(git -C "${ROOT}" config user.name || true)" ]]; then
  git -C "${ROOT}" config user.name "${GITHUB_ACTOR:-apme-bot}"
fi
if [[ -z "$(git -C "${ROOT}" config user.email || true)" ]]; then
  git -C "${ROOT}" config user.email "${GITHUB_ACTOR:-apme-bot}@users.noreply.github.com"
fi

git -C "${ROOT}" fetch "${REMOTE}" "${PAGES_BRANCH}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/apme-helm-pages.XXXXXX")"
cleanup() {
  git -C "${ROOT}" worktree remove --force "${WORK}" 2>/dev/null || rm -rf "${WORK}"
}
trap cleanup EXIT

git -C "${ROOT}" worktree add --detach "${WORK}" "${REMOTE}/${PAGES_BRANCH}"

for f in "${PROFILES[@]}"; do
  cp "${CHART_DIR}/${f}" "${WORK}/${f}"
done

git -C "${WORK}" add "${PROFILES[@]}"
if git -C "${WORK}" diff --cached --quiet; then
  echo "No changes to values profiles on ${PAGES_BRANCH}."
  exit 0
fi

git -C "${WORK}" commit -m "$(cat <<'EOF'
docs(helm): publish values profiles on chart repo

Ship values-portal.yaml and values-standalone.yaml next to index.yaml so
helm install can use -f https://ansible.github.io/apme/values-*.yaml.
EOF
)"

git -C "${WORK}" push "${REMOTE}" "HEAD:refs/heads/${PAGES_BRANCH}"

# Pages CDN can lag the git push; fail the release if URLs never become ready.
verify_pages_url() {
  local url="$1"
  local attempts="${HELM_PAGES_VERIFY_ATTEMPTS:-12}"
  local delay="${HELM_PAGES_VERIFY_DELAY_SECS:-5}"
  local i code
  for ((i = 1; i <= attempts; i++)); do
    code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 "${url}" || true)"
    if [[ "${code}" == "200" ]]; then
      echo "OK: ${url} → 200"
      return 0
    fi
    echo "waiting for ${url} (attempt ${i}/${attempts}, got ${code:-err})"
    sleep "${delay}"
  done
  echo "error: ${url} did not return HTTP 200 after ${attempts} attempts" >&2
  return 1
}

for f in "${PROFILES[@]}"; do
  verify_pages_url "https://ansible.github.io/apme/${f}"
done

echo "OK: published values profiles to ${REMOTE}/${PAGES_BRANCH}"
for f in "${PROFILES[@]}"; do
  echo "  https://ansible.github.io/apme/${f}"
done
