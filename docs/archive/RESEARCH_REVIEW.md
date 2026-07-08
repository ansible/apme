# Research Review: ARI Integration Strategy (research.md)

**Reviewed**: 2026-03-05
**Source**: [`research.md`](research.md) (predates current architecture)
**Context**: The research document was written for an x2a-convertor integration approach using ARI as a pip dependency. The project has since evolved into a standalone multi-validator platform with a vendored engine, gRPC contracts, and containerized deployment.

## Status by Section

### Superseded — Already Implemented (differently)

| Section | Research Recommendation | What We Did Instead |
|---------|------------------------|---------------------|
| **R1** ARI Integration | Use ARI as a pip dependency via `ARIScanner` | Fully integrated the engine into `src/apme_engine/engine/`; first-class owned code, not an external dep |
| **R2** Programmatic API | Call `ARIScanner(Config(...)).evaluate()` | Our own `runner.py` + `ScanContext` wrapping the integrated engine; same pipeline, owned code |
| **R3** x2a-convertor Patterns | Click CLI, BaseAgent, ValidationService, ARIValidator, ARIScanTool | argparse CLI, gRPC daemon, unified `Validator` protocol over gRPC; no x2a-convertor dependency |
| **R6** Output Directory | Write corrected files to `--output-dir ./modernized/` | `format --apply` writes in-place; `FormatResult.diff` for diffing; `remediate` follows the same model |
| **R9** Test Data | Synthetic fixture directories per category | Colocated `*_test.py` / `*_test.rego` per rule + `.md` docs with frontmatter examples; integration via `test_e2e.py` |

### Partially Addressed — Core Concepts Adopted

| Section | Concept | Current State |
|---------|---------|---------------|
| **R4** Two-Tier Pipeline | Deterministic scan → partition → AI escalation | Architecture supports this: formatter (Phase 1) → modernize (Phase 2, stubbed) → AI (Phase 3, planned). The partition logic is not yet implemented. |
| **R5** Version Detection | Infer source Ansible version from playbook patterns | M-series rules cover 2.19/2.20 explicitly; the heuristic-based auto-detection (signal-to-version-ceiling) is not implemented. |
| **R7** Custom Rules (SEC/AAP/EE) | Nine new ARI-style rules for secrets, AAP migration, EE compat | AAP module deprecation partially covered by M-series and the ansible validator. SEC and EE rules not implemented. |
| **R8** Module Metadata | Static `module_metadata.json` from `ansible-doc` diffs | No machine-readable module metadata file; `ANSIBLE_CORE_MIGRATION.md` covers 2.19/2.20 in human-readable form. |

### Not Yet Addressed — Valuable Concepts

| Section | Concept | Value |
|---------|---------|-------|
| **R4** `is_finding_resolvable()` | Single decision point partitioning findings into auto-fixable vs manual/AI | Essential for Phase 2 modernization engine to decide what can be auto-fixed vs what needs AI |
| **R4** Multi-pass convergence | check → remediate → re-check → bail on oscillation | Core loop for the `remediate` subcommand's modernization phase |
| **R4** AI prompt template | Structured LLM prompt with finding context + 10-line code window | Starting point for Phase 3 OpenLLM integration |
| **R5** Auto-detection signals | Short-form modules → ≤2.9, `include:` → ≤2.7, `tower_*` → ≤2.13 | Auto-scopes M-rules without explicit `--ansible-core-version` flag |
| **R7** SEC001–SEC003 | Hardcoded passwords, API keys, private keys (regex-based) | **Implemented** via Gitleaks validator (800+ patterns, supersedes the 3 research rules) |
| **R7** EE001–EE003 | Undeclared collections, system path assumptions, Python deps | Unique value-add for users moving to containerized Ansible execution |
| **R8** `module_metadata.json` | Machine-readable module lifecycle + parameter deprecation data | Modernize rules could query this instead of hardcoding version-specific logic per rule |
| **R10** EE Baseline | Static allowlist of packages/paths/collections in `ee-supported-rhel9` | Data source for EE rules; `--ee-config` overlay for custom EE definitions |

## Valuable Concepts — Detail

### 1. Finding Partition Function (`is_finding_resolvable`)

The research proposes a single function as the sole decision point for splitting findings into "deterministic fix" vs "AI/manual":

```python
def is_finding_resolvable(rule_result) -> bool:
    return getattr(rule_result.rule, 'spec_mutation', False)
```

This maps cleanly to our validator model. Each rule could declare a `fixable: bool` attribute on its metadata. The `remediate` pipeline would use this to decide whether to attempt automatic remediation or defer to the AI service. This should be a property on our rule metadata base class, not inferred via `getattr`.

### 2. Multi-Pass Convergence Algorithm

The algorithm from R2/R4:
1. Count fixable violations
2. Apply fixes
3. Re-check (internal re-scan)
4. If count decreased → repeat (up to `--max-passes`)
5. If count unchanged or increased → oscillation, bail out

This is the correct approach for our `remediate` subcommand's modernization phase. The formatter already has `check_idempotent()` as a simpler form of this; the modernizer needs the full convergence loop with oscillation detection.

### 3. Security Rules — Implemented via Gitleaks

The research proposed three regex-based native rules (SEC001–SEC003). Instead, we implemented a **Gitleaks validator** — a dedicated container wrapping the Gitleaks binary (800+ secret patterns) behind the unified gRPC `Validator` contract. This supersedes the need for hand-written regex rules and provides far broader coverage.

The Gitleaks wrapper adds Ansible-specific filtering:
- Vault-encrypted files (`$ANSIBLE_VAULT;`) are excluded
- Jinja2 expressions (`{{ var }}`) are filtered as false positives
- Rule IDs are prefixed `SEC:` (e.g., `SEC:aws-access-key-id`)

### 4. Version Auto-Detection

Signal table from R5 for inferring source Ansible version:

| Signal | Version Ceiling | Detection |
|--------|----------------|-----------|
| Short-form module names (no FQCN) | 2.9 | OPA/native already flags this |
| `include:` instead of `include_tasks:` | 2.7 | Detectable from hierarchy |
| No `collections:` keyword in plays | 2.9 | Detectable from hierarchy |
| `tower_*` module names | 2.13 | Detectable from task data |

The minimum ceiling across all triggered signals becomes the inferred source version. This lets us auto-scope M-rules (modernize) and surface version-specific findings without requiring the user to declare their source version.

### 5. EE Compatibility Rules

Three rules checking playbook assumptions against Execution Environment baselines:

| Rule | Proposed ID | What It Checks |
|------|-------------|----------------|
| Undeclared collections | R505 | Used collections missing from `requirements.yml` and EE baseline |
| System path assumptions | R506 | Filesystem paths and package references not in EE allowlist |
| Undeclared Python deps | R507 | Python imports not in EE baseline or `requirements.txt` |

Requires a static `ee_baseline.json` (derivable from `podman inspect` + `pip list` on `ee-supported-rhel9`). The `--ee-config` overlay for custom EE definitions is a follow-on.

### 6. Module Metadata File

A machine-readable `module_metadata.json` covering module lifecycle (introduced, deprecated, removed, replacement), parameter changes, and version diffs. Generated by a script that runs `ansible-doc --list --json` across ansible-core versions and parses porting guides.

This would replace per-rule hardcoded version logic in M-series rules with data-driven lookups.

### 7. AI Escalation Prompt Template

Structured prompt for Phase 3 OpenLLM integration:
- Finding metadata (rule ID, severity, file, line)
- 10-line code window around the violation
- Structured response schema: explanation, suggested_code, confidence, reasoning, applicable

The `LLMRemediationResponse` model (without `finding_id`, injected by the wrapper) is a clean separation of LLM output from internal entity tracking.
