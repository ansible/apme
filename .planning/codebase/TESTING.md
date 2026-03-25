# Testing Patterns

**Analysis Date:** 2026-03-25

## Test Framework

**Runner:**
- Framework: pytest [configured in pyproject.toml]
- Config file: `pyproject.toml` [tool.pytest.ini_options]
- Version: pytest (latest, from dev dependencies)

**Assertion Library:**
- Built-in `assert` statements (not pytest.raises style for most tests)
- Plain assertions with descriptive messages

**Run Commands:**
```bash
pytest tests/                          # Run all tests (excludes integration)
pytest tests/ -m integration           # Run integration tests only
pytest tests/ -v                       # Verbose output
pytest tests/ -v --tb=short            # Short traceback format
pytest tests/ --cov                    # With coverage report
pytest tests/ -k test_name             # Run single test pattern
pytest -m "not integration and not ui" # Default (from addopts)
```

**Configuration in pyproject.toml:**
```toml
[tool.pytest.ini_options]
testpaths = ["tests", "src/apme_engine/validators/native/rules"]
pythonpath = ["src"]
addopts = "-v --tb=short -m 'not integration and not ui'"
markers = [
    "integration: marks tests as integration (may use network or external deps)",
    "ui: marks tests as browser/Playwright tests (require running UI + gateway stack)",
]
asyncio_mode = "auto"
```

**Key settings:**
- `asyncio_mode = "auto"`: Automatically detects and runs async tests
- Default addopts exclude integration and UI tests (run with `-m integration` explicitly)
- Testpaths include `src/apme_engine/validators/native/rules` (rule tests co-located with implementation)

## Test File Organization

**Location:**
- Unit tests: `tests/` directory structure mirrors `src/apme_engine/`
- Integration tests: `tests/integration/` with special markers
- UI tests: `tests/` with `@pytest.mark.ui` decorator
- Rule tests: `src/apme_engine/validators/native/rules/` (co-located with rules)
- Fixtures: `tests/fixtures/` (test data, terrible-playbook example)

**Naming:**
- `test_*.py` for test modules
- `*_test.py` not used
- Test classes: `Test*` (e.g., `TestConfig`, `TestTabRemoval`)
- Test functions: `test_*` with descriptive names: `test_tabs_replaced_with_spaces()`

**Structure:**
```
tests/
├── conftest.py                              # Shared fixtures (module scope)
├── integration/
│   ├── conftest.py                          # Integration infrastructure
│   ├── test_e2e.py
│   └── test_ui_e2e.py
├── fixtures/
│   ├── terrible-playbook/                   # Real playbook files for testing
│   └── opa_rules.rego
├── test_ansi.py
├── test_engine_scanner.py
├── test_formatter.py
├── test_gateway_api.py
├── test_gateway_db.py
└── test_session.py
```

## Test Structure

**Suite organization (from `tests/test_engine_scanner.py`):**
```python
"""Tests for apme_engine.engine.scanner."""

from __future__ import annotations

import pytest
from apme_engine.engine.scanner import Config, SingleScan


class TestConfig:
    """Tests for Config."""

    def test_defaults_no_config_file(self, tmp_path: Path) -> None:
        """Config with missing file uses defaults.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cfg = Config(path=str(tmp_path / "nonexistent.yml"))
        assert cfg.data_dir \!= ""
        assert cfg.log_level == "info"

    def test_from_yaml_file(self, tmp_path: Path) -> None:
        """Config loads from YAML file.

        Args:
            tmp_path: Pytest temporary directory fixture.
        """
        cfg_file = tmp_path / "config.yml"
        cfg_file.write_text("data_dir: /custom/data\nlog_level: debug\n")
        cfg = Config(path=str(cfg_file))
        assert cfg.data_dir == "/custom/data"
```

**Patterns:**
- Test classes group related tests: `class TestConfig:`, `class TestTabRemoval:`
- Docstrings on test methods (Args section for fixtures)
- One logical assertion per test when possible
- Descriptive test names that describe the behavior being tested

## Test Function Structure

**Naming pattern:** `test_<behavior>_<outcome>`

Examples:
- `test_tabs_replaced_with_spaces()` - behavior: tab handling, outcome: spaces
- `test_config_with_missing_file_uses_defaults()` - behavior: missing config, outcome: defaults
- `test_list_sessions_empty()` - behavior: list on empty DB, outcome: empty list

**Arrange-Act-Assert (AAA) pattern:**
```python
def test_scan_detects_fqcn_issues(self) -> None:
    """Scan should detect modules missing FQCN."""
    # Arrange
    playbook = create_playbook_with_short_module("copy")

    # Act
    result = scanner.scan(playbook)

    # Assert
    assert len(result.issues) == 1
    assert result.issues[0].type == IssueType.FQCN
```

## Fixtures

**Definition (from `tests/conftest.py`):**
```python
@pytest.fixture  # type: ignore[untyped-decorator]
def repo_root() -> Path:
    """Project root (ansible-forward).

    Returns:
        Path to project root.
    """
    return Path(__file__).resolve().parent.parent


@pytest.fixture  # type: ignore[untyped-decorator]
def sample_hierarchy_payload() -> YAMLDict:
    """Minimal valid OPA input (hierarchy payload).

    Returns:
        YAMLDict with scan_id, hierarchy, metadata.
    """
    return {
        "scan_id": "test-scan-1",
        "hierarchy": [...],
        "metadata": {...},
    }
```

**Fixture scope:**
- No scope specified: function (default, new fixture per test)
- `scope="module"`: One fixture for all tests in module
- `scope="session"`: One fixture for entire test session
- `autouse=True`: Automatically apply to all tests in scope

**Type ignore comment:**
All fixtures have `# type: ignore[untyped-decorator]` because pytest decorators lack full type stubs.

**Location:**
- Global fixtures: `tests/conftest.py` (shared across all test modules)
- Module-specific fixtures: In the test module itself
- Integration fixtures: `tests/integration/conftest.py` (daemon/gateway infrastructure)

**Infrastructure fixtures (from `tests/integration/conftest.py`):**
```python
@pytest.fixture(scope="session")  # type: ignore[untyped-decorator]
def infrastructure() -> Infrastructure:
    """Provide the daemon infrastructure to tests.

    Returns:
        Infrastructure dataclass with daemon addresses and state.
    """
    assert INFRASTRUCTURE is not None, "Daemon not started. Run with: pytest -m integration"
    return INFRASTRUCTURE
```

**Temporary directory fixtures:**
```python
@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
async def _db(tmp_path: Path) -> AsyncIterator[None]:
    """Initialise a fresh DB per test.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Yields:
        None: Test runs between setup and teardown.
    """
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    yield
    await close_db()
```

## Mocking

**Framework:** `unittest.mock` (built-in, no external dependency)

**Patterns:**

Import:
```python
from unittest import mock
from unittest.mock import patch, MagicMock
```

Using `patch` decorator:
```python
@mock.patch("sys.stdout.isatty", return_value=False)
def test_color_disabled(self, mock_isatty: mock.Mock) -> None:
    """Color detection respects terminal check."""
    assert not should_use_color()
```

Using `patch` context manager:
```python
def test_env_overrides(self, tmp_path: Path) -> None:
    """Environment variables override config."""
    with patch.dict(os.environ, {"ARI_DATA_DIR": "/env/data"}):
        cfg = Config(path=str(tmp_path / "missing.yml"))
    assert cfg.data_dir == "/env/data"
```

Using `monkeypatch` fixture (preferred for simple cases):
```python
@pytest.fixture  # type: ignore[untyped-decorator]
def force_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set FORCE_COLOR environment variable.

    Args:
        monkeypatch: Pytest fixture for modifying environment.
    """
    monkeypatch.setenv("FORCE_COLOR", "1")
    monkeypatch.delenv("NO_COLOR", raising=False)
```

**What to mock:**
- External services: filesystem, network, environment variables
- Time-dependent operations: `time.time()`, `datetime.now()`
- Random operations: `random.random()`
- Large dependencies (databases, servers) for unit tests

**What NOT to mock:**
- Logic under test
- Internal utility functions
- Simple standard library functions (dict, list operations)
- Logging (let it run, suppress stderr if needed)

## Async Testing

**Auto-detection (asyncio_mode = "auto"):**
```python
async def test_list_sessions_empty() -> None:
    """Listing sessions on empty DB returns empty list."""
    async with get_session() as db:
        result = await q.list_sessions(db)
    assert result == []
```

- Mark tests with `async def` automatically handled
- No `@pytest.mark.asyncio` decorator needed (pytest-asyncio 0.24+)
- Fixtures can be async: `async def _db(tmp_path: Path) -> AsyncIterator[None]:`

**Async fixture pattern:**
```python
@pytest.fixture(autouse=True)  # type: ignore[untyped-decorator]
async def _db(tmp_path: Path) -> AsyncIterator[None]:
    """Initialize fresh DB per test.

    Args:
        tmp_path: Pytest-provided temporary directory.

    Yields:
        None: Test runs between setup and teardown.
    """
    db_path = str(tmp_path / "test.db")
    await init_db(db_path)
    yield
    await close_db()
```

**Async generators for streaming:**
```python
class AsyncCommandStream:
    """Async iterator backed by a queue for feeding commands."""

    def __init__(self) -> None:
        """Initialize empty command queue."""
        self._queue: asyncio.Queue[SessionCommand | None] = asyncio.Queue()

    def send(self, cmd: SessionCommand) -> None:
        """Enqueue a command for the stream."""
        self._queue.put_nowait(cmd)

    def close(self) -> None:
        """Signal end of stream."""
        self._queue.put_nowait(None)

    def __aiter__(self) -> AsyncCommandStream:
        """Return self as async iterator."""
        return self

    async def __anext__(self) -> SessionCommand:
        """Return next command or raise StopAsyncIteration."""
        val = await self._queue.get()
        if val is None:
            raise StopAsyncIteration
        return val
```

## Error Testing

**Expecting exceptions:**
```python
def test_bad_config_file_raises(self, tmp_path: Path) -> None:
    """Invalid YAML config raises ValueError.

    Args:
        tmp_path: Pytest temporary directory fixture.
    """
    cfg_file = tmp_path / "bad.yml"
    cfg_file.write_text("invalid: yaml: content: [[[")
    with pytest.raises(ValueError, match="failed to load"):
        Config(path=str(cfg_file))
```

- Use `pytest.raises(ExceptionType, match="pattern")` for exception assertions
- Match pattern is a regex applied to exception message
- Optional context manager body for additional assertions

## Parametrized Tests

**Pattern from `tests/test_rule_doc_coverage.py`:**
```python
@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "rule_id,expected_severity",
    [
        ("L001", "info"),
        ("L002", "warning"),
        ("L003", "error"),
    ],
)
def test_rule_severity(self, rule_id: str, expected_severity: str) -> None:
    """Each rule has documented severity level.

    Args:
        rule_id: Rule identifier.
        expected_severity: Expected severity string.
    """
    rule = rules[rule_id]
    assert rule.severity == expected_severity
```

- Use `@pytest.mark.parametrize` for multiple inputs
- First arg: comma-separated parameter names (string)
- Second arg: list of tuples with test inputs
- Test function receives unpacked arguments

## Coverage

**Configuration (in pyproject.toml):**
```toml
[tool.coverage.run]
source = ["src/apme_engine"]
omit = ["*/tests/*", "*/__pycache__/*", "*/rules/*"]
branch = true

[tool.coverage.report]
exclude_lines = [
    "pragma: no cover",
    "def __repr__",
    "raise AssertionError",
    "raise NotImplementedError",
]
show_missing = true
fail_under = 50
```

**Requirements:**
- Minimum 50% coverage enforced (`fail_under = 50`)
- Branch coverage enabled (`branch = true`)
- Validator rules excluded from coverage

**View coverage:**
```bash
pytest tests/ --cov=src/apme_engine --cov-report=html
open htmlcov/index.html
```

**Lines to exclude:**
```python
def some_func() -> None:
    """Example function."""
    raise NotImplementedError  # Excluded from coverage
```

## Test Organization by Type

**Unit Tests (most common):**
- Location: `tests/test_*.py`
- Markers: None (default)
- Dependencies: No external services
- Speed: Fast (< 100ms)
- Fixtures: Lightweight (tmp_path, mocks)

**Example from `tests/test_formatter.py`:**
```python
class TestTabRemoval:
    """Tests for tab removal (L040)."""

    def test_tabs_replaced_with_spaces(self) -> None:
        """Tabs in YAML are replaced with spaces."""
        result = _fmt("- name: Test\n\tansible.builtin.debug:\n\t\tmsg: hello\n")
        assert "\t" not in result.formatted
        assert result.changed
```

**Integration Tests:**
- Location: `tests/integration/test_*.py`
- Markers: `@pytest.mark.integration`
- Dependencies: Full daemon, gateway, proxy infrastructure
- Speed: Slow (several seconds each)
- Fixtures: `infrastructure` fixture from conftest.py
- Run with: `pytest -m integration`

**Example from `tests/integration/test_e2e.py`:**
```python
@pytest.mark.integration  # type: ignore[untyped-decorator]
async def test_scan_e2e(infrastructure: Infrastructure) -> None:
    """Full scan workflow: submit project, await results."""
    # Uses infrastructure fixture which starts daemon, gateway, proxy
```

**UI Tests (Playwright):**
- Location: `tests/test_ui_playwright.py`
- Markers: `@pytest.mark.ui`
- Dependencies: Running frontend + gateway
- Speed: Very slow (browser automation)
- Run with: `pytest -m ui` (requires UI stack running)

## Common Test Helpers

**From `tests/test_formatter.py`:**
```python
def _fmt(text: str, filename: str = "test.yml") -> FormatResult:
    """Format dedented text and return FormatResult.

    Args:
        text: YAML content (will be dedented).
        filename: Optional filename for the formatter.

    Returns:
        FormatResult from format_content.
    """
    return format_content(textwrap.dedent(text), filename=filename)
```

**From `tests/integration/conftest.py`:**
```python
def _free_port() -> int:
    """Find a free TCP port on localhost.

    Returns:
        Available port number.
    """
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _wait_for_port(port: int, timeout: float = 15.0) -> bool:
    """Block until a TCP port is accepting connections.

    Args:
        port: Port number to probe.
        timeout: Maximum seconds to wait.

    Returns:
        True if port became reachable, False on timeout.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False
```

## Testing Checklist

Before submitting a PR:
- [ ] Unit tests pass: `pytest tests/ -v`
- [ ] Coverage minimum met: `pytest --cov` shows >= 50%
- [ ] No integration tests broken: `pytest -m integration` passes (if running locally)
- [ ] Async tests use `async def` (auto-detected)
- [ ] Fixtures properly scoped (function, module, or session)
- [ ] Mocks match actual interface (avoid brittle mocks)
- [ ] Test names describe behavior clearly
- [ ] Docstrings on test methods include Args for fixtures
- [ ] Parametrized tests use meaningful parameter names

## Debugging Tests

**Run single test with verbose output:**
```bash
pytest tests/test_formatter.py::TestTabRemoval::test_tabs_replaced_with_spaces -vv
```

**Run with print statements visible:**
```bash
pytest tests/ -v -s
```

**Run with pdb on failure:**
```bash
pytest tests/ --pdb
```

**Run with full traceback:**
```bash
pytest tests/ --tb=long
```

**Capture test output in variable:**
```bash
pytest tests/ -v --capture=no
```

---

*Testing analysis: 2026-03-25*
