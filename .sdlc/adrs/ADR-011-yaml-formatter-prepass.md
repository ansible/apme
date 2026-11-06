# ADR-011: YAML Formatter as Phase 1 Pre-Pass

## Status

Implemented

## Date

2026-03

## Context

Remediation (semantic fixes) requires clean YAML as input. If formatting and semantic fixes are interleaved, diffs become noisy and convergence is harder to guarantee.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Format + fix in one pass | Single pipeline | Noisy diffs, hard to verify formatter stability |
| Format as Phase 1, then fix | Clean separation, idempotency gate | Two passes over files |
| Route formatter through remediation engine | Unified pipeline | Requires artificial "formatting violations", scan-before-format |

## Decision

**YAML formatter runs as Phase 1, completely independent of the scan and remediation engine.**

The pipeline is:

```
format → idempotency gate → scan → remediate → rescan → converge
```

## Rationale

- The formatter (`ruamel.yaml` round-trip) normalizes:
  - Indentation
  - Key ordering
  - Jinja spacing
  - Tab removal
- Idempotency is verified by running the formatter twice — if the second pass produces any diffs, the formatter has a bug and the pipeline aborts
- Subsequent semantic diffs are clean because formatting noise has been eliminated
- The formatter does not consume violations — it operates on raw YAML

## Consequences

### Positive
- Clean semantic diffs
- Idempotency guarantee
- Formatter bugs caught early
- Separation of concerns

### Negative
- Two passes over files
- Formatter must be idempotent

## Implementation Notes

### Idempotency Gate

```python
def verify_idempotency(path: Path) -> bool:
    """Format twice, verify no diff on second pass."""
    format_yaml(path)
    first_content = path.read_text()

    format_yaml(path)
    second_content = path.read_text()

    if first_content != second_content:
        raise FormatterNotIdempotentError(path)
    return True
```

### Formatter Configuration

```yaml
formatter:
  indent_sequences: 2
  indent_mappings: 2
  preserve_quotes: true
  explicit_start: true
  jinja_spacing: true
```

### Pipeline Stages

1. **Format**: Normalize all YAML files
2. **Idempotency Gate**: Re-format, verify zero diff
3. **Scan**: Run all validators
4. **Remediate**: Apply Tier 1 transforms
5. **Rescan**: Verify fixes
6. **Converge**: Repeat 4-5 until stable

### Blank-Line Cleanup

Line-based cleanup preserves blank lines inside literal and folded block
scalars. The task-spacing pass uses the same scalar-content mask, so embedded
task-like YAML is left as scalar data. Scalar indentation is calculated from
the mapping key for dash-first mapping headers and from the sequence dash for
direct sequence scalars. Task-list membership is classified in one forward
pass so formatting large task lists does not repeat backward searches for each
blank line.

## Related Decisions

- ADR-009: Remediation engine (consumes formatted YAML)

## Revision History

| Date | Author | Change |
|------|--------|--------|
| 2026-11-06 | AI agent | Document scalar-safe cleanup and task spacing, indentation handling, and linear task-list classification |
