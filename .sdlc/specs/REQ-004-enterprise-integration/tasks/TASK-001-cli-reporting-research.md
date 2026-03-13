# TASK-001: CLI Reporting Options Research

## Parent Requirement

REQ-004: Enterprise Integration

## Status

Complete

## Description

Research spike for config-only reporting options for the CLI. Evaluate lightweight dashboard/reporting tools that integrate with CLI output. Produce PoC and recommendation.

## Prerequisites

- [ ] None (research task)

## Implementation Notes

1. **Define evaluation criteria**
   - Zero-config or minimal config setup
   - Works directly with CLI output
   - Lightweight and standalone
   - No authentication required
   - Simple deployment (single binary or pip install)

2. **Evaluate candidates**
   - **Rich**: Terminal-based tables and formatting
   - **Textual**: TUI dashboards in terminal
   - **Static HTML**: Generate standalone HTML reports
   - **CSV/JSON + viewer**: Simple file-based reporting

3. **Build proof-of-concept dashboards**
   - Display scan results from CLI
   - Config-only setup (no code changes to use)
   - Generate reports that can be shared/viewed offline

4. **Document findings**
   - Pros/cons for each approach
   - Integration complexity
   - Recommendation with rationale

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `.sdlc/research/cli-reporting-options.md` | Create | Research findings document |
| `prototypes/cli-reporting/` | Create | PoC implementations |

## Deliverables

| Deliverable | Description |
|-------------|-------------|
| Config-only reporting/dash | Evaluated options for CLI reporting |
| PoC code | Working prototype(s) |
| Recommendation | Final choice with rationale |
| DR or ADR | Decision record if architectural |

## Verification

Before marking complete:

- [x] Multiple reporting options evaluated (5 options in research doc)
- [x] PoC demonstrates config-only setup (Rich already a dependency)
- [x] Works standalone with CLI output (terminal + HTML export)
- [x] Recommendation documented with rationale (Rich + HTML Export)
- [x] DR/ADR created if architectural decision needed (No new ADR needed - uses existing deps)

## Acceptance Criteria Reference

From REQ-004:
- [ ] CLI tooling outputs results in usable format
- [ ] Results can be displayed/shared

## Constraints

- **No authentication**: Just works with CLI, no auth layer
- **Standalone**: Self-contained, no server dependencies
- **Lightweight**: Minimal tooling, easy to install and use
- **No concurrent users**: Single-user CLI tool usage

---

## Completion Checklist

- [x] Research complete (2026-03-13)
- [x] Deliverables produced
- [x] Status updated to Complete
- [ ] Committed with message: `Implements TASK-001: CLI reporting options research`

## Results Summary

**Recommendation**: Use Rich + HTML Export (already a dependency)

**Deliverables**:
- `.sdlc/research/cli-reporting-options.md` - Full evaluation of 5 options + implementation guide
- `prototypes/cli-reporting/output_formatter.py` - **Complete implementation reference** (all 4 formats)
- `prototypes/cli-reporting/rich_terminal.py` - Terminal output demo
- `prototypes/cli-reporting/rich_html_export.py` - HTML export demo
- `prototypes/cli-reporting/demo_report.html` - Generated HTML example
- `prototypes/cli-reporting/README.md` - Usage instructions

**Key Finding**: No new dependencies needed. Rich's `Console.save_html()` provides shareable HTML reports with zero additional packages.

## Re-Running This Research

To regenerate example outputs or test changes:
```bash
uv run python prototypes/cli-reporting/output_formatter.py
```

This produces sample output for all 4 formats:
- `apme scan .` (Rich terminal)
- `apme scan . --json` (JSON)
- `apme scan . --junit` (JUnit XML)
- `apme scan . --html report.html` (HTML)
