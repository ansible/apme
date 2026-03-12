# ADR-009: Separate Remediation Engine with Transform Registry

## Status

Accepted

## Date

2026-03

## Context

We needed a strategy for automatically fixing detected violations. Should fixes be embedded in rules, or should there be a separate engine?

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Rules fix violations inline | Self-contained | Rules become read-write, mixing concerns |
| Separate remediation engine | Clean separation (detect vs fix) | Additional component to maintain |
| AI-only fixes | Handles everything | Unreliable, expensive, slow |

## Decision

**Separate remediation engine with a three-tier finding classification:**

| Tier | Description | Automation |
|------|-------------|------------|
| **Tier 1** | Deterministic transforms | Registered functions in `TransformRegistry`, keyed by rule ID. Always correct, fully automated. |
| **Tier 2** | AI-proposable | Findings where an LLM can suggest a fix with high confidence. Human reviews before applying. |
| **Tier 3** | Manual review | Findings that require human judgment (architectural concerns, security risk acceptance). |

## Rationale

- Validators are read-only by design — they detect but never modify files
- The remediation engine operates on the YAML AST (`ruamel.yaml` round-trip) and produces diffs
- A convergence loop (`scan → fix → rescan → repeat until stable`) ensures correctness
- The formatter is a blind pre-pass, not part of the remediation engine
- Separating remediation into its own service enables future AI escalation without touching validators

> "I think B is best since as a separate service, it could invoke AI if needed right?" — user decision

## Consequences

### Positive
- Clean separation of concerns (detect vs fix)
- Validators remain stateless and read-only
- Tiered approach handles complexity gracefully
- AI integration path is clear

### Negative
- Additional service to maintain
- Transform registry must be kept in sync with rules

## Implementation Notes

### Transform Registry

```python
class TransformRegistry:
    _transforms: dict[str, TransformFunc] = {}

    @classmethod
    def register(cls, rule_id: str):
        def decorator(func: TransformFunc):
            cls._transforms[rule_id] = func
            return func
        return decorator

    @classmethod
    def get(cls, rule_id: str) -> TransformFunc | None:
        return cls._transforms.get(rule_id)

@TransformRegistry.register("L002")
def fix_fqcn(node: YAMLNode, violation: Violation) -> YAMLNode:
    """Replace short module name with FQCN."""
    ...
```

### Convergence Loop

```
format → idempotency gate → scan → remediate → rescan → converge
```

### Tier Classification in Config

```yaml
rules:
  L002:
    tier: 1  # Deterministic
  L015:
    tier: 2  # AI-proposable
  R101:
    tier: 3  # Manual review
```

## Related Decisions

- ADR-002: OPA/Rego rules (detection)
- ADR-011: YAML formatter as pre-pass
