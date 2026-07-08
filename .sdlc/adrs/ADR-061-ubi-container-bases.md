# ADR-061: UBI10 Container Base Images

## Status

Accepted

## Date

2026-07-08

## Context

APME ships **12 container images** built from this repository (shared Python
base plus per-service Dockerfiles, UI, and bootc host image). Today those
images use heterogeneous upstream bases:

- **Python services** (10 images): `ghcr.io/astral-sh/uv:python3.12-bookworm-slim`
  (Debian Bookworm) via [`containers/base/Dockerfile`](../../containers/base/Dockerfile)
- **Gateway**: inherits the Debian base and installs `git` with `apt-get`
  (ADR-037)
- **UI**: `node:22-alpine` build stage + `nginx:1-alpine` serve stage
- **bootc host VM image**: `quay.io/centos-bootc/centos-bootc:stream10`

APME targets Red Hat ecosystems for production deployment (Helm on
OpenShift/Kubernetes per ADR-054, bootc VM images, Quay/GHCR publishing).
Using Universal Base Image (UBI) aligns application containers with RHEL 10 /
OpenShift platform expectations, simplifies security scanning and compliance
narratives, and avoids mixing Debian/Alpine package managers (`apt`, `apk`)
with the `dnf`/`microdnf` tooling used elsewhere in the stack.

### Decision Drivers

- **Platform alignment**: OpenShift and RHEL customers expect UBI-based
  application images.
- **Supply chain consistency**: One OS family (RHEL 10 / UBI10) for
  APME-built runtime images.
- **Multi-arch support**: UBI10 Application Stream images publish amd64 and
  arm64 manifests (required for developer laptops and CI).
- **Minimal scope change**: Service Dockerfiles inherit `apme-base` via
  `BASE_IMAGE`; only the shared base, gateway package install, UI, and bootc
  host need direct changes.
- **Existing pinning policy**: [`tests/test_ci_workflow_hygiene.py`](../../tests/test_ci_workflow_hygiene.py)
  requires pinned base image tags, not `:latest`.

### Constraints

- **Abbenay is external**: `ghcr.io/redhat-developer/abbenay` is pulled, not
  built here — out of scope for this ADR.
- **OPA and Gitleaks binaries**: Static Go binaries continue to be copied from
  upstream official images in multi-stage builds; only the Python runtime base
  changes (ADR-010).
- **Architectural invariants unchanged**: No change to gRPC topology, validator
  read-only semantics, or pod scaling model (ADR-001, ADR-009, ADR-012).
- **bootc is not a UBI app image**: RHEL image-mode OS bases (`rhel-bootc`)
  include kernel and boot infrastructure UBI application images omit; bootc
  migration is paired with but distinct from UBI10 app containers.

## Decision

**We will migrate all APME-built container images to Red Hat UBI10 Application
Stream bases, and the bootc host image to RHEL 10 bootc, implemented in three
sequential pull requests.**

### Application containers (UBI10)

| Image role | Target base |
|------------|-------------|
| Python runtime (`apme-base` + 10 services) | `registry.access.redhat.com/ubi10/python-312-minimal:<pin>` |
| UI build stage | `registry.access.redhat.com/ubi10/nodejs-22-minimal:<pin>` |
| UI serve stage | `registry.access.redhat.com/ubi10/nginx-126:<pin>` |

- Install **uv** by copying the static binary from a pinned
  `ghcr.io/astral-sh/uv:<version>` image (not the Debian-based
  `uv:python3.12-bookworm-slim` combined image).
- Preserve the existing **`/app/.venv`** contract and `PATH` used by Galaxy
  Proxy, Ansible prebuild, and service entrypoints.
- Replace Gateway `apt-get install git` with `microdnf install git`.
- Pin UBI tags explicitly (e.g. `10.2` or full build tag); document bump
  process in implementation PRs.

### bootc host (RHEL 10 image mode)

| Image role | Target base |
|------------|-------------|
| bootc VM OS | `registry.redhat.io/rhel10/rhel-bootc:<pin>` |

- Provide **`BOOTC_BASE_IMAGE` build-arg** defaulting to `rhel-bootc`.
- Document unauthenticated dev fallback:
  `quay.io/centos-bootc/centos-bootc:stream10` for contributors without
  `registry.redhat.io` credentials.

### Out of scope

- **Abbenay** (`ghcr.io/redhat-developer/abbenay`) — third-party; no change.
- **OPA/Gitleaks upstream copy stages** — retain pinned
  `openpolicyagent/opa` and `zricethezav/gitleaks` source images; final
  runtime layer inherits UBI10 via `apme-base`.

### Implementation sequence

| PR | Scope |
|----|-------|
| **PR 1** | This ADR only (decision record) |
| **PR 2** | `containers/base/Dockerfile`, `containers/gateway/Dockerfile`, Python-stack documentation |
| **PR 3** | `containers/ui/Dockerfile`, `deploy/bootc/Containerfile`, bootc docs, integration verification |

## Alternatives Considered

### Alternative 1: Keep Debian/Alpine bases

**Description**: Continue using `uv:python3.12-bookworm-slim`, Alpine Node/nginx,
and CentOS Stream bootc.

**Pros**:
- No migration effort
- Smaller image sizes (especially slim/alpine variants)
- Current builds already work

**Cons**:
- Misaligned with RHEL/OpenShift customer expectations
- Mixed package managers across the stack
- Harder to justify in enterprise security/compliance reviews

**Why not chosen**: Does not meet platform alignment goals.

### Alternative 2: UBI9 instead of UBI10

**Description**: Use `ubi9/python-312-minimal` and related UBI9 App Stream
images.

**Pros**:
- Broader current OCP 4.x / RHEL 9 adoption
- Mature image catalog

**Cons**:
- bootc reference already targets CentOS Stream 10 / RHEL 10
- UBI10 matches the forward-looking RHEL 10 product line

**Why not chosen**: UBI10 aligns with bootc stream10 and RHEL 10 image mode.

### Alternative 3: Single monolithic PR for all Dockerfile changes

**Description**: Change base, gateway, UI, and bootc in one PR.

**Pros**:
- No intermediate mixed-base state
- One review cycle

**Cons**:
- Large diff harder to review and bisect
- ADR decision separated from implementation anyway

**Why not chosen**: Three-PR sequence separates decision record from incremental
implementation (ADR → Python stack → UI/bootc).

## Consequences

### Positive

- APME-built images are UBI10/RHEL-ecosystem compliant after full rollout.
- Gateway uses `microdnf` consistent with UBI minimal images.
- CI and local builds continue via existing `BASE_IMAGE` build-arg pattern.
- Multi-arch (amd64 + arm64) supported by UBI10 App Stream manifests.

### Negative

- **Larger images** than Debian slim / Alpine (accepted tradeoff).
- **bootc production builds** require `registry.redhat.io` authentication.
- **Intermediate state after PR 2**: Python services on UBI10 while UI/bootc
  remain on old bases until PR 3 merges.
- **Non-root runtime (UID 1001)** on UBI Python images may require volume
  permission verification for `/sessions`, `/data`, `/cache`.

### Neutral

- OPA/Gitleaks final images change OS base but retain upstream binary copy
  pattern.
- Helm chart values unchanged; same image names and tags published to GHCR/Quay.
- Container CI workflow (`.github/workflows/container-images.yml`) needs no
  structural change.

## Implementation Notes

### Python base (`containers/base/Dockerfile`)

```dockerfile
ARG UBI_PYTHON_IMAGE=registry.access.redhat.com/ubi10/python-312-minimal:10.2
FROM ${UBI_PYTHON_IMAGE}
USER root
COPY --from=ghcr.io/astral-sh/uv:0.<pin> /uv /uvx /usr/local/bin/
WORKDIR /app
# ... existing uv sync layers; PATH=/app/.venv/bin:$PATH
```

- Use `USER root` for build-time `RUN` layers; avoid `USER root` in final
  runtime layers where possible.
- Keep `UV_CACHE_DIR=/tmp/uv-cache` (not `/root/.cache/uv`).

### Gateway

Replace `apt-get` with:

```dockerfile
RUN microdnf install -y git && microdnf clean all
```

### UI

- Build: `ubi10/nodejs-22-minimal` — `npm ci && npm run build`
- Serve: `ubi10/nginx-126` — install `gettext` for `envsubst` via `dnf` (full App Stream image, not minimal); retain existing
  nginx config template and entrypoint.
- Do not remove Helm emptyDir/initContainer nginx workarounds until OCP
  restricted SCC testing confirms they are unnecessary.

### bootc

```dockerfile
ARG BOOTC_BASE_IMAGE=registry.redhat.io/rhel10/rhel-bootc:10.2
FROM ${BOOTC_BASE_IMAGE}
```

### Verification (per PR)

- **PR 1**: `tox -e lint`
- **PR 2**: `tox -e build`, `tox -e up`, `tox -e cli -- check`, `tox -e unit`,
  plus a write-permission check for `/sessions`, `/data`, and `/cache` under
  the UBI default non-root UID (1001) so volume mounts work after rollout
- **PR 3**: above plus UI/API proxy smoke test, bootc build (authenticated +
  fallback arg), integration tests

### Documentation updates

| PR | Files |
|----|-------|
| PR 2 | `docs/guides/DEPLOYMENT.md`, `docs/architecture/17-scaling-and-deployment.md`, `.sdlc/context/architecture.md`, `SECURITY.md` |
| PR 3 | bootc README, remaining deployment/architecture references, `deploy/helm/apme/README.md` |

## Related Decisions

- [ADR-004](ADR-004-podman-pod-deployment.md): Podman pod local deployment
- [ADR-010](ADR-010-gitleaks-validator.md): Gitleaks multi-stage binary copy
- [ADR-037](ADR-037-project-centric-ui-model.md): Gateway requires `git`
- [ADR-054](ADR-054-production-deployment.md): Helm and bootc production paths

## References

- [Red Hat UBI10 python-312-minimal](https://catalog.redhat.com/en/software/containers/ubi10/python-312-minimal/677d315be3c3dff7ee21a2ee)
- [Using image mode for RHEL 10](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html/using_image_mode_for_rhel_to_build_deploy_and_manage_operating_systems/index)
- [uv Docker integration guide](https://docs.astral.sh/uv/guides/integration/docker/)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-07-08 | APME Team | Initial proposal and acceptance |
