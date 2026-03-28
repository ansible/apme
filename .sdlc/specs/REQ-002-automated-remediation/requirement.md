# REQ-002: Automated Remediation

## Metadata

- **Phase**: PHASE-002 - Rewrite Engine
- **Status**: Implemented
- **Created**: 2026-03-12
- **Updated**: 2026-03-25

## Overview

Automated "Rewrite" feature that applies safe fixes for renamed parameters, module redirects, and basic syntax changes.

## User Stories

**As a DevOps Engineer**, I want auto-fixes applied to simple issues so that I can focus on complex problems.

**As a Code Owner**, I want to see diffs before changes are committed so that I can review what will change.

**As a CI Pipeline**, I want iterative processing so that nested issues are uncovered progressively.

## Acceptance Criteria

### Safe Auto-Fixes
- [x] GIVEN a playbook with renamed parameters
- [x] WHEN rewrite runs
- [x] THEN parameters are updated to current names

### Module Redirects
- [x] GIVEN deprecated module references
- [x] WHEN rewrite runs
- [x] THEN modules are replaced with current equivalents

### Iterative Processing
- [x] GIVEN a playbook with nested issues
- [x] WHEN multiple rewrite passes run
- [x] THEN each pass uncovers issues hidden by previous fixes

### Diff Generation
- [x] GIVEN any rewrite operation
- [x] WHEN changes are proposed
- [x] THEN a before/after diff is shown for approval

## Dependencies

- REQ-001: Core Scanning Engine
- TransformRegistry (ADR-009)
- Two-pass remediation engine (ADR-036)

## Implementation Notes

Implemented by Brad (cidrblock) across multiple PRs. Key implementation details:

- **Two-pass remediation engine** (ADR-036): Format pre-pass followed by violation-driven transform pipeline
- **Three-tier finding classification**: Tier 1 (deterministic transforms), Tier 2 (AI-proposable via Abbenay), Tier 3 (manual review)
- **Transform Registry**: Rule-specific deterministic fix functions in `src/apme_engine/remediation/transforms/`
- **AI escalation**: Abbenay AI container (:50057) for Tier 2 proposals with confidence scoring
- **Unit-level AI proposals**: One proposal per unit for independent user approval, decoupled from line numbers using snippet replacement
- **Convergence loop**: Scan-transform-rescan with oscillation detection (max N passes)
- **Progress streaming**: Bidirectional gRPC (FixSession) for engine progress; WebSocket bridge in Gateway for UI
- **CLI flags**: `--ai` (enable Tier 2 escalation), `--auto-approve` (non-interactive CI mode)

See [design-remediation.md](../../context/design-remediation.md) for full architecture.
