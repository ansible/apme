# REQ-002: Design

## Architecture

See [architecture.md](../../context/architecture.md) for system design.
See [design-remediation.md](../../context/design-remediation.md) for detailed remediation engine design.

## Implementation Approach

Implemented as a two-pass remediation engine (ADR-036) within the Primary service. The engine consumes scan violations and produces file patches through a three-tier classification model.

## Key Components

- **TransformRegistry** (`src/apme_engine/remediation/registry.py`) — Maps rule IDs to deterministic fix functions
- **Convergence Loop** (`src/apme_engine/remediation/engine.py`) — Scan-transform-rescan with oscillation detection
- **Finding Partition** (`src/apme_engine/remediation/partition.py`) — Routes violations to Tier 1/2/3
- **AI Escalation** (`src/apme_engine/remediation/engine.py:_escalate_tier2*`, `src/apme_engine/remediation/abbenay_provider.py`) — Tier 2 orchestration and Abbenay gRPC-backed provider
- **Transform Functions** (`src/apme_engine/remediation/transforms/`) — Per-rule deterministic fix implementations

## Key ADRs

- [ADR-009: Remediation Engine](../../adrs/ADR-009-remediation-engine.md) — Validators read-only, remediation separate
- [ADR-036: Two-Pass Remediation Engine](../../adrs/ADR-036-two-pass-remediation-engine.md) — Format pre-pass + violation-driven transforms
- [ADR-025: AI Provider Protocol](../../adrs/ADR-025-ai-provider-protocol.md) — AI provider abstraction
