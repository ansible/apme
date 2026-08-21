# DR-021: Should L039 (undefined variable) have AI remediation guidance?

## Status

Open

## Raised By

User — 2026-08-21

## Category

Technical

## Priority

High

---

## Question

Should L039 (undefined variable) have an `ai_prompt` field to enable effective AI-assisted remediation, and what should the guidance say?

## Context

During AI remediation testing on [eloycoto/ansible-sample PR #2](https://github.com/eloycoto/ansible-sample/pull/2), L039 findings were marked "AI eligible" but the AI made no fixes ("Findings resolved: 0").

Investigation found:
1. Only 5 rules have `ai_prompt` guidance — all R-series (R101, R103, R104, R105, R108)
2. L039 has no `ai_prompt`, so AI doesn't know how to handle undefined variables
3. Without guidance, AI skips L039 findings entirely

The flagged code:
```yaml
- name: Create PostgreSQL database
  ansible.builtin.shell:
    cmd: sudo -u postgres psql -c "CREATE DATABASE {{ db_name }} OWNER {{ db_username }};"
  when: pg_db_check.stdout != '1'
```

L039 flags `db_name`, `db_username`, `pg_db_check` as potentially undefined — but they come from inventory/role params and are valid at runtime.

## Impact of Not Deciding

- L039 findings remain "AI eligible" but never fixed
- Users see false promise of AI remediation
- Reduces trust in AI remediation feature

---

## Options Considered

### Option A: Add ai_prompt with noqa-first guidance

**Description**: Add guidance that tells AI to add `# noqa: L039` when variables appear to come from trusted sources (inventory, role params, extra_vars).

**Proposed ai_prompt**:
```yaml
ai_prompt: |
  L039 flags variables that may be undefined at static analysis time. If the
  variable appears to come from inventory, role parameters, extra_vars, or is
  a registered variable from a prior task, add "# noqa: L039" to suppress —
  but your explanation MUST justify why (e.g. "variable comes from role
  defaults"). If the variable appears genuinely undefined with no clear source,
  skip the finding and let the user handle it manually.
```

**Pros**:
- Matches R-series pattern (noqa when intentional)
- Reduces false positive noise
- Conservative — doesn't add arbitrary defaults

**Cons**:
- May suppress some true positives
- Doesn't actually define missing variables

**Effort**: Low

### Option B: Add ai_prompt with default() filter guidance

**Description**: Tell AI to add `| default('')` or similar filters to make variables safe.

**Pros**:
- Actually fixes the code
- Makes playbook more defensive

**Cons**:
- Wrong default value could break playbook
- Not what user intended in most cases
- Variables from inventory/params shouldn't need defaults

**Effort**: Low

### Option C: Keep L039 manual-only

**Description**: Remove L039 from AI eligibility — undefined variable detection requires human judgment about where variables should come from.

**Pros**:
- Avoids AI making wrong assumptions
- Human reviews variable sources

**Cons**:
- Defeats purpose of AI remediation
- Other rules with similar ambiguity still work (R-series)

**Effort**: Low

---

## Recommendation

**Option A** — matches existing R-series pattern. AI adds noqa when source appears trusted, skips when genuinely unknown.

---

## Related Artifacts

- DR-019: AI Remediation Rule Compliance
- DR-020: M005 False Positive Exemptions
- GitHub: eloycoto/ansible-sample PR #2 (test case)
- **PR #582**: [fix(L039): add ai_prompt for AI-assisted remediation](https://github.com/ansible/apme/pull/582)

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| 2026-08-21 | User | Reported AI remediation not fixing L039 findings |
| 2026-08-21 | Claude | Found L039 has no ai_prompt; only R-series rules have guidance |

---

## Decision

**Status**: Open
**Date**: 
**Decided By**: 

**Decision**: 

**Rationale**: 

**Action Items**:
- [ ] Add ai_prompt to L039 guidance file
- [ ] Test with eloycoto/ansible-sample PR
- [ ] Document in remediation docs

---

## Post-Decision Updates

| Date | Update |
|------|--------|
