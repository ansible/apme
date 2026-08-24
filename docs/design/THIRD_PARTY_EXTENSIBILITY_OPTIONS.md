# Options discussion: third-party extensibility and overall tool use

This document is a **decision-support briefing** grounded in the current
repository. It complements [ADR-042: Third-Party Plugin Services](../../.sdlc/adrs/ADR-042-third-party-plugin-services.md), which remains **Proposed**; there is **no** `plugin.proto` or `APME_PLUGIN_*` integration in code yet.

---

## How the tool works today (what users actually touch)

**Key user paths**

- **`apme check`** — Streams project files over **`FixSession`** to Engine; receives **remaining violations** and "what-would-change" **patch diffs** (readable via `--diff` / JSON `diffs`), but does **not** write changes back to disk. See [`src/apme_engine/cli/check.py`](../../src/apme_engine/cli/check.py).
- **`apme remediate`** — Same **`FixSession`** stream with **`FixOptions`** (passes, AI, etc.); Tier 1 fixes, optional proposals, writes back. See [`src/apme_engine/cli/remediate.py`](../../src/apme_engine/cli/remediate.py).
- **Hosted CI** — [`action.yml`](../../action.yml) installs the CLI and talks to an **`engine-address`**; no local pod in the workflow unless you bring one. Extensibility on the engine side **must exist on that hosted Engine** for CI to see it.

**What Engine does**

- Parses content, builds **hierarchy** (and related payloads), then **fan-out** to validators via `asyncio.gather()` — see [`_scan_pipeline`](../../src/apme_engine/daemon/engine_server.py).
- Validators implement **`Validator.Validate` + `Health` only** — [`proto/apme/v1/validate.proto`](../../proto/apme/v1/validate.proto). Built-in validators are **read-only** (ADR-009).
- **Tier 1 remediation** is **in-process**: [`partition.py`](../../src/apme_engine/remediation/partition.py) routes to **`TransformRegistry`** by `rule_id` (after `normalize_rule_id`). There is **no** `EXT-` or plugin routing today.

**Local daemon**

- [`launcher.py`](../../src/apme_engine/daemon/launcher.py) sets env vars (`NATIVE_GRPC_ADDRESS`, etc.) for known services. Plugins would need an analogous **discovery and startup** story for `apme daemon` / pod (ADR-042 sketches `APME_PLUGIN_<NAME>_ADDRESS`).

```mermaid
flowchart LR
  subgraph clients [Clients]
    CLI[apme_check_remediate]
    GA[GitHub_Action]
  end
  subgraph core [Engine_core_daemon_and_pod]
    E[Engine]
    V1[Native]
    V2[OPA]
    V3[Ansible]
    GP[Galaxy_Proxy :8765]
  end
  subgraph optionalPod [Optional_validators_pod_only_by_default]
    V4[Gitleaks]
    V5[CollectionHealth]
    V6[DepAudit]
  end
  CLI -->|FixSession_gRPC| E
  GA -->|FixSession_gRPC| E
  E -->|Validate_parallel| V1
  E --> V2
  E --> V3
  E -->|PEP503_HTTP| GP
  E -.->|pod_or_include_optional| V4
  E -.->|pod_or_include_optional| V5
  E -.->|pod_or_include_optional| V6
  E -->|Tier1_in_process| TR[TransformRegistry]
```

CLI daemon mode starts only the core stack (Engine, Native, OPA, Ansible, Galaxy
Proxy). Gitleaks, Collection Health, and Dep Audit require the Podman/Helm pod
or `include_optional=True` on the daemon.

### Target state after ADR-042 (sketch)

If [ADR-042](../../.sdlc/adrs/ADR-042-third-party-plugin-services.md) is implemented, **Plugin** containers join the same **Validate** fan-out as built-in validators. **Tier 1** for built-in rule IDs stays **in-process** (`TransformRegistry`); violations with an **`EXT-`** prefix are routed back to the **originating plugin’s `Transform`** during remediation (same convergence loop: scan → fix → rescan).

```mermaid
flowchart LR
  subgraph clients [Clients]
    CLI[apme_check_remediate]
  end
  E[Engine]
  subgraph builtIn [BuiltInValidators]
    V1[Native]
    V2[OPA]
    V3[Ansible]
    V4[Gitleaks]
  end
  PL[Plugin_gRPC]
  TR[TransformRegistry]
  CLI -->|FixSession| E
  E -->|Validate_parallel| V1
  E --> V2
  E --> V3
  E --> V4
  E -->|Validate_parallel| PL
  E -->|Tier1_builtin_rules| TR
  E -->|Transform_EXT_rules| PL
```

### ADR-042 implementation phases vs user-visible outcomes

Phases follow [ADR-042 Implementation Notes](../../.sdlc/adrs/ADR-042-third-party-plugin-services.md#implementation-notes).

| Phase | What ships (summary) | User-visible outcome |
|-------|----------------------|----------------------|
| **1** | `plugin.proto`, `apme_plugin_sdk`, shared codegen, example plugin | Stable **contract** for authors; runnable **reference plugin** (not yet wired into product Engine). |
| **2** | `APME_PLUGIN_*_ADDRESS` discovery, `Describe`, **Validate** fan-out on Engine | **`apme check`** (and hosted scans) can report **`EXT-`** violations when the deployment includes plugins. |
| **3** | `partition.py` / routing, plugin **`Transform`** in convergence loop | **`apme remediate`** applies **Tier 1** fixes for plugin-owned rules via gRPC, not only built-in transforms. |
| **4** | Tier 2 partitioned by plugin; **`ai_guidance`** in prompts | Failed or missing plugin transforms escalate to **AI** with plugin-supplied context (per ADR-025 stack). |
| **5** | PyPI SDK, template project, **versioned hierarchy JSON** doc, authoring guide | **Production onboarding** for third parties without copying the monorepo. |

**Smallest slice that proves end-to-end product value:** Phases **2 + 3** (findings + deterministic fixes). Phase **1** alone is a valid **spike** but does not change the shipped CLI until Engine integration lands.

---

## Option 1: Implement ADR-042 as written (Plugin gRPC service)

**Idea:** One container per plugin: **`Validate` + `Transform` + `Describe` + `Health`**, `EXT-` rule IDs, env-based discovery, remediation routes `Transform` back to the owning plugin; Tier 2 AI **batched per plugin** with `metadata["ai_guidance"]`.

**Effect on tool use**

| Audience | Impact |
|----------|--------|
| **End users** | Same CLI; more findings possible from org plugins; **`EXT-...`** in output/SARIF/UI; remediate may apply **third-party** transforms (trust + review matter). |
| **Platform ops** | More containers, ports, env vars, health checks; hosted deployments must **register** plugin addresses on Engine. |
| **CI (hosted action)** | Plugins only run if the **remote Engine** is configured with them—**not** something the Action can turn on by itself without deployment support. |
| **Plugin authors** | Ship a container + (eventually) SDK; work off **`files` + `hierarchy_payload`**, not `scandata` (ADR-042 contract). |
| **Performance** | Validation fan-out stays parallel; **per-violation plugin `Transform`** is slower than in-proc Tier 1. |
| **Security / governance** | Clear isolation vs loading Python into Native; org accepts **write path** in plugin for remediate. |

**Dependency chain:** proto + SDK → Engine fan-out + Describe map → remediation routing → AI partitioning + `violation_convert` allowlist for `ai_guidance` (called out explicitly in ADR-042).

---

## Option 2: “Validation-only extensibility” without full ADR-042

**Idea:** Add only **custom detection** (e.g. another `Validator` implementation or a read-only sidecar) **without** a first-class plugin transform path.

**Effect on tool use**

- **`apme check`** and SARIF gain new rules if wired like other validators.
- **`apme remediate`** still only fixes **built-in** `TransformRegistry` (and AI tiers)—org-specific findings stay **manual or AI-only** unless you duplicate routing logic elsewhere.
- **Architectural tension:** [`AGENTS.md`](../../AGENTS.md) / ADR-042 narrative pushes **closed built-in bundles** and **plugins for custom policy**; a one-off “extra validator” without an ADR update risks drifting from that story.

Useful when the **only** goal is gates in CI, not automated fixes aligned with those gates.

---

## Option 3: No engine plugins — policy outside APME

**Idea:** Keep APME for Ansible-core compatibility + built-in catalog; enforce org naming/tags/compliance with **Ansible Lint custom rules**, **CI shell checks**, **OPA/conftest on YAML**, or **Gateway** policy if using enterprise UI.

**Effect on tool use**

- **Simpler ops** (single APME image/version).
- **Two tools / two reports** unless you merge results yourself; **no unified `FixSession`** convergence for org rules.
- Best when org policy changes often and teams already own another policy engine.

---

## Option 4: Contribute rules upstream to built-in validators

**Idea:** General-purpose rules land in Native/OPA/Ansible bundles; **no** third-party container.

**Effect on tool use**

- **Best UX** for everyone: one binary, one rule catalog, in-proc Tier 1 where transforms exist.
- **Slow / impossible** for proprietary org logic you cannot publish.

---

## Cross-cutting concerns (any plugin path)

1. **`partition.py` / Tier routing** — Today unknown IDs follow scope-based Tier 2/3 logic. Plugins need explicit rules: **Tier 1 = plugin `Transform` success**; failures → ADR-042’s **transform failed → AI candidate** behavior.
2. **Violation metadata** — SARIF, UI, and Gateway reporting should carry **origin** (`EXT-` prefix / plugin name) for attribution.
3. **Hierarchy JSON as public API** — ADR-042 flags **versioning** responsibility; breaking hierarchy shape breaks plugins silently or loudly depending on discipline.
4. **ADR status** — Treating ADR-042 as **Accepted** before implementation aligns SDLC with [`AGENTS.md`](../../AGENTS.md) invariants (built-in closed, plugins separate).

---

## Related ADRs and tensions

### ADR-041 (rule catalog and Gateway authority)

[ADR-041: Rule Catalog & Override Architecture](../../.sdlc/adrs/ADR-041-rule-catalog-override-architecture.md) already handles **built-in rule catalog registration** (authority Engine pushes to the Gateway Reporting sink at startup). The open question is **`EXT-*` plugin rule registration** — how plugin rules are cataloged alongside built-in ones — and **multi-pod** deployments where **plugin sets differ** (e.g. one Engine has `secteam`, another does not). That directly affects:

- **Hosted CI:** the catalog visible to the Gateway must match what the **scanning** Engine can execute, or scans misrepresent available rules.
- **Enterprise UX:** rule authority (`APME_RULE_AUTHORITY` and related ideas in ADR-041) must be consistent with **which** plugins are installed on the registering Engine.

ADR-042’s “point Engine at plugin addresses” story should be read **together** with ADR-041 whenever you standardize **catalog registration** and overrides.

### ADR-043 (default severity) and `Describe`

[ADR-043](../../.sdlc/adrs/ADR-043-default-severity-assignment.md) already anticipates plugin **`Describe`** RPC supplying **`default_severity`** for **`EXT-`** rules. Reporting, SARIF, and future overrides depend on that metadata being stable and honest.

### ADR-008, ADR-025, ADR-026 (short)

- **[ADR-008](../../.sdlc/adrs/ADR-008-rule-id-conventions.md)** — `EXT-` prefix convention for plugin rule IDs.
- **[ADR-025](../../.sdlc/adrs/ADR-025-ai-provider-protocol.md)** — Tier 2 AI path plugin violations reuse after **partitioning** (ADR-042 Phase 4).
- **[ADR-026](../../.sdlc/adrs/ADR-026-rule-scope-metadata.md)** — Plugins should set **`scope`** on violations so Tier 2 vs Tier 3 routing stays coherent with built-in rules.

---

## Implementation checkpoint: `ai_guidance` and violation metadata

ADR-042 requires preserving **`metadata["ai_guidance"]`** through proto ↔ dict conversion for Tier 2 escalation. Today, [`violation_convert.py`](../../src/apme_engine/daemon/violation_convert.py) uses an explicit **`_METADATA_KEYS`** allowlist for fields copied onto violations; **`ai_guidance` is not listed**, so plugin guidance would be **dropped** unless that list (or the conversion rules for **`EXT-`** violations) is updated when Phase **4** is implemented—ideally alongside or before first plugin AI paths ship.

---

## Open questions (DR / REQ candidates)

These are suitable for a **Decision Request** or the preamble of a **Requirement** when you commit to ADR-042 delivery:

1. **Fleet consistency** — Must every replica ship the **same** plugin set, or is **partial rollout** (different `EXT-` availability per Engine) an explicit, supported mode? (Touches ADR-041 authority model.)
2. **GitHub Action vs hosted-only plugins** — Should [`action.yml`](../../action.yml) document a **supported** pattern (e.g. optional plugin sidecars to a local Engine), or is **hosted Engine configuration** the only supported path for `EXT-` in CI?
3. **Hierarchy JSON contract** — Where does the **versioned** public schema live, what is the **SemVer** policy when the engine changes hierarchy shape, and how do plugins declare **minimum engine version**?
4. **Trust and signing** — Are plugin images **org-built only**, or is there a future need for **signature / provenance** policy before `Transform` is allowed in regulated environments?

---

## SDLC follow-up (when moving from briefing to delivery)

| Step | Notes |
|------|--------|
| **Accept or revise ADR-042** | Moves status beyond **Proposed**; clarifies exemption to ADR-009 for the `Plugin` service only. |
| **Author REQ** (e.g. `/req-new`) | “Third-party plugin services” with acceptance criteria aligned at minimum to **Phases 2–3** for MVP, **Phase 4** if AI for `EXT-` is in scope. |
| **Break into TASKs** | Typical split: proto + SDK + codegen TASK; Engine fan-out TASK; remediation routing TASK; AI partition + `violation_convert` TASK; DX / template TASK. |
| **Verification** | Per [AGENTS.md](../../AGENTS.md): **`tox -e lint`**, **`tox -e unit`** (or **`uv run tox`** after `uv sync --extra dev`) on each mergeable slice. |

No REQ/TASK files are created by this design doc alone—use the project SDLC templates when you are ready to schedule work.

---

## Suggested conversation forks (for you to choose next)

- **Product:** “We need org gates in **hosted CI**” → stresses **deployment + Engine config**, not just SDK.
- **Product:** “We need **auto-fix** for our rules” → pushes toward **full ADR-042** (or upstream transforms).
- **Engineering:** “We want smallest slice” → **Phase 1** in ADR-042 (proto + SDK + example) **without** full remediate routing is a possible spike—but **end-to-end tool value** needs Phase 2–3.

Reply with your primary constraint (**CI vs local**, **check-only vs remediate**, **hosted vs pod**) to narrow a concrete recommendation or a scoped REQ/TASK set.
