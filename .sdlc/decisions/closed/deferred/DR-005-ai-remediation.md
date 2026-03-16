# DR-005: AI-Assisted Remediation Execution

## Status

Open

## Raised By

Team Review — 2026-03-11

## Category

Technical

## Priority

Medium

---

## Question

How does AI-assisted remediation actually work? The PRD says "AI-assisted remediation for complex logic transitions" but:

- With what model? Self-hosted or API?
- What's the trust model for AI-generated code changes?
- How does Tier 2 (AI-proposable) actually execute?

ADR-009 defines the Tier 2 partition but the execution path is undefined.

## Context

ADR-009 establishes three tiers:
- **Tier 1**: Deterministic transforms (TransformRegistry) — fully automated
- **Tier 2**: AI-proposable — LLM suggests fix, human reviews
- **Tier 3**: Manual review — human judgment required

Tier 2 is mentioned but not implemented. Questions:
- Which LLM? (Claude, GPT-4, local models?)
- API vs self-hosted? (Cost, latency, data privacy)
- How are suggestions presented? (Diff in CLI? PR comment? Dashboard?)
- What's the approval workflow?

## Impact of Not Deciding

- Tier 2 remediation cannot be implemented
- No clarity on LLM costs/infrastructure
- Data privacy requirements undefined (can we send code to external API?)

---

## Options Considered

### Option A: Claude API (External)

**Description**: Use Anthropic Claude API for Tier 2 suggestions. Send code context + violation, receive suggested fix.

**Pros**:
- Best-in-class code generation
- No infrastructure to maintain
- Fast iteration on prompts

**Cons**:
- Per-token cost
- Data leaves user's environment
- Enterprise compliance concerns
- API availability dependency

**Effort**: Low

### Option B: Self-Hosted LLM (Ollama/vLLM)

**Description**: Deploy local LLM (CodeLlama, DeepSeek, etc.) via Ollama or vLLM. All inference happens in user's environment.

**Pros**:
- Data never leaves environment
- No per-token cost after setup
- Works airgapped

**Cons**:
- Significant infrastructure (GPU)
- Quality may be lower than frontier models
- Model management complexity

**Effort**: High

### Option C: Hybrid (User Choice)

**Description**: Support both modes via configuration:
- `APME_LLM_PROVIDER=anthropic` → Claude API
- `APME_LLM_PROVIDER=ollama` → Local model
- `APME_LLM_PROVIDER=none` → Tier 2 disabled

**Pros**:
- Maximum flexibility
- Enterprise can use self-hosted
- Individuals can use API

**Cons**:
- More code paths to maintain
- Testing both providers

**Effort**: Medium

### Option D: Defer AI to v2

**Description**: Ship v1 with Tier 1 only. Tier 2 findings are flagged as "requires manual review" with no AI suggestion.

**Pros**:
- Simpler v1
- Avoids LLM complexity
- Focus on deterministic quality

**Cons**:
- Less differentiation
- Manual work for users on complex fixes

**Effort**: None (deferral)

---

## Recommendation

**Option D** (defer) for v1, then **Option C** (hybrid) for v2.

Rationale:
- Tier 1 transforms cover the majority of FQCN issues
- AI adds complexity (prompts, cost, privacy)
- Better to ship solid deterministic fixes first
- Can learn from Tier 2/3 distribution in real usage

---

## Related Artifacts

- ADR-009: Remediation Engine
- REQ-002: Rewriter Module
- PRD: AI remediation mention

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| 2026-03-11 | Team | Initial question raised during PRD review |

---

## Decision

**Status**: Deferred
**Date**: 2026-03-16
**Decided By**: Team

**Decision**: Deferred — Brad investigating as part of PHASE-001 delivery

**Rationale**:
- Active investigation underway by Brad
- Part of PHASE-001 delivery scope
- Decision will be informed by Brad's findings
- Will revisit when investigation complete

**Revisit**: When Brad's investigation is complete

**Action Items**:
- [ ] Brad to complete AI remediation investigation
- [ ] Schedule DR review with Brad's findings
- [ ] Update ADR-009 if approach changes
