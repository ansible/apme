# ADR-069: Helm Chart Simple All-in-One Topology (EAP / Upstream)

## Status

Accepted

## Date

2026-08-03

## Context

ADR-054 defined the Helm chart as a **split** Kubernetes topology: engine
Deployment (Primary + validators + Galaxy Proxy) separate from Gateway, UI, and
Abbenay Deployments. That shape assumed a production scale path where engines
horizontally scale behind a shared Gateway and optional AI daemon.

In practice the Helm chart’s audience is **EAP and upstream evaluation /
single-site installs**, not a multi-replica engine farm. The split topology
forces:

- Cross-pod Service DNS for Primary → Abbenay and Primary → Gateway reporting
- Abbenay C2 / DR-029 TLS or `--insecure` on non-loopback binds (APME #400,
  Abbenay #63 / #65, APME PR #492 CA Secret seeding)
- Chart and ops complexity (multiple Deployments, Services, NetworkPolicies,
  CA mounts) that does not buy a shipping scale story

Podman (`tox -e up`) and the local daemon (ADR-049 Gateway co-location) already
run Gateway (and Abbenay in the pod) on localhost. Helm diverged for a scale
model we are not delivering via this chart.

### Constraints and drivers

- Helm remains the **only** supported K8s/OpenShift install path (AGENTS.md
  invariant 16; ADR-054). This ADR changes **topology inside** that path, not
  the deployment method.
- ADR-012 still defines the conceptual scaling unit (engine stack). The chart
  simply does **not** offer multi-replica engine scaling while Gateway SQLite
  and Abbenay share the pod.
- ADR-005 localhost networking is the preferred intra-stack transport.
- Abbenay auth remains consumer tokens (`x-abbenay-token`); TLS peer trust is
  unnecessary when Abbenay binds loopback in the same pod.
- Portal vs standalone UI profiles (ADR-030 / `values-portal.yaml` /
  `values-standalone.yaml`) remain orthogonal value overlays, not separate
  topologies.

### Invariant consistency

| Invariant | Status |
|-----------|--------|
| 1. Validators read-only | Consistent |
| 2. gRPC between services | Consistent |
| 5. Stateless engine; persistence at Gateway | Consistent — Gateway still owns DB; co-located, not inverted |
| 6. Scale pods, not services | **Amends Helm packaging** — chart ships one pod including Gateway/UI/Abbenay; independent engine HPA is out of chart scope until a future topology ADR |
| 11. Engine emits only (reporting sink) | Consistent — sink targets `127.0.0.1:50060` |
| 12. Engine-core required services | Consistent |
| 16. Helm for K8s/OCP; Podman local | Consistent — Helm method unchanged |

This ADR **amends** ADR-054’s Helm workload topology and the Helm-specific
reading of ADR-012 / ADR-029 “independent Gateway scaling.” It does **not**
change Podman, bootc, or the daemon.

## Decision

**The APME Helm chart uses a Simple (all-in-one) topology: one Deployment whose
pod co-locates the engine stack, Gateway, UI, and optional Abbenay, communicating
over `127.0.0.1` (ADR-005).**

1. **Single workload** — Prefer one Deployment (name may remain `engine` or
   become `apme`; implementation detail). Containers: Primary, validators,
   Galaxy Proxy, Gateway, UI nginx, optional Abbenay (and optional OTel
   collector per ADR-067).
2. **Localhost addresses** — `APME_ABBENAY_ADDR`, reporting sink, and other
   in-stack clients use `127.0.0.1:<port>`, matching Podman. Abbenay binds
   `--grpc-host 127.0.0.1` (plaintext loopback; no `--grpc-tls` / CA Secret for
   the chart path).
3. **Single replica** — Chart defaults and validation: `replicas: 1`. HPA for
   this Deployment is disabled or rejected. Multi-replica requires a future ADR
   that reintroduces a split (or otherwise solves Gateway SQLite + session
   affinity).
4. **Services / Ingress** — Expose Gateway REST (and UI if standalone) via
   Service + Ingress as today; in-cluster clients that previously targeted
   `<release>-engine` / `<release>-abbenay` use the single pod Service or
   localhost from sidecars.
5. **UI profiles** — `values-portal.yaml` / `values-standalone.yaml` continue to
   toggle UI presence only (ADR-030).
6. **Documentation** — Brand the chart as **Simple / EAP / upstream** install;
   do not describe split Deployments as the Helm production topology.

**Amends:** [ADR-054](ADR-054-production-deployment.md) Helm workload topology.  
**Compatible with:** ADR-012 (conceptual engine unit; chart does not scale it
independently while co-located). ADR-029 Gateway role unchanged; independent
Gateway replica count is not offered by this chart.

## Alternatives Considered

### Alternative 1: Keep split Deployments (ADR-054 as written)

**Description**: Engine, Gateway, UI, Abbenay remain separate Deployments;
TLS/`--insecure` for Abbenay cross-pod.

**Pros**:
- Independent Gateway/engine scale if ever needed
- Failure isolation between AI daemon and engine

**Cons**:
- CA/TLS or plaintext Service complexity for EAP
- No shipping multi-replica story to justify the cost
- Diverges from Podman / daemon co-location UX

**Why not chosen**: Helm’s actual audience is EAP/upstream Simple installs.
Scale-out topology is speculative debt.

### Alternative 2: Optional `values-simple.yaml` beside split default

**Description**: Keep split as chart default; add an all-in-one profile.

**Pros**:
- Preserves ADR-054 default for hypothetical scale users

**Cons**:
- Two topologies to test and document
- Implies a “real” production path we do not ship
- EAP still pays for the wrong default

**Why not chosen**: Chart is only EAP/upstream — Simple should be **the**
topology, not an opt-in beside a phantom scale chart.

### Alternative 3: Helm Simple but Abbenay still separate Deployment

**Description**: Co-locate Gateway/UI with engine; leave Abbenay split.

**Pros**:
- Isolates LLM credentials / blast radius

**Cons**:
- Retains the Abbenay TLS/CA problem that motivates the change
- Partial simplification only

**Why not chosen**: Abbenay loopback co-location is the main operational win for
EAP AI remediation.

## Consequences

### Positive

- Helm matches Podman mental model (one pod, localhost).
- Eliminates Abbenay gRPC TLS/CA Secret seeding for the chart path (simplifies
  or largely retires Helm portions of APME #400 / PR #492).
- Fewer Services, probes, and NetworkPolicy edges.
- Clearer EAP install story: `helm install` → one pod.

### Negative

- No independent engine HPA while Gateway SQLite shares the pod.
- Larger scheduling footprint (one pod requests sum of all containers).
- Engine pod restart takes Gateway DB and Abbenay down together.
- Chart templates and tests must be reworked (breaking change vs current
  split chart for existing installs).

### Neutral

- bootc / Podman / CLI daemon paths unchanged in intent (already co-located).
- ADR-034 multi-pod Gateway registration remains future work if multi-engine
  returns.
- Portal vs standalone UI values files unchanged in purpose.

## Implementation Notes

- Collapse `gateway-deployment.yaml`, `ui-deployment.yaml`, and
  `abbenay-deployment.yaml` into the primary workload template (or equivalent
  single Deployment). Adjust Services accordingly.
- Set Abbenay `--grpc-host 127.0.0.1`; Primary `APME_ABBENAY_ADDR=127.0.0.1:50057`
  (or configured port). Drop Helm `abbenay.grpc.tls` / CA Secret requirements
  for the default path.
- Wire `APME_REPORTING_ENDPOINT=127.0.0.1:50060` (same as daemon / Podman).
- Fail Helm render if `replicas > 1` or HPA enabled for the Simple Deployment.
- Update `docs/guides/DEPLOYMENT.md`, chart README/NOTES, and
  `.sdlc/context/architecture.md` Scaling section.
- Align or simplify APME #400 / PR #492: keep client TLS factory for non-Helm
  remote Abbenay if needed; chart path is loopback plaintext.
- Follow-up: Abbenay #65 (cert reuse) remains useful for non-Simple remote
  clients, not required for this chart topology.

## Related Decisions

- [ADR-004](ADR-004-podman-pod-deployment.md) — Podman reference pod (same shape)
- [ADR-005](ADR-005-no-service-discovery.md) — localhost within pod
- [ADR-012](ADR-012-scale-pods-not-services.md) — engine stack as scale unit
- [ADR-029](ADR-029-web-gateway-architecture.md) — Gateway role (persistence/REST)
- [ADR-030](ADR-030-frontend-deployment-model.md) — portal vs standalone UI
- [ADR-049](ADR-049-gateway-in-daemon.md) — Gateway co-located in local daemon
- [ADR-054](ADR-054-production-deployment.md) — Helm + bootc (amended here)
- [ADR-067](ADR-067-otel-metrics-in-pod-collector.md) — in-pod OTel sidecar

## References

- Conversation decision: Helm chart is EAP/upstream only → Simple topology
- APME #400 / PR #492 — Abbenay gRPC TLS preparation (chart path simplified by this ADR)
- Abbenay DR-029 / #63 / #65 — non-loopback TLS policy and cert reuse

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-08-03 | APME Team | Accepted: Helm Simple all-in-one for EAP/upstream |
