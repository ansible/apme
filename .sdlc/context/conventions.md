# APME Coding Conventions

## Python Standards

### Version

- Python 3.11+ required
- Use modern syntax (match statements, type unions with `|`)

### Style

- **Formatter**: Black with 88-char line limit
- **Linter**: Ruff
- **Type Checker**: mypy (strict mode)

### Imports

```python
# Standard library
from dataclasses import dataclass
from pathlib import Path

# Third-party
import typer
from ruamel.yaml import YAML

# Local
from apme.scanner import AriWrapper
from apme.rules import FQCNMapper
```

Order: stdlib → third-party → local, alphabetized within groups.

### Type Hints

Required for all function signatures:

```python
def scan_playbook(
    path: Path,
    *,
    fix: bool = False,
    output_format: OutputFormat = OutputFormat.JSON,
) -> ScanResult:
    ...
```

### Docstrings

Google style for public APIs:

```python
def apply_fqcn_fix(module_name: str, line: int) -> FixResult:
    """Apply FQCN fix to a module reference.

    Args:
        module_name: The short module name (e.g., "copy").
        line: Line number where the module is used.

    Returns:
        FixResult containing the applied transformation.

    Raises:
        UnknownModuleError: If module has no FQCN mapping.
    """
```

### Error Handling

```python
# Define custom exceptions
class APMEError(Exception):
    """Base exception for APME."""

class ScanError(APMEError):
    """Error during playbook scanning."""

class TransformError(APMEError):
    """Error during YAML transformation."""

# Use specific exceptions
try:
    result = scanner.scan(path)
except AriNotFoundError:
    raise ScanError(f"ARI not available: {path}")
```

### Logging

```python
import structlog

logger = structlog.get_logger(__name__)

# Use structured logging
logger.info("scan_started", path=str(path), fix_mode=fix)
logger.warning("issue_detected", issue_type="FQCN", line=42)
logger.error("scan_failed", path=str(path), error=str(e))
```

## File Organization

### Source Structure

```
src/apme/
├── __init__.py          # Version, public API
├── cli.py               # Typer CLI
├── scanner/
│   ├── __init__.py      # Public scanner API
│   ├── ari_wrapper.py   # ARI integration
│   ├── issue_types.py   # Issue enums and classes
│   └── reporter.py      # Output formatting
├── rewriter/
│   ├── __init__.py
│   ├── graph.py         # LangGraph definition
│   ├── transforms.py    # Transformation functions
│   └── validators.py    # Validation logic
└── rules/
    ├── __init__.py
    ├── fqcn.py          # FQCN mappings
    ├── deprecated.py    # Deprecated modules
    └── syntax.py        # Syntax patterns
```

### Test Structure

Mirror source structure:

```
tests/
├── conftest.py          # Shared fixtures
├── test_cli.py
├── scanner/
│   ├── test_ari_wrapper.py
│   └── test_issue_types.py
├── rewriter/
│   └── test_transforms.py
└── fixtures/
    ├── playbooks/       # Test playbook files
    └── expected/        # Expected outputs
```

## Naming Conventions

### Files

- `snake_case.py` for all Python files
- `kebab-case.md` for documentation
- `UPPER_CASE.md` for root docs (README, CLAUDE, AGENTS)

### Classes

```python
class AriWrapper:        # PascalCase
class FQCNMapper:        # Acronyms as words
class ScanResult:        # Nouns for data classes
class PlaybookScanner:   # Noun for service classes
```

### Functions

```python
def scan_playbook():     # verb_noun
def apply_fix():         # verb_noun
def is_fixable():        # is_adjective for booleans
def has_issues():        # has_noun for booleans
def get_severity():      # get_noun for getters
```

### Variables

```python
playbook_path: Path      # Descriptive snake_case
scan_result: ScanResult  # Type-matching names
issues: list[Issue]      # Plural for collections
is_valid: bool           # is_ prefix for booleans
```

### Constants

```python
DEFAULT_OUTPUT_FORMAT = OutputFormat.JSON
MAX_BATCH_SIZE = 100
FQCN_PATTERN = re.compile(r"...")
```

## Git Conventions

### Branch Names

```
feature/REQ-001-scanner
fix/TASK-003-fqcn-detection
docs/update-readme
```

### Commit Messages

```
Implements TASK-001: Add ARI wrapper with subprocess integration

- Add AriWrapper class for ARI integration
- Handle JSON output parsing
- Add error handling for missing ARI

Co-Authored-By: Claude Opus 4.5 <noreply@anthropic.com>
```

### PR Template

```markdown
## Summary
Brief description of changes.

## Related Specs
- REQ-001: Scanner Module
- TASK-001: ARI Wrapper

## Testing
- [ ] Unit tests pass
- [ ] Integration test with sample playbook
- [ ] Manual verification

## Checklist
- [ ] Code follows conventions
- [ ] Tests added/updated
- [ ] Documentation updated
```

## Testing Standards

### Unit Tests

```python
def test_scan_detects_fqcn_issues():
    """Scan should detect modules missing FQCN."""
    playbook = create_playbook_with_short_module("copy")

    result = scanner.scan(playbook)

    assert len(result.issues) == 1
    assert result.issues[0].type == IssueType.FQCN
```

### Fixtures

```python
@pytest.fixture
def sample_playbook(tmp_path: Path) -> Path:
    """Create a sample playbook for testing."""
    content = """
    - hosts: all
      tasks:
        - copy:
            src: /tmp/foo
            dest: /tmp/bar
    """
    path = tmp_path / "playbook.yml"
    path.write_text(content)
    return path
```

### Assertions

- Use plain `assert` statements
- One assertion per test when possible
- Descriptive test names that document behavior

## YAML Handling

Use `ruamel.yaml` to preserve comments:

```python
from ruamel.yaml import YAML

yaml = YAML()
yaml.preserve_quotes = True

# Load
with open(path) as f:
    data = yaml.load(f)

# Modify in place

# Save (preserves formatting)
with open(path, "w") as f:
    yaml.dump(data, f)
```

## CLI Standards

### Typer Patterns

```python
import typer

app = typer.Typer(help="APME - Ansible Playbook Modernization Engine")

@app.command()
def scan(
    path: Path = typer.Argument(..., help="Path to playbook or directory"),
    fix: bool = typer.Option(False, "--fix", help="Auto-fix issues"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Output file"),
):
    """Scan playbooks for AAP compatibility issues."""
```

### Output Formatting

```python
from rich.console import Console
from rich.table import Table

console = Console()

# Success
console.print("[green]Scan complete[/green]")

# Warning
console.print("[yellow]Warning:[/yellow] 3 issues found")

# Error
console.print("[red]Error:[/red] File not found")
```

## Visualization Selection

When representing data or relationships in documentation or reports:

| Relationship Type | Use | CLI Representation |
|-------------------|-----|-------------------|
| Hierarchical (parent → child) | Tree diagram | ASCII tree with boxes |
| Sequential (step → step) | Flowchart | Numbered steps or arrows |
| Many-to-many | Force-directed | Indented hierarchy |
| Quantities/flow | Sankey | Flow arrows with counts |
| Comparisons | Matrix | Table with symbols |

### ASCII Diagram Examples

```
# Tree (hierarchical)
├── parent
│   ├── child-1
│   └── child-2

# Flow (sequential)
Step 1 ──> Step 2 ──> Step 3

# Box diagram (components)
┌──────────┐     ┌──────────┐
│  Source  │ ──> │  Target  │
└──────────┘     └──────────┘
```
