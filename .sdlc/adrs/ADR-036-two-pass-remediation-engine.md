# ADR-036: Two-Pass Remediation Engine with Project-Level Transforms

## Status

Proposed

## Date

2026-03-23

## Context

The APME remediation engine (ADR-009) currently operates in a single pass
at the **unit level**: individual tasks are extracted as `FixableUnit`s,
violations are partitioned into Tier 1 (deterministic transforms) and
Tier 2 (AI proposals), and patches are applied per-file within a
convergence loop.

This architecture works well for task-scoped rules (FQCN fixes, module
parameter updates, deprecated syntax) but cannot address violations that
require **cross-file context or project-wide visibility**:

| Violation type | Why unit-level fails |
|----------------|---------------------|
| SEC:* (hardcoded secrets) | Remediation creates new files (`secrets.yml`) and modifies `vars_files:` references across playbooks |
| R111/R112 (parameterized imports) | Needs role/taskfile inventory across the project |
| L042 (high task count) | Requires restructuring entire plays, possibly into roles |
| Common vars across files | Deduplication requires seeing all files simultaneously |

Currently these violations route to **Tier 3 (manual review)** because
`partition_violations()` drops them there:

- SEC violations have `scope="playbook"`, which is not in
  `AI_PROPOSABLE_SCOPES` (`task`, `block`) — falls to Tier 3
- R111/R112 are in `CROSS_FILE_RULES` — explicitly routed to Tier 3

Some of these (notably secret externalization, per ADR-035) are
**deterministic** transforms that don't need AI — they just need to see
the whole project.

### Concurrent work

ADR-027 proposes an **agentic** project-level tier using MCP tools and
LLM agents for complex cross-file fixes. That tier handles violations
that require judgment. This ADR addresses the **deterministic** subset
of project-level transforms that can run without AI, as a natural
complement.

## Decision

**Extend the remediation engine to run in two passes:**

### Pass 1: Unit-Level (current behavior)

Operates on individual tasks/blocks within files:

- **Tier 1**: Deterministic unit transforms (FQCN, formatting, deprecated
  params) via `TransformRegistry`
- **Tier 2**: AI unit transforms (task-scoped LLM proposals) via
  `AIProvider`

This pass is unchanged. After convergence, remaining violations are
classified: those eligible for project-level deterministic transforms
route to Pass 2 instead of falling to manual review.

### Pass 2: Project-Level (new)

Operates on the entire project simultaneously:

- **Project Tier 1**: Deterministic project transforms that see all files.
  Secret externalization (ADR-035) is the first. Concrete candidates
  grounded in existing rules:

  | Transform | Rules | What it does |
  |-----------|-------|-------------|
  | Secret externalization | SEC:* (playbook) | Replace hardcoded secrets with `{{ vault_var }}` references, insert `vars_files:`, emit vault setup next-steps |
  | Collection dependency generation | L037, R501 (collection) | Generate/update `collections/requirements.yml` from unresolved FQCN usage; the engine already has FQCN-to-collection mappings from ADR-032 |
  | Variable rename refactoring | L050 (inventory) | Rename convention-violating variables (`myDBPassword` → `my_db_password`) across all definition and usage sites: `vars:` blocks, `set_fact:`, `register:`, Jinja2 expressions, `group_vars/`, `host_vars/`, role defaults, `when:` conditionals; emit next-steps for external references (Tower surveys, inventory scripts) |
  | Python interpreter cleanup | M010 (play) | Remove or update `ansible_python_interpreter` to `auto` in play vars and `group_vars/` for AAP 2.5+ targets |
  | Role meta normalization | L052, L053, L054 (role) | Normalize `galaxy_info.version` to semver, scaffold required `meta/main.yml` fields, add `galaxy_tags` |

- **Project Tier 3**: Agentic AI project transforms (ADR-027, future).
  Handles violations that require judgment and multi-step reasoning
  (e.g. L042 play restructuring, R108 privilege escalation policy).

### Pipeline Flow

```
Format (idempotency check)
          |
Pass 1: Unit-Level
  Tier 1 — deterministic unit transforms (FQCN, deprecated params)
  Tier 2 — AI unit transforms (task-scoped LLM proposals)
          |
  remaining violations partitioned:
    project_deterministic → Pass 2
    project_agentic → Pass 2 (ADR-027, future)
    manual → Tier 4
          |
Pass 2: Project-Level
  Tier 1 — deterministic project transforms (secret externalization)
  Tier 3 — agentic AI project transforms (ADR-027, future)
          |
Output
  Patches — file diffs for approval
  Next Steps — instructions via DataPayload
  Remaining — Tier 4 manual review
```

**Tier numbering**: ADR-009 defined Tiers 1-3. This ADR extends the
model: Tier 1 (deterministic, both unit and project), Tier 2 (AI unit),
Tier 3 (AI project/agentic per ADR-027), Tier 4 (manual review, formerly
Tier 3 in ADR-009). The partition function's return tuple reflects this
four-tier model.

### Partition Routing Changes

Extend `partition_violations()` to return a fourth bucket:

```python
def partition_violations(
    violations: list[ViolationDict],
    registry: TransformRegistry,
    project_registry: ProjectTransformRegistry,
) -> tuple[
    list[ViolationDict],  # tier1_unit
    list[ViolationDict],  # tier2_ai
    list[ViolationDict],  # tier1_project (new)
    list[ViolationDict],  # tier4_manual
]:
```

Routing logic for the new bucket:

- SEC:* violations (scope=`playbook`) → `tier1_project` when a
  project-level transform is registered for the rule
- L037, R501 (scope=`collection`) → `tier1_project` when the engine
  has a FQCN-to-collection mapping for the unresolved module
- L050 (scope=`inventory`) → `tier1_project` for cross-file variable
  rename refactoring
- M010 (scope=`play`) → `tier1_project` for Python interpreter cleanup
  across play vars and `group_vars/`
- L052, L053, L054 (scope=`role`) → `tier1_project` for role meta
  normalization
- `CROSS_FILE_RULES` (R111, R112) → `tier1_project` when a
  project-level transform exists; otherwise `tier3_manual`

### Project Transform Interface

```python
@dataclass
class ProjectTransformResult:
    patches: list[FilePatch]
    next_steps: list[NextStep]
    remaining: list[ViolationDict]


class ProjectTransform(Protocol):
    def apply(
        self,
        files: dict[str, bytes],
        violations: list[ViolationDict],
    ) -> ProjectTransformResult:
        """Transform project files to address violations.

        Args:
            files: All project files keyed by relative path.
            violations: Violations routed to this transform.

        Returns:
            Patches, next-step instructions, and remaining violations.
        """
        ...
```

Project transforms receive **all files** and the violations routed to them.
They return patches (file diffs), next-step instructions, and any
violations they could not address.

### "Next Steps" Protocol

Some remediations cannot be fully completed by APME. Secret
externalization is the canonical example: APME can replace hardcoded
values with `{{ vault_variable_name }}` references and insert
`vars_files:` entries, but it **must not** send the actual secret values
back over the wire. Instead, it emits **next-step instructions** telling
the user what to add to their Ansible Vault.

Next steps use the existing `DataPayload` session event (no proto schema
changes required):

```python
SessionEvent(
    data=DataPayload(
        kind="next_steps",
        data=Struct(
            fields={
                "category": "vault_setup",
                "title": "Add secrets to Ansible Vault",
                "items": [
                    {
                        "variable": "db_password",
                        "files": ["site.yml", "deploy.yml"],
                    },
                    {
                        "variable": "aws_access_key_id",
                        "files": ["provision.yml"],
                    },
                ],
                "instruction": (
                    "These variables were replaced with vault references. "
                    "Add the actual values to your Ansible Vault and "
                    "encrypt with: ansible-vault encrypt <vault-file>"
                ),
            }
        ),
    )
)
```

The UI renders this as an actionable checklist after the result is
delivered. The `useSessionStream` hook will need a new `"data"` case
in its message handler to receive `DataPayload` events, plus a
"Next Steps" component to render the structured instructions.

### Security Model for Secret Externalization

1. **Detection**: The Gitleaks validator identifies SEC violations during
   scanning. The `Match` field (actual secret value) is used internally
   for line-range mapping but is **never** included in violation protos
   or transmitted to clients.

2. **Transform**: The `SecretExternalizationTransform` replaces secret
   values in YAML with `{{ vault_variable_name }}` references. Patches
   contain only the vault variable references, not the original values.

3. **Next steps**: Instructions contain only variable **names**
   (`db_password`, `aws_access_key_id`), never values. The user already
   knows their secret values — APME tells them where to put them.

4. **Cross-file dedup**: When the same secret variable name appears in
   multiple playbooks, the transform deduplicates to a single vault
   variable entry. All playbooks reference the same vault file via
   `vars_files:`.

### Secret Externalization Transform (First Project Transform)

Implementation based on the detection patterns proven in PR #70
(ADR-035):

```python
class SecretExternalizationTransform:
    """Replace hardcoded secrets with Ansible Vault variable references.

    Detection uses gitleaks findings (SEC violations from the scan) unioned
    with variable-name heuristics for low-entropy placeholder values.

    Output:
    - Patches: playbook vars replaced with {{ vault_var }} references,
      vars_files entry inserted
    - Next steps: vault setup instructions (variable names only,
      never values)
    """

    def apply(
        self,
        files: dict[str, bytes],
        violations: list[ViolationDict],
    ) -> ProjectTransformResult:
        # Group SEC violations by file
        # For each file with SEC violations:
        #   1. Parse YAML (ruamel round-trip mode)
        #   2. Union gitleaks findings with name-heuristic matches
        #   3. Replace secret values with {{ vault_var_name }}
        #   4. Insert vars_files reference
        #   5. Collect variable names for next-steps
        # Deduplicate common variables across files
        # Return patches + next-step instructions
        ...
```

## Alternatives Considered

### Alternative 1: Standalone CLI subcommand (ADR-035 / PR #70)

**Pros**: Works without daemon; zero proto changes.

**Cons**: Duplicates detection logic; bypasses the remediation pipeline;
creates parallel code paths; no approval workflow; no cross-file dedup;
directory mode has overwrite bugs.

**Why not chosen**: Secret externalization is a cross-file remediation
transform that should run within the engine and benefit from its scan
results, approval flow, and reporting.

### Alternative 2: Fold everything into ADR-027 (agentic)

**Pros**: Single project-level tier.

**Cons**: Deterministic transforms don't need AI; using an LLM agent for
what is essentially a structured find-and-replace is wasteful and slower.
The agentic tier adds cost and latency for transforms that have
predictable, testable behavior.

**Why not chosen**: Deterministic project transforms should run as
Tier 1 (fast, free, predictable) before the agentic tier is invoked for
violations that genuinely need judgment.

### Alternative 3: Extend unit-level Tier 1

**Pros**: No new pass; simpler engine changes.

**Cons**: Unit-level transforms see one file at a time. Secret
externalization creates new files and deduplicates across the project.
The abstraction doesn't fit.

**Why not chosen**: Project-level transforms fundamentally need multi-file
visibility.

## Consequences

### Positive

- **Broad Tier 3 reduction** — SEC:*, L037, R501, L050, M010, L052-L054
  currently fall straight to manual review; project-level transforms
  give them automated remediation paths
- **No proto changes** — next steps use existing `DataPayload`
- **Security by design** — secret values never leave the engine
- **Shared cross-file machinery** — variable reference tracking needed
  by both secret externalization and variable rename refactoring;
  FQCN-to-collection mappings from ADR-032 feed directly into
  requirements.yml generation
- **Complements ADR-027** — deterministic Pass 2 runs before the agentic
  tier, reducing the workload for the (more expensive) AI path

### Negative

- **Engine complexity** — `RemediationEngine.remediate()` gains a second
  pass with its own transform registry and convergence check
- **New abstraction** — `ProjectTransformRegistry` and `ProjectTransform`
  protocol add surface area
- **UI work** — frontend needs a "Next Steps" component to render
  `DataPayload` with `kind="next_steps"`

### Neutral

- Pass 2 is only invoked when project-level violations exist; scan-only
  and unit-level-fix workflows are unaffected
- The agentic tier (ADR-027) remains future work and is not blocked by
  this change

## Implementation Notes

1. **Phase 1: Engine plumbing**
   - Add `ProjectTransformRegistry` and `ProjectTransform` protocol
   - Extend `partition_violations()` with fourth bucket
   - Add second pass to `RemediationEngine.remediate()`
   - Emit `DataPayload(kind="next_steps")` from `_session_process`

2. **Phase 2: Secret externalization transform**
   - Refactor detection logic from PR #70 into
     `SecretExternalizationTransform`
   - Register for SEC:* rule patterns
   - YAML round-trip editing with `ruamel.yaml`
   - Cross-file variable deduplication

3. **Phase 3: UI**
   - "Next Steps" panel in the React frontend
   - Render after session result is delivered
   - Actionable checklist format with copy-to-clipboard for vault commands

4. **Phase 4: Collection dependency generation**
   - `CollectionDependencyTransform` for L037/R501
   - Generate/update `collections/requirements.yml` from unresolved FQCNs
   - Leverage ADR-032's FQCN-to-collection mapping

5. **Phase 5: Variable rename refactoring**
   - `VariableRenameTransform` for L050
   - Build cross-file variable reference index (definitions + usages)
   - Rename across `vars:`, `set_fact:`, `register:`, Jinja2
     expressions, `group_vars/`, `host_vars/`, role defaults
   - Emit next-steps for external references (Tower surveys, inventory
     scripts, CI/CD env vars)

6. **Phase 6: Python interpreter and role meta cleanup**
   - `PythonInterpreterTransform` for M010 — remove or set to `auto`
   - `RoleMetaTransform` for L052/L053/L054 — semver normalization,
     required field scaffolding, `galaxy_tags` addition

## Related Decisions

- ADR-009: Remediation Engine (tiered architecture — extended by this ADR)
- ADR-027: Agentic Project-Level AI Remediation (complements as Tier 3)
- ADR-028: Session-Based Fix Workflow (transport for patches and next steps)
- ADR-035: Secret Externalization (requirements and detection approach —
  superseded implementation)

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-23 | Bradley A. Thornton | Initial proposal |
| 2026-03-23 | Bradley A. Thornton | Added concrete transform candidates grounded in rule catalog (L037/R501, L050, M010, L052-L054) |
