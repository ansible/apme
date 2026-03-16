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
- [x] PoC demonstrates config-only setup (Rich prototype, then internal ANSI)
- [x] Works standalone with CLI output (terminal + HTML export)
- [x] Recommendation documented with rationale (Rich initially, then internal ANSI)
- [x] ADR created for architectural decision (ADR-014: CLI Output Formats)

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

**Initial Recommendation**: Use Rich + HTML Export (already a dependency)

**Final Decision (ADR-014)**: Use **internal zero-dependency ANSI module** instead of Rich

**Rationale for change**:
- Rich + dependencies = ~1.6 MB (rich + pygments + markdown-it-py + mdurl)
- Internal ANSI module = ~200 lines, single file, fully typed
- Feature surface is tiny (8 colors, badges, boxes, tables, tree chars)
- Rich would add 30K+ lines of code, 95% unused
- Internal module provides pixel-perfect control over badge styling

**Deliverables**:
- `.sdlc/research/cli-reporting-options.md` - Full evaluation of 5 options + implementation guide
- `prototypes/cli-reporting/output_formatter.py` - Rich-based reference (superseded)
- `src/apme_engine/ansi.py` - **Final implementation** (zero-dependency ANSI styling)
- `tests/test_ansi.py` - 45 unit tests for ANSI module

**Implementation** (ADR-014):
| Format | Flag | Implementation |
|--------|------|----------------|
| ANSI terminal | `--format rich` (default) | `src/apme_engine/ansi.py` |
| JSON | `--format json` / `--json` | stdlib `json` |
| JUnit XML | `--format junit` | stdlib `xml.etree` |
| HTML | `--format html` | ANSI-to-HTML conversion |

**Key Finding**: Zero new dependencies achieved. Internal ANSI module provides all needed terminal styling with NO_COLOR/FORCE_COLOR support (no-color.org compliant).

## Testing the Implementation

To test the ANSI styling module:
```bash
uv run pytest tests/test_ansi.py -v
```

To see the CLI output formats in action:
```bash
# ANSI terminal output (default)
apme-scan scan .

# JSON output
apme-scan scan . --json

# With diagnostics
apme-scan scan . -v --primary-addr localhost:50051
```

**Related PRs**:
- PR #17: ADR-014 and SDLC documentation
- PR #18: `src/apme_engine/ansi.py` implementation
- PR #19: Test coverage for CLI health-check and diagnostics
