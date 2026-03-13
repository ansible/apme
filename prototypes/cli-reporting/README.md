# CLI Reporting Prototypes

**TASK**: TASK-001 - CLI Reporting Options Research

## Contents

| File | Description |
|------|-------------|
| `output_formatter.py` | **Complete implementation reference** — all 4 output formats |
| `rich_terminal.py` | Standalone Rich terminal demo |
| `rich_html_export.py` | Standalone HTML export demo |
| `demo_report.html` | Generated HTML report example |
| `sample_output.html` | Alternative HTML example |

## Running the Demos

```bash
cd /path/to/aap-apme

# Run the complete formatter demo (shows all 4 formats)
uv run python prototypes/cli-reporting/output_formatter.py

# Run individual demos
uv run python prototypes/cli-reporting/rich_terminal.py
uv run python prototypes/cli-reporting/rich_html_export.py
```

## Output Format Examples

### 1. Rich Terminal (default)
```
apme scan .
```
- Color-coded severity badges
- Summary panel with pass/fail status
- Issues table with rule ID, severity, message, location
- Tree view grouped by file

### 2. JSON (automation)
```
apme scan . --json
apme scan . --json > results.json
```
- Machine-readable format
- Includes scan metadata, summary, and violations array
- Exit code: 0 (pass) or 1 (errors found)

### 3. JUnit XML (CI/CD)
```
apme scan . --junit results.xml
```
- Compatible with Jenkins, GitHub Actions, GitLab CI
- Errors = failures, Warnings = skipped, Hints = passing
- One testsuite per file

### 4. HTML Report (sharing)
```
apme scan . --html report.html
```
- Standalone HTML file (no server needed)
- Same formatting as terminal via Rich's save_html()
- Can be emailed, archived, or viewed offline

## Implementation Reference

The `output_formatter.py` file contains:
- Data models matching the protobuf definitions
- All 4 format implementations
- CLI integration example for Typer
- Sample data for testing

Copy and adapt for `src/apme/cli/formatter.py`.

## Key Findings

1. **Rich is already a dependency** — No new packages needed
2. **`Console.save_html()` works well** — Preserves formatting in browser
3. **Unified codebase** — Same rendering logic for terminal and HTML
4. **JUnit XML** — Standard library only (xml.etree.ElementTree)

## Recommendation

Use Rich for all CLI output with format flags:
- `--format rich` (default)
- `--format json` / `--json`
- `--format junit` / `--junit FILE`
- `--format html` / `--html FILE`

See `.sdlc/research/cli-reporting-options.md` for full analysis.
