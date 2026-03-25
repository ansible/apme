# Coding Conventions

**Analysis Date:** 2026-03-25

## Naming Patterns

**Files:**
- `snake_case.py` for all Python files
- `kebab-case.md` for documentation files
- `UPPER_CASE.md` for root documentation (CLAUDE.md, CONTRIBUTING.md, README.md)
- Private modules use `_prefix.py` (e.g., `_models.py`, `_project_root.py`)

**Classes:**
- `PascalCase` for all classes (e.g., `ARIScanner`, `SessionState`, `FormatResult`)
- Acronyms as separate words: `FQCNMapper`, `HTTPClient`, `YAMLDict`
- Enum names use PascalCase: `RuleScope`, `RemediationClass`, `LoadType`
- Exception classes end with `Error` or `Exception`: `PlaybookFormatError`, `ResourceExhaustedError`, `ScanError`

**Functions:**
- `snake_case` for all functions
- Predicates use `is_` or `has_` prefix: `is_local_path()`, `has_issues()`
- Getters use `get_` prefix: `get_logger()`, `get_session()`
- Private functions use `_prefix`: `_free_port()`, `_restore_env()`
- Verb-noun pattern: `apply_fix()`, `scan_playbook()`, `resolve_primary()`

**Variables:**
- `snake_case` for all variables
- Type-matching names: `scan_result: ScanResult`, `rules: list[Rule]`
- Plural for collections: `violations: list[Violation]`, `proposals: dict[str, Proposal]`
- Boolean prefixes: `is_valid: bool`, `should_continue: bool`, `has_data: bool`
- File paths use `Path` type: `playbook_path: Path`, `rules_dir: str`

**Constants:**
- `UPPER_SNAKE_CASE` for module-level constants
- Examples: `DEFAULT_OUTPUT_FORMAT = OutputFormat.JSON`, `MAX_BATCH_SIZE = 100`, `_DEFAULT_TTL = 1800`
- Environment variable defaults: `_DEFAULT_TTL = int(os.environ.get("APME_SESSION_TTL", "1800"))`

## Code Style

**Formatting:**
- Tool: Ruff (via `ruff-format`)
- Line length: 120 characters (per pyproject.toml)
- Editor config enforces 4-space indentation for Python files

**Linting:**
- Tool: Ruff
- Configuration: `pyproject.toml` [tool.ruff] section
- Rules enabled: E, F, W, I, UP, B, SIM, D (docstring rules)
- Exception: `src/apme_engine/engine/annotators/ansible.builtin/*.py` skips E501 (line length)
- Pre-commit hook runs: `ruff --fix` and `ruff-format`

**Type Checking:**
- Tool: mypy (strict mode)
- `disallow_any_explicit = true` enforced across most modules
- Exceptions for inherently untyped modules:
  - `galaxy_proxy.*` (Galaxy YAML/JSON parsing)
  - `apme_gateway.*` (SQLAlchemy generic types)
- Protocol imports: `from typing import TYPE_CHECKING, Protocol`

## Import Organization

**Order (enforced by Ruff I rules):**
1. `from __future__ import annotations` (always first if present)
2. Standard library: `import datetime`, `from dataclasses import dataclass`
3. Third-party: `from ruamel.yaml import YAML`, `import grpc`
4. Local: `from apme_engine.scanner import Scanner`

**Within groups:** Alphabetically sorted

**Example from `src/apme_engine/daemon/session.py`:**
```python
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from apme.v1.common_pb2 import ProgressUpdate
from apme.v1.primary_pb2 import (
    FileDiff,
    FilePatch,
    FixOptions,
)

logger = logging.getLogger(__name__)
```

**Module-level logger pattern:**
```python
logger = logging.getLogger(__name__)
```
All modules use Python's standard `logging` module with `__name__` channel.

## Error Handling

**Exception hierarchy:**
- Base: `Exception` (standard Python)
- Specific exceptions per module:
  - `PlaybookFormatError(Exception)` - Malformed playbook YAML
  - `TaskFormatError(Exception)` - Malformed task structure
  - `FatalRuleResultError(Exception)` - Rule evaluation failure
  - `ResourceExhaustedError(Exception)` - Session/resource limits exceeded

**Pattern:**
```python
try:
    result = scanner.scan(path)
except PlaybookFormatError as e:
    raise ScanError(f"Invalid playbook: {path}") from e
except FileNotFoundError as e:
    raise ScanError(f"File not found: {path}") from e
```

- Catch specific exceptions
- Re-raise as domain-specific exceptions when appropriate
- Use `from e` for exception chaining
- Include context in error messages with f-strings

**System exit in CLI:**
```python
if error_condition:
    sys.stderr.write(f"Error: {message}\n")
    sys.exit(1)
```

## Logging

**Framework:** Python standard library `logging` module

**Setup pattern:**
```python
logger = logging.getLogger(__name__)
```

**Logging levels:**
- `logger.debug()` - Detailed diagnostic info
- `logger.info()` - General informational messages
- `logger.warning()` - Warning conditions
- `logger.error()` - Error events
- `logger.exception()` - Exception with traceback

**When to log:**
- Debug: Entering functions, intermediate results, variable states (use sparingly)
- Info: Phase transitions, config initialization, scan start/complete
- Warning: Recoverable issues, deprecated usage, resource constraints
- Error: File not found, invalid input, failed operations

**Format:** Use string interpolation, avoid f-strings inside logging calls (for lazy evaluation):
```python
logger.info("scan_started", extra={"path": str(path), "fix_mode": fix})
logger.error("scan_failed", extra={"error": str(e)})
```

## Comments

**When to comment:**
- Complex logic that isn't self-documenting
- Non-obvious performance decisions
- Workarounds for bugs or platform limitations
- Links to issues, ADRs, or external documentation

**Avoid commenting:**
- Self-evident code (naming should be clear)
- Multiple comment lines for simple operations
- Commented-out code (delete it)

**JSDoc/TSDoc:** Not applicable (Python codebase)

**Docstring style:** Google style (enforced by Ruff D rules and pydoclint)

## Docstrings (Google Style)

**For all public modules, classes, and functions:**

```python
def apply_fix(module_name: str, line: int) -> FixResult:
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

**Class with attributes:**
```python
@dataclass
class ScanResult:
    """Result of a playbook scan.

    Attributes:
        issues: List of detected issues.
        passed_rules: Count of passing rules.
        duration_ms: Scan duration in milliseconds.
    """
    issues: list[Issue] = field(default_factory=list)
    passed_rules: int = 0
    duration_ms: int = 0
```

**Sections (in order):**
1. One-line summary (first line)
2. Multi-line description (optional)
3. Args: Parameter descriptions (omit type hints in docstring)
4. Returns: Return value description
5. Raises: Exceptions that may be raised
6. Yields: For generators
7. Attributes: For dataclasses with instance attributes

**Rules:**
- Type hints belong in function signature only, NOT in docstring
- Blank line after last section before closing `"""`
- One-line docstrings are acceptable for simple functions
- Do not use `@param`, `@return` syntax (Google style only)

## Function Design

**Size guidelines:**
- Aim for functions under 50 lines
- Single responsibility: one task per function
- Prefer early returns for error conditions

**Parameters:**
- Keyword-only arguments for optional parameters: `def scan(path, *, fix=False)`
- Dataclass for multiple related parameters
- Default values in signature: `def foo(count: int = 10) -> None`

**Return values:**
- Consistent return type across all code paths
- Use `None` explicitly for functions with no return value
- Return structured data (dataclass, dict) instead of tuple
- Use `| None` union for optional returns: `def get_session() -> Session | None`

## Module Design

**Exports:**
- Implicit: no `__all__` unless intentionally hiding internal APIs
- Public API modules re-export from submodules:
  ```python
  # src/apme_engine/engine/__init__.py
  from .scanner import ARIScanner, Config
  from .findings import Findings

  __all__ = ["ARIScanner", "Config", "Findings"]
  ```

**Barrel files:**
- Used sparingly for grouping related exports
- Example: `src/apme_engine/__init__.py` for version and main API

**Private modules:**
- Internal utilities start with underscore: `_models.py`, `_helpers.py`
- Not re-exported from package `__init__.py`

## Type Hints

**Required for:**
- All function signatures (parameters and return)
- Module-level variables
- Class attributes (dataclass fields use type hints)

**Modern syntax:**
- Use `|` for unions: `str | int | None` (not `Union[str, int, None]`)
- Use `list[T]` instead of `List[T]` (requires `from __future__ import annotations`)
- Use `dict[K, V]` instead of `Dict[K, V]`

**Example:**
```python
def process_violations(
    violations: list[Violation],
    output_format: str = "json",
) -> dict[str, int]:
    """Process violations by severity level."""
    result: dict[str, int] = {}
    for v in violations:
        result[v.level] = result.get(v.level, 0) + 1
    return result
```

**Protocols and generics:**
```python
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

class Readable(Protocol):
    """Something that can be read as text."""

    def read(self) -> str: ...
```

## Dataclasses

**Pattern:**
```python
@dataclass
class ScanResult:
    """Result of a playbook scan.

    Attributes:
        violations: Detected issues.
        passed_rules: Number of passing rules.
    """
    violations: list[Violation] = field(default_factory=list)
    passed_rules: int = 0
```

- Use `@dataclass` for structured data (not Pydantic models)
- Default factories for mutable defaults
- Always include Attributes docstring section

## Async/Await

**Pattern:**
```python
async def scan_async(path: Path) -> ScanResult:
    """Scan a file asynchronously."""
    result = await engine.evaluate(path)
    return result
```

- Use `async def` for coroutines
- Use `await` for async calls
- Avoid mixing sync and async (prefer all-async for new code)
- For generators: `async def` with `yield` (used in gRPC streaming)

**Example from `src/apme_engine/daemon/session.py`:**
```python
async def __anext__(self) -> SessionCommand:
    """Return next command or raise StopAsyncIteration."""
    val = await self._queue.get()
    if val is None:
        raise StopAsyncIteration
    return val
```

## Regex Patterns

**Inline definition:**
```python
_SAFE_SESSION_RE = __import__("re").compile(r"^[A-Za-z0-9_\-]+$")
```

Or import normally:
```python
import re

FQCN_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*\.[a-z_]")
```

## YAML Handling

**Library:** `ruamel.yaml` (preserves comments and formatting)

**Pattern:**
```python
from ruamel.yaml import YAML

yaml = YAML()
yaml.preserve_quotes = True
yaml.default_flow_style = False

# Load
with open(path) as f:
    data = yaml.load(f)

# Modify in place
data["key"] = "value"

# Save (preserves formatting)
with open(path, "w") as f:
    yaml.dump(data, f)
```

## gRPC

**Generated code location:** `src/apme/v1/` (excluded from type checking and linting)

**Generated files:**
- `*_pb2.py` - Message definitions
- `*_pb2_grpc.py` - Service stubs and servicers

**Usage pattern:**
```python
from apme.v1 import primary_pb2, primary_pb2_grpc

stub = primary_pb2_grpc.PrimaryStub(channel)
response = stub.ScanStream(chunks)
```

## CLI (argparse)

**Pattern:**
```python
def run_scan(args: argparse.Namespace) -> None:
    """Execute the scan subcommand.

    Args:
        args: Parsed CLI arguments.
    """
    verbosity = getattr(args, "verbose", 0) or 0
    target = args.target

    # Process...
```

- Use `getattr(args, "key", default)` for optional arguments
- Error messages to stderr: `sys.stderr.write(f"Error: {msg}\n")`
- Exit with code 1 on error: `sys.exit(1)`

## Pre-commit Hooks

**Hooks enforced:**
- `ruff` with `--fix` flag (auto-fixes linting issues)
- `ruff-format` (code formatting)
- `mypy --strict` (type checking)
- `pydoclint` (docstring validation)
- `uv-lock` (dependency lock file sync)

**Excluded from linting:**
- `src/apme/v1/*_pb2*.py` (generated gRPC code)

---

*Convention analysis: 2026-03-25*
