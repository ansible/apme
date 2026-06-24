# APME Coding Conventions

## Python Standards

### Version

- Python 3.10+ required (`requires-python = ">=3.10"`)
- Use modern syntax (match statements, type unions with `|`)

### Style

- **Linter & Formatter**: Ruff (120-char line limit)
- **Type Checker**: mypy (strict mode)

### Imports

```python
# Standard library
from dataclasses import dataclass
from pathlib import Path

# Third-party
from ruamel.yaml import YAML

# Local
from apme_engine.runner import run_scan
from apme_engine.validators.base import ScanContext
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

### Docstrings (Google style)

Google style for all public modules, classes, and functions. Enforced by Ruff (D rules, convention = google) and pydoclint via prek. Do not put type hints in docstrings; types belong in signatures only. Include Args, Returns, Raises (and Yields where applicable). For classes with instance attributes (e.g. dataclasses), include an **Attributes** section listing and describing each attribute. Blank line after the last section before closing `"""`.

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
import logging

logger = logging.getLogger(__name__)

# Use standard logging with context
logger.info("Engine: loader start (%s, type=%s)", path.name, scan_type)
logger.warning("Validator %s returned empty result", name)
logger.error("Scan failed for %s: %s", path, e)
```

## File Organization

### Source Structure

```
src/
├── apme/v1/                    # Generated proto stubs (NEVER edit by hand)
├── apme_engine/                # Core product
│   ├── cli/                    # argparse-based CLI (check, remediate, format, daemon, health-check)
│   ├── daemon/                 # gRPC servers: primary, native, opa, ansible, gitleaks, etc.
│   │   └── sinks/             # Event sinks (grpc_reporting)
│   ├── engine/                 # Project loader: parser, models, content_graph, scanner
│   ├── remediation/            # Convergence engine, transform registry
│   │   └── transforms/        # Per-rule deterministic fix functions
│   ├── validators/             # Rule implementations
│   │   ├── native/rules/      # ~90 GraphRule subclasses
│   │   ├── opa/bundle/        # ~50 Rego rule files
│   │   ├── ansible/rules/     # L057–L059, M001–M004
│   │   ├── gitleaks/          # Gitleaks binary wrapper
│   │   ├── collection_health/ # Installed collection quality checks
│   │   └── dep_audit/         # pip-audit CVE scanner
│   ├── venv_manager/          # Session-scoped venvs (VenvSessionManager)
│   └── runner.py              # run_scan() entry point
├── apme_gateway/               # FastAPI REST + SQLAlchemy DB + Reporting gRPC server
│   ├── api/                   # REST routers and schemas
│   ├── db/                    # SQLAlchemy models and queries
│   ├── grpc_reporting/        # ReportingServicer (engine event sink)
│   └── scm/                   # SCM integrations (GitHub)
└── galaxy_proxy/               # PEP 503 proxy (Galaxy → wheels)
    └── proxy/                 # ASGI server, cache layer
```

### Test Structure

Tests mirror the engine structure:

```
tests/
├── conftest.py              # Shared fixtures (tmp projects, mock validators)
├── unit/                    # Unit tests (run via tox -e unit)
│   ├── test_runner.py
│   ├── test_formatter.py
│   ├── test_remediation/
│   ├── test_validators/
│   └── test_cli/
├── integration/             # Integration tests (marked, may need services)
└── fixtures/
    ├── projects/            # Sample Ansible projects for scan testing
    └── golden-collection/   # Collection fixture for validator tests
```

## Naming Conventions

### Files

- `snake_case.py` for all Python files
- `kebab-case.md` for documentation
- `UPPER_CASE.md` for root docs (README, CLAUDE, AGENTS)

### Classes

```python
class ScanContext:            # PascalCase, nouns for data classes
class ContentGraph:           # PascalCase
class OpaValidator:           # Noun for service classes
class TransformRegistry:      # Noun for service classes
class AnsibleProjectLoader:   # Descriptive noun phrase
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

### argparse Patterns

The CLI uses standard library `argparse` with subparsers. Each subcommand
has its own module in `src/apme_engine/cli/`.

```python
import argparse

def build_parser() -> argparse.ArgumentParser:
    """Build and return the CLI argument parser."""
    parser = argparse.ArgumentParser(
        description="APME: Ansible Policy & Modernization Engine",
    )
    sub = parser.add_subparsers(dest="command")

    # check subcommand
    check_p = sub.add_parser("check", help="Assess project (read-only)")
    check_p.add_argument("target", help="Path to playbook or directory")
    check_p.add_argument("--json", action="store_true", help="JSON output")
    check_p.add_argument("--sarif", action="store_true", help="SARIF output")

    # remediate subcommand
    rem_p = sub.add_parser("remediate", help="Apply fixes via FixSession")
    rem_p.add_argument("target", help="Path to playbook or directory")
    rem_p.add_argument("--ai", action="store_true", help="Enable Tier 2 AI remediation")

    return parser
```

### Output Formatting

The CLI uses a custom ANSI module (`cli/ansi.py`) for colored output,
respecting `--no-ansi` and `NO_COLOR` environment variable:

```python
from apme_engine.cli.ansi import style, Color

# Success
print(style("Check complete", fg=Color.GREEN))

# Warning
print(style("Warning:", fg=Color.YELLOW), "3 issues found")

# Error  
print(style("Error:", fg=Color.RED), "File not found")
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
