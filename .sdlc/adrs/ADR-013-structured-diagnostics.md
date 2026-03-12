# ADR-013: Structured Diagnostics in the gRPC Contract

## Status

Accepted

## Date

2026-03

## Context

During development and production operation, understanding where time is spent during a scan is critical. Ad-hoc stderr logging was insufficient for programmatic consumption.

## Options Considered

| Option | Pros | Cons |
|--------|------|------|
| Ad-hoc stderr logging | Simple, immediate | Not programmatic, not visible to CLI or UI |
| Structured diagnostics in proto | Machine-readable, flows through gRPC, CLI and UI can display | Proto changes, always collected |
| Optional sidecar (Prometheus, OpenTelemetry) | Standard observability | Infrastructure overhead, not embedded in response |

## Decision

**Add structured diagnostics messages to the proto contract:**

| Message | Contents |
|---------|----------|
| `RuleTiming` | Per-rule elapsed time and violation count |
| `ValidatorDiagnostics` | Per-validator summary (total time, file count, violation count, rule timings, metadata) |
| `ScanDiagnostics` | Engine phases + all validator diagnostics aggregated |

Diagnostics are **always collected** by every validator and the engine.

Display in the CLI is gated by verbosity:

| Flag | Display |
|------|---------|
| (none) | Violations only |
| `-v` | Validator summaries + top 10 slowest rules |
| `-vv` | Full per-rule breakdown, metadata, engine phase timing |

## Rationale

- Diagnostics data is carried in the gRPC response — no log parsing needed
- Future UIs, CI integrations, and API consumers can access timing data directly from `ScanResponse.diagnostics`
- Always-collect/tiered-display means zero overhead for users who don't need it, full detail for those who do
- Per-rule granularity enables identifying slow rules, regression detection, and performance optimization

> "Always collect, but we can show the user the top ten with -v and maybe all with -vv." — user decision

## Consequences

### Positive
- Machine-readable diagnostics
- No log parsing required
- Available to all consumers (CLI, UI, CI)
- Per-rule performance visibility

### Negative
- Proto contract changes
- Always-collect overhead (minimal)

## Implementation Notes

### Proto Definition

```protobuf
message RuleTiming {
  string rule_id = 1;
  double elapsed_ms = 2;
  int32 violation_count = 3;
}

message ValidatorDiagnostics {
  string validator_name = 1;
  double total_elapsed_ms = 2;
  int32 files_processed = 3;
  int32 total_violations = 4;
  repeated RuleTiming rule_timings = 5;
  map<string, string> metadata = 6;
}

message ScanDiagnostics {
  double engine_parse_ms = 1;
  double engine_annotate_ms = 2;
  double engine_hierarchy_ms = 3;
  repeated ValidatorDiagnostics validators = 4;
  double total_elapsed_ms = 5;
}

message ScanResponse {
  repeated Violation violations = 1;
  ScanDiagnostics diagnostics = 2;
}
```

### CLI Display

```bash
# Default: violations only
apme scan playbook.yml

# -v: summaries + top 10 slowest
apme scan -v playbook.yml
# Output:
# Validators:
#   native: 245ms, 12 violations
#   opa: 89ms, 5 violations
# Top 10 slowest rules:
#   L002 (fqcn): 45ms
#   L015 (no-changed-when): 32ms
#   ...

# -vv: full breakdown
apme scan -vv playbook.yml
# Output:
# Engine phases:
#   parse: 120ms
#   annotate: 80ms
#   hierarchy: 45ms
# Validator: native (245ms)
#   L002: 45ms, 3 violations
#   L003: 12ms, 0 violations
#   ...
```

## Related Decisions

- ADR-001: gRPC communication (diagnostics in response)
- ADR-007: Async gRPC servers (timing collection)
