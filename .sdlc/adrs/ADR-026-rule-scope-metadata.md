# ADR-026: Rule Scope as First-Class Metadata

## Status

Accepted

## Date

2026-03-19

## Context

APME's remediation engine routes violations to three tiers:

1. **Tier 1 (Deterministic)** — rule has a registered transform
2. **Tier 2 (AI-Proposable)** — send to LLM for fix proposal
3. **Tier 3 (Manual Review)** — requires human judgment

The current partition logic uses a hardcoded set (`PLAY_LEVEL_RULES`) to identify violations that shouldn't go to AI:

```python
PLAY_LEVEL_RULES: frozenset[str] = frozenset({
    "L042",  # high task count complexity
    "M010",  # Python 2 interpreter
    "R108",  # privilege escalation
})
```

This approach has significant problems:

- **Not self-documenting** — rule scope is defined far from the rule itself
- **Error-prone** — new rules require updating a separate list
- **Incomplete** — the list must be manually audited for each new rule
- **No query capability** — can't filter or group rules by scope at scan time
- **Wasted AI calls** — play-level violations sent to task-level AI produce useless "cannot fix" responses

During AI escalation testing, we observed that play-level rules (L042, M010, R108) were being sent to the LLM once per task in the play, resulting in ~60 duplicate "I cannot fix this play-level issue" responses — wasting tokens and time.

## Decision

**We will add a `scope` field to all rule definitions as first-class metadata, and use it to determine remediation routing.**

### Scope Enum

```python
class RuleScope(str, Enum):
    """Structural scope at which a rule operates."""
    
    TASK = "task"             # Individual task — AI can propose fixes
    BLOCK = "block"           # Block structure — AI may help
    PLAY = "play"             # Play header, vars, become — manual
    PLAYBOOK = "playbook"     # Multi-play structure — manual
    ROLE = "role"             # Role-level (meta, defaults) — manual
    INVENTORY = "inventory"   # Inventory/group_vars — manual
    COLLECTION = "collection" # Cross-repo scope — manual
```

### Violation Schema Extension

```protobuf
message Violation {
  // ... existing fields ...
  RuleScope scope = 15;  // Structural scope of this violation
}
```

### Partition Logic

```python
AI_PROPOSABLE_SCOPES = frozenset({RuleScope.TASK, RuleScope.BLOCK})

def partition_violations(...):
    for v in violations:
        if is_finding_resolvable(v, registry):
            tier1.append(v)
        elif v.scope in AI_PROPOSABLE_SCOPES:
            tier2.append(v)
        else:
            tier3.append(v)
```

## Alternatives Considered

### Alternative 1: Hardcoded Rule Lists

**Description**: Maintain `PLAY_LEVEL_RULES`, `CROSS_FILE_RULES`, etc. as frozensets in partition.py.

**Pros**:
- Simple to implement
- No schema changes required
- Works today

**Cons**:
- Must remember to update list for each new rule
- Rule scope is defined far from rule definition
- No way to query scope at scan time
- Lists can drift out of sync

**Why not chosen**: Does not scale; already causing issues with duplicate AI failures.

### Alternative 2: Infer Scope from Violation Line Context

**Description**: At partition time, analyze the violation's line number against the parsed YAML structure to determine if it's in a play header, task, etc.

**Pros**:
- No rule changes required
- Works for existing violations

**Cons**:
- Expensive to compute per-violation
- Unreliable for edge cases (e.g., play-level `become:` affects all tasks)
- Doesn't capture semantic scope (e.g., "this rule is about variable naming across files")

**Why not chosen**: Scope is a property of the rule, not the line number.

### Alternative 3: `ai_proposable` Boolean Field

**Description**: Add a simple `ai_proposable: bool` field to violations.

**Pros**:
- Simple schema change
- Direct signal for partition logic

**Cons**:
- Loses semantic information (why isn't it AI-proposable?)
- Can't distinguish play-level from cross-file from security-review
- Future routing logic would need more granularity

**Why not chosen**: `scope` provides richer information for multiple use cases.

## Consequences

### Positive

- **Self-documenting rules** — scope is defined with the rule, not in a separate list
- **Correct routing** — play-level violations never waste AI calls
- **Query capability** — can filter/group violations by scope in reports
- **Extensible** — new scopes can be added without changing partition logic
- **Consistent** — OPA, native, and Ansible validators all use the same schema

### Negative

- **Schema migration** — existing violations lack `scope`; must default to `TASK`
- **Rule audit** — all existing rules must be reviewed and assigned a scope
- **Proto regeneration** — gRPC clients need updated stubs

### Neutral

- OPA policies will need `scope` in their output; Rego changes required
- Native validators already have rule classes; adding scope is straightforward

## Implementation Notes

All steps completed in the initial implementation PR:

1. **`RuleScope` enum added to `models.py`** alongside `RemediationClass` and `RemediationResolution`
2. **Proto schema extended**: `RuleScope` enum + `scope = 10` field on `Violation` in `common.proto`; stubs regenerated
3. **`RuleMetadata` base class** gains `scope: str = RuleScope.TASK` — all 19 non-TASK native rules override explicitly
4. **OPA policies**: all 28 Rego rules emit `"scope": "<value>"` in violation objects
5. **Ansible validator**: M001-M004 (task), L057 (playbook), L058/L059 (task)
6. **Gitleaks validator**: SEC violations emit `"scope": "playbook"`
7. **`_extract_results`**: threads `scope` from `RuleMetadata` into native violation dicts
8. **Violation converters**: `violation_dict_to_proto` / `violation_proto_to_dict` in both daemon and CLI carry `scope`
9. **`partition.py` rewritten**: `PLAY_LEVEL_RULES` eliminated; routing uses `AI_PROPOSABLE_SCOPES = {TASK, BLOCK}`. `CROSS_FILE_RULES` reduced to `{R111, R112}` — the remaining task-scoped rules that need cross-file context
10. **Rule docs**: 92 `.md` files updated with `scope:` in YAML front matter
11. **Backward compatibility**: violations without `scope` default to `TASK` (most permissive)

## Related Decisions

- ADR-008: Rule ID Conventions (L/M/R/P/SEC prefixes)
- ADR-009: Remediation Engine (validators are read-only)
- ADR-023: Per-Finding Classification (remediation class/resolution)
- ADR-024: AI Provider Protocol (Tier 2 escalation)

## References

- Current hardcoded lists: `src/apme_engine/remediation/partition.py`
- Rule documentation: `docs/rules/`
- OPA policy examples: `opa/bundle/apme/`

---

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-03-19 | AI Agent | Initial proposal |
| 2026-03-19 | AI Agent | Accepted — full implementation: proto schema, models, all rule classes/docs/Rego, converters, partition logic, 11 new tests |
