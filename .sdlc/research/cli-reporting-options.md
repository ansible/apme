# CLI Reporting Options Research

**TASK**: TASK-001
**Status**: In Progress
**Date**: 2026-03-13

## Objective

Evaluate lightweight, config-only reporting options for APME CLI output. The goal is to provide users with rich visualization of scan results without requiring a full dashboard server.

## Evaluation Criteria

| Criterion | Weight | Description |
|-----------|--------|-------------|
| Zero-config | High | Works out of the box with minimal setup |
| CLI integration | High | Consumes CLI output directly (JSON/text) |
| Lightweight | Medium | Minimal dependencies, fast startup |
| No auth required | High | Single-user, no login needed |
| Simple deployment | High | pip install or single binary |
| Offline capable | Medium | Works without network access |

## Current Stack Context

From `pyproject.toml`:
- **Already included**: `typer`, `rich` (terminal formatting)
- **Optional extra**: `streamlit`, `pandas`, `plotly` (dashboard)
- **Output formats**: JSON, JUnit, Text (per REQ-001)

---

## Options Evaluated

### Option 1: Rich (Terminal Tables & Panels)

**Description**: Use Rich library (already a dependency) for formatted terminal output with tables, trees, and panels.

**Implementation**:
```python
from rich.console import Console
from rich.table import Table

console = Console()
table = Table(title="Scan Results")
table.add_column("Rule", style="cyan")
table.add_column("Severity", style="magenta")
table.add_column("File:Line")
table.add_row("L001", "Warning", "playbook.yml:15")
console.print(table)
```

**Evaluation**:
| Criterion | Score | Notes |
|-----------|-------|-------|
| Zero-config | 5/5 | Already installed |
| CLI integration | 5/5 | Native Python, reads data directly |
| Lightweight | 5/5 | Already a dependency |
| No auth | 5/5 | Terminal only |
| Simple deployment | 5/5 | Part of base install |
| Offline | 5/5 | No network needed |

**Pros**:
- Zero additional dependencies
- Beautiful terminal output
- Progress bars, spinners, trees
- Export to HTML possible via `console.save_html()`

**Cons**:
- Terminal only (no browser view)
- Limited interactivity
- No historical comparison

**Verdict**: **Recommended for default CLI output**

---

### Option 2: Textual (Terminal UI Application)

**Description**: Build a TUI (Text User Interface) dashboard using Textual, from the same author as Rich.

**Implementation**:
```python
from textual.app import App
from textual.widgets import DataTable, Header, Footer

class ScanResultsApp(App):
    def compose(self):
        yield Header()
        yield DataTable()
        yield Footer()
```

**Evaluation**:
| Criterion | Score | Notes |
|-----------|-------|-------|
| Zero-config | 4/5 | Requires `pip install textual` |
| CLI integration | 4/5 | Can read JSON files |
| Lightweight | 4/5 | ~2MB, fast startup |
| No auth | 5/5 | Terminal only |
| Simple deployment | 4/5 | Single pip install |
| Offline | 5/5 | No network needed |

**Pros**:
- Interactive navigation (keyboard/mouse)
- Runs in terminal (SSH-friendly)
- Can display multiple scan results
- Filtering and sorting built-in

**Cons**:
- Additional dependency (~2MB)
- Learning curve for TUI patterns
- Still terminal-bound

**Verdict**: **Good for power users who want interactivity**

---

### Option 3: Static HTML Report

**Description**: Generate a standalone HTML file with embedded CSS/JS that can be opened in any browser.

**Implementation**:
```python
from jinja2 import Template

HTML_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <style>/* embedded CSS */</style>
</head>
<body>
    <h1>APME Scan Report</h1>
    <table>{% for issue in issues %}...{% endfor %}</table>
    <script>/* sorting/filtering JS */</script>
</body>
</html>
"""
```

**Evaluation**:
| Criterion | Score | Notes |
|-----------|-------|-------|
| Zero-config | 4/5 | Jinja2 likely needed |
| CLI integration | 5/5 | `apme scan --html report.html` |
| Lightweight | 5/5 | Just templates |
| No auth | 5/5 | Local file |
| Simple deployment | 5/5 | Opens in any browser |
| Offline | 5/5 | Self-contained |

**Pros**:
- Shareable (email, Slack, archive)
- Rich formatting (charts, colors)
- Works on any device with a browser
- Can include interactive filtering (JS)

**Cons**:
- Requires template maintenance
- No real-time updates
- Separate from terminal workflow

**Verdict**: **Recommended for shareable reports**

---

### Option 4: JSON + External Viewer

**Description**: Output JSON and let users view with existing tools (jq, fx, jless, or browser extensions).

**Implementation**:
```bash
apme scan . --json | jq '.issues[] | select(.severity == "error")'
apme scan . --json > results.json && open results.json  # macOS JSON viewer
```

**Evaluation**:
| Criterion | Score | Notes |
|-----------|-------|-------|
| Zero-config | 5/5 | JSON output already planned |
| CLI integration | 5/5 | Pipe to any tool |
| Lightweight | 5/5 | No dependencies |
| No auth | 5/5 | Local files |
| Simple deployment | 5/5 | Nothing to install |
| Offline | 5/5 | Files only |

**Pros**:
- Maximum flexibility
- Unix philosophy (composability)
- User chooses their viewer
- CI/CD friendly

**Cons**:
- Requires user to know tools
- No built-in visualization
- Raw data, not presentation

**Verdict**: **Essential baseline, not sufficient alone**

---

### Option 5: Rich + HTML Export (Hybrid)

**Description**: Use Rich for terminal display AND export to HTML using Rich's built-in `Console.save_html()`.

**Implementation**:
```python
from rich.console import Console

console = Console(record=True)
# ... render tables, panels, etc ...
console.save_html("report.html")
```

**Evaluation**:
| Criterion | Score | Notes |
|-----------|-------|-------|
| Zero-config | 5/5 | Already have Rich |
| CLI integration | 5/5 | Native |
| Lightweight | 5/5 | No new deps |
| No auth | 5/5 | Local files |
| Simple deployment | 5/5 | Built-in |
| Offline | 5/5 | Self-contained HTML |

**Pros**:
- Single codebase for terminal + HTML
- Preserves Rich formatting in HTML
- No template maintenance
- Zero new dependencies

**Cons**:
- HTML is "screenshot" of terminal (not semantic)
- Limited interactivity in HTML output
- Styling tied to Rich's terminal theme

**Verdict**: **Best balance of effort vs. value**

---

## Comparison Matrix

| Option | Zero-config | Integration | Lightweight | Shareability | Interactivity |
|--------|-------------|-------------|-------------|--------------|---------------|
| Rich (terminal) | 5 | 5 | 5 | 2 | 2 |
| Textual (TUI) | 4 | 4 | 4 | 2 | 5 |
| Static HTML | 4 | 5 | 5 | 5 | 3 |
| JSON + viewer | 5 | 5 | 5 | 3 | 2 |
| **Rich + HTML** | **5** | **5** | **5** | **4** | **2** |

---

## Recommendation

### Primary: Rich + HTML Export (Option 5)

**Rationale**:
1. **Zero new dependencies** - Rich is already installed
2. **Unified codebase** - Same rendering for terminal and HTML
3. **Immediate value** - Works today, no new packages
4. **Shareable** - HTML files can be emailed/archived
5. **Progressive enhancement** - Can add Textual later for power users

### Implementation Plan

```
apme scan .                    # Rich terminal output (default)
apme scan . --json             # JSON for automation
apme scan . --junit            # JUnit XML for CI
apme scan . --html report.html # Rich HTML export
```

### Future Enhancements (if needed)

| Phase | Feature | Dependency |
|-------|---------|------------|
| v1.0 | Rich terminal + HTML export | None (existing) |
| v1.1 | Interactive TUI viewer | textual |
| v2.0 | Full dashboard | streamlit (optional extra) |

---

## Prototypes

See `/prototypes/cli-reporting/` for working examples:

| File | Purpose |
|------|---------|
| `output_formatter.py` | **Complete implementation reference** — formatter module with all 4 formats |
| `rich_terminal.py` | Standalone terminal demo |
| `rich_html_export.py` | HTML export demo |
| `demo_report.html` | Generated HTML report example |

Run the formatter demo to see all output formats:
```bash
uv run python prototypes/cli-reporting/output_formatter.py
```

---

## Implementation Requirements

### What Needs to Be Added to the Scanner

| Component | Location | Purpose |
|-----------|----------|---------|
| `OutputFormat` enum | `src/apme/cli/formats.py` | Define supported output formats |
| `format_output()` | `src/apme/cli/formatter.py` | Main entry point for formatting |
| `format_rich()` | `src/apme/cli/formatter.py` | Rich terminal output |
| `format_json()` | `src/apme/cli/formatter.py` | JSON output |
| `format_junit()` | `src/apme/cli/formatter.py` | JUnit XML output |
| `format_html()` | `src/apme/cli/formatter.py` | HTML export via Rich |

### Dependencies

**Already included** (no changes to `pyproject.toml`):
- `rich>=13.0.0` — Terminal formatting + HTML export
- `typer>=0.9.0` — CLI framework

**Standard library only**:
- `json` — JSON serialization
- `xml.etree.ElementTree` — JUnit XML generation

### CLI Flags

```
apme scan [OPTIONS] [PATH]

Options:
  -f, --format [rich|json|junit|html]  Output format [default: rich]
  -o, --output PATH                     Output file (required for HTML)
  --html PATH                           Shortcut for --format html --output PATH
  --json                                Shortcut for --format json (stdout)
  --junit PATH                          Shortcut for --format junit --output PATH
```

### Integration Points

```
┌─────────────────────────────────────────────────────────────────┐
│ CLI (Typer)                                                     │
│   ├── scan command receives flags                               │
│   ├── Calls Primary.Scan() via gRPC                            │
│   ├── Receives ScanResponse with violations                    │
│   └── Calls format_output(result, format, output)              │
└─────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Formatter (this module)                                         │
│   ├── format_rich()  → Console output                          │
│   ├── format_json()  → stdout or file                          │
│   ├── format_junit() → stdout or file                          │
│   └── format_html()  → file (uses Rich's save_html)            │
└─────────────────────────────────────────────────────────────────┘
```

---

## Example Outputs

### Rich Terminal (default)
```
apme scan .
```
![Rich terminal output with tables, panels, and tree view]

### JSON
```
apme scan . --json
```
```json
{
  "version": "1.0",
  "scan": { "project": ".", "files_scanned": 23, "scan_time_ms": 847.3 },
  "summary": { "total": 9, "errors": 2, "warnings": 5, "hints": 2, "passed": false },
  "violations": [ { "rule_id": "L001", "level": "error", ... } ]
}
```

### JUnit XML
```
apme scan . --junit results.xml
```
```xml
<?xml version='1.0' encoding='utf-8'?>
<testsuites name="APME Scan" tests="9" failures="2" errors="0" time="0.847">
  <testsuite name="playbook.yml" tests="5" failures="2">
    <testcase name="L001 at line 15" classname="playbook">
      <failure type="L001">Module 'apt' should use FQCN</failure>
    </testcase>
  </testsuite>
</testsuites>
```

### HTML Report
```
apme scan . --html report.html
```
Generates a standalone HTML file viewable in any browser with the same rich formatting as the terminal.

---

## Decision Needed

This research supports **Option A (file-based)** from DR-008 (Scan Result Persistence):
- CLI outputs JSON/HTML files
- Users manage their own file storage
- No database required for v1

**Recommend closing this task and creating ADR for CLI output formats.**
