# DR-020: How should M005 handle safe registered variable usage?

## Status

Open

## Raised By

User — 2026-08-20

## Category

Technical

## Priority

High

---

## Question

How should M005 distinguish unsafe re-templating from safe registered-result
usage in loop sources and assert conditions?

## Context

M005 (data tagging trust model) warns about registered variables used in Jinja templates because ansible-core 2.19+ treats module results as untrusted. However, the rule currently fires on safe patterns:

1. `ansible.builtin.assert` conditions that compare strings (not template them)
2. Test files (`molecule/`, `tests/`) where verification is the purpose
3. Comparison operators (`in`, `==`) that don't re-template content
4. Loop sources such as `{{ nginx_dir_stat_results.results }}`

Example false positive from molecule verify.yml:
```yaml
- ansible.builtin.slurp:
    src: /tmp/test/security.conf
  register: security_conf_content

- ansible.builtin.assert:
    that:
      - "'server_tokens off' in (security_conf_content['content'] | b64decode)"
```

GitHub issue: https://github.com/ansible/apme/issues/581

## Impact of Not Deciding

- Users receive noisy false positives in test code
- Reduces trust in M005 rule accuracy
- May lead users to disable M005 entirely, missing real issues

---

## Options Considered

### Option A: Field-Aware Detection (Recommended)

**Description**: Inspect task-field context and exclude registered variables used
as `loop` data sources. Continue flagging registered values interpolated into
module arguments or dynamically constructed expressions.

**Pros**:
- Simple implementation (~5 lines)
- Handles reported loop-source case without exempting whole modules
- Low risk of masking real issues

**Cons**:
- Doesn't exempt test paths
- Requires preserving field names during scanning

**Effort**: Medium

### Option B: Module Exemption

**Description**: Exempt `ansible.builtin.assert` and `ansible.builtin.fail`.

**Pros**:
- Simple implementation
- Handles common assert false positives

**Cons**:
- Can hide future unsafe usage inside exempt modules
- Does not solve loop-source false positives for other modules

**Effort**: Low

### Option C: Operator Detection

**Description**: Parse expressions to distinguish comparisons from possible
re-templating.

**Pros**:
- More precise than module/path exemptions
- Targets actual expression semantics

**Cons**:
- Requires expression parsing
- May miss edge cases

**Effort**: High

### Option D: Path-Based Exemption

**Description**: Exempt test directories from M005 entirely.

**Pros**:
- Simple path matching
- Covers common test verification patterns

**Cons**:
- Masks real issues in test code
- Does not help production verification playbooks
- Overly broad exemption

**Effort**: Low

---

## Recommendation

Implement **Option A (Field-Aware Detection)**. It fixes the reported false
positive without broad module or path exemptions. Evaluate operator-aware
parsing separately if more cases appear.

---

## Related Artifacts

- DR-018: Risk Rule Guidance Trust Context (related trust model questions)
- ADR-008: Rule ID Conventions (M prefix for modernization rules)
- GitHub #581: M005 false positive on ansible.builtin.assert conditions

---

## Discussion Log

| Date | Participant | Input |
|------|-------------|-------|
| 2026-08-20 | User | Reported false positive in molecule verify.yml with slurp + assert pattern |
| 2026-08-20 | Claude | Confirmed no exemptions in current implementation; proposed options |
| 2026-09-01 | Review | Confirmed loop-source access is safe; recommend field-aware exclusion |

---

## Decision

**Status**: Open
**Date**: 
**Decided By**: 

**Decision**: Pending maintainer review. Proposed implementation is field-aware
exclusion for `loop` sources, with no path-based or broad module exemption.

**Rationale**: 

**Action Items**:
- [ ] Implement chosen exemption strategy
- [ ] Add test coverage for exempted patterns
- [ ] Update M005 rule documentation
- [ ] Open upstream PR and link it here

---

## Post-Decision Updates

| Date | Update |
|------|--------|
