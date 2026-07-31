# ADR-063: Multi-Platform Container Image Publish

## Status

Implemented

## Date

2026-07-15

## Context

APME publishes container images to GHCR (and optionally Quay) for Helm,
bootc, and external consumers. CI historically built on a single
`ubuntu-latest` (amd64) runner with Docker Buildx and **no** `platforms`
matrix, so published tags were effectively **linux/amd64 only**.

Early-access and demo users frequently run on **Apple Silicon** laptops and
**arm64** Kubernetes/OpenShift nodes. Requiring those users to rebuild all
images locally is a high-friction mismatch with "meet users where they are."

[ADR-061](ADR-061-ubi-container-bases.md) already selected UBI10 Application
Stream bases partly because they publish amd64 and arm64 manifests. That made
the **bases** multi-arch ready; it did **not** make APME's **publish pipeline**
a contract. Without an explicit decision, multi-arch support can regress when
CI is "simplified."

Abbenay (`ghcr.io/redhat-developer/abbenay`) already ships multi-arch images.
OPA and Gitleaks upstream images used in multi-stage `COPY --from` builds are
also multi-arch. The gap is APME-built images only.

### Decision Drivers

- **Meet users where they are**: amd64 servers and arm64 developer/demo clusters
  must pull the same Helm tags.
- **Stable consumer contract**: same image names and tags; nodes select the
  matching platform from a manifest list (no chart or values change).
- **Reliable CI**: prefer native per-arch builds over QEMU cross-compilation for
  heavy images (`apme-base` `uv sync`, `apme-ansible` prewarm).
- **Org precedent**: [ansible-dev-tools](https://github.com/ansible/ansible-dev-tools)
  builds amd64 and arm64 on native runners, then merges manifests.
- **Lock the requirement**: document the platforms so "drop arm to save CI
  minutes" requires a superseding ADR.

### Constraints

- **Registries stay the same**: `ghcr.io/<owner>/apme-*` and optional
  `quay.io/<ns>/apme-*`.
- **Helm remains tag-based** (ADR-054); no digest pinning required for this ADR.
- **Local `tox -e build`** builds the host architecture only (developer laptop
  path). CI is the multi-arch guarantee.
- **bootc VM images** are out of scope (separate host/arch concern).
- **Lean CI** (ADR-047): merge/publish logic lives in a repo script callable
  from the workflow, not only in opaque YAML.

## Decision

**Published APME application images MUST be multi-platform manifest lists for
`linux/amd64` and `linux/arm64` under the existing GHCR/Quay image names and
consumer tags.**

Build method:

1. **Native per-arch CI builds** on GitHub-hosted runners (`ubuntu-24.04` for
   amd64, `ubuntu-24.04-arm` for arm64).
2. Push **arch-suffixed intermediate tags** into the **same** final image
   repositories (e.g. `apme-gateway:<git-sha>-amd64` /
   `apme-gateway:<git-sha>-arm64`) so GHCR blob mounting works when merging.
3. A **merge job** creates consumer tags (`sha-*`, semver, `latest` per existing
   metadata rules) via `docker buildx imagetools create`, with a **preflight**
   that all per-arch sources exist, **phase-1 immutable `sha-*` tags** for the
   full image set, then **phase-2 floating tags**, and a **hard assert** that
   each published consumer tag lists both `linux/amd64` and `linux/arm64`.
4. Optional Quay publish receives the **final** multi-arch tags from the merge
   job (sources may remain on GHCR). Quay is enabled only when both username
   and password secrets are set.
5. Image inventory is **`containers/ci/images.txt`** — single source of truth
   for the workflow service matrix and the merge script.

Removing either platform from published images requires a superseding ADR.

## Alternatives Considered

### Alternative 1: QEMU / single-runner `platforms: linux/amd64,linux/arm64`

**Description**: Keep one `ubuntu-latest` job and ask Buildx to cross-build
arm64 under QEMU.

**Pros**:
- Smaller workflow matrix
- Familiar Buildx one-liner

**Cons**:
- Slow and flaky for `apme-base` and `apme-ansible`
- Harder to debug arch-specific failures

**Why not chosen**: ansible-dev-tools and our own arm rebuild experience favor
native builds for reliability.

### Alternative 2: Separate `-arm64` image names or a second chart

**Description**: Publish `apme-gateway-arm64` (or a second Helm chart) instead
of multi-arch tags.

**Pros**:
- Simple single-arch tags per name

**Cons**:
- Shifts arch selection onto every consumer
- Helm values / docs fork forever
- Violates "same tags, meet users where they are"

**Why not chosen**: Manifest lists keep the Helm contract unchanged.

### Alternative 3: amd64-only publish; document local rebuild for arm

**Description**: Keep current CI; tell arm users to `tox -e build`.

**Pros**:
- Lowest CI cost

**Cons**:
- Blocks Helm-from-registry demos on arm64
- High friction for EAP / Apple Silicon users

**Why not chosen**: Unacceptable for the supported deployment story.

## Consequences

### Positive

- `helm install` with a published tag works on amd64 and arm64 nodes.
- Apple Silicon and arm64 cluster demos no longer require a full local rebuild.
- ADR-061's multi-arch base choice is completed by a publish contract.
- Intentional removal of a platform requires an explicit ADR change.

### Negative

- CI cost and wall-clock time increase (roughly ~2× image build capacity;
  arm runners may queue).
- Merge job adds a failure mode if one arch leg fails.
- Intermediate arch-suffixed tags accumulate in the registry (acceptable;
  GC/package retention can age them out).

### Neutral

- Local `tox -e build` / Podman remains single-arch (host native).
- Abbenay and bootc unchanged by this ADR.
- Chart `values.yaml` image names and tag semantics unchanged.

## Implementation Notes

- Workflow: [`.github/workflows/container-images.yml`](../../.github/workflows/container-images.yml)
- Merge script: [`containers/ci/merge-manifests.sh`](../../containers/ci/merge-manifests.sh)
  (per-image `imagetools` work is parallelized via `MERGE_PARALLELISM`, default
  6; phase-1 `sha-*` still completes before phase-2 floating tags)
- Service builds for a given arch must `FROM` that arch's `apme-base:<sha>-<arch>`
  tag (never an unfinished multi-arch `latest` mid-pipeline).
- Prefer `docker buildx imagetools create` over raw `docker manifest create`
  (matches ansible-dev-tools `merge-release` action).
- Verification: merge CI fails unless each published consumer tag lists both
  `linux/amd64` and `linux/arm64`. After the first post-merge release, confirm
  with `docker buildx imagetools inspect` on arm and amd64 pull/smoke.
- Tags published **before** this ADR remain single-arch until rebuilt; chart
  defaults and older release tags are not retroactively multi-arch.
- Pin Actions to commit SHAs with `# vN` comments (ADR-015).

## Related Decisions

- [ADR-015](ADR-015-github-actions-prek.md): Action pinning / CI hygiene
- [ADR-047](ADR-047-tox-developer-orchestration.md): tox / lean CI orchestration
- [ADR-054](ADR-054-production-deployment.md): Helm / bootc production paths
- [ADR-061](ADR-061-ubi-container-bases.md): UBI10 bases (multi-arch capable)

## References

- [ansible-dev-tools `tools/ee.sh`](https://github.com/ansible/ansible-dev-tools/blob/main/tools/ee.sh) — native per-arch build + manifest merge
- [ansible-dev-tools merge-release action](https://github.com/ansible/ansible-dev-tools/blob/main/.github/actions/merge-release/action.yml) — `imagetools create`
- [Docker Buildx imagetools](https://docs.docker.com/reference/cli/docker/buildx/imagetools/)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-07-15 | APME Team | Initial acceptance — multi-platform publish contract |
