# APME Dependencies

## Core Dependencies

### Ansible Risk Insights (ARI)

**Package**: `ansible-risk-insight`
**Purpose**: The underlying scanning engine for detecting playbook issues.

#### Installation

```bash
pip install ansible-risk-insight
```

#### Usage Pattern

```python
from ansible_risk_insight.scanner import ARIScanner
from ansible_risk_insight.models import ScanRequest

# Initialize scanner
scanner = ARIScanner()

# Scan a playbook
request = ScanRequest(
    target_path="/path/to/playbook.yml",
    scan_type="playbook",
)
result = scanner.scan(request)

# Access results
for finding in result.findings:
    print(f"{finding.rule_id}: {finding.message}")
    print(f"  Location: {finding.file}:{finding.line}")
    print(f"  Severity: {finding.severity}")
```

#### Key Classes

- `ARIScanner` - Main scanner class
- `ScanRequest` - Scan configuration
- `ScanResult` - Contains findings
- `Finding` - Individual issue details

#### Output Format

ARI can output in multiple formats:
- JSON (default)
- SARIF (for CI integration)
- Console (human-readable)

---

### LangGraph

**Package**: `langgraph`
**Purpose**: Agent orchestration for the rewriting workflow.

#### Installation

```bash
pip install langgraph
```

#### Usage Pattern

```python
from langgraph.graph import StateGraph, END
from typing import TypedDict

# Define state
class RewriterState(TypedDict):
    playbook_path: str
    issues: list[dict]
    fixes_applied: list[str]
    current_step: str

# Create graph
workflow = StateGraph(RewriterState)

# Add nodes
workflow.add_node("load", load_playbook)
workflow.add_node("analyze", analyze_issues)
workflow.add_node("transform", apply_transforms)
workflow.add_node("validate", validate_output)

# Add edges
workflow.set_entry_point("load")
workflow.add_edge("load", "analyze")
workflow.add_edge("analyze", "transform")
workflow.add_edge("transform", "validate")
workflow.add_edge("validate", END)

# Compile and run
app = workflow.compile()
result = app.invoke({"playbook_path": "/path/to/playbook.yml"})
```

#### Key Concepts

- **StateGraph**: Defines the workflow structure
- **Nodes**: Functions that process state
- **Edges**: Connections between nodes
- **Conditional Edges**: Branching logic based on state

---

### Typer

**Package**: `typer`
**Purpose**: CLI framework with automatic help generation.

#### Installation

```bash
pip install typer[all]
```

#### Usage Pattern

```python
import typer
from pathlib import Path

app = typer.Typer()

@app.command()
def scan(
    path: Path = typer.Argument(..., help="Playbook path"),
    fix: bool = typer.Option(False, "--fix", "-f"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """Scan a playbook for AAP compatibility issues."""
    if verbose:
        typer.echo(f"Scanning {path}...")
    # ... implementation

@app.command()
def dashboard(
    port: int = typer.Option(8501, "--port", "-p"),
):
    """Start the APME dashboard."""
    # ... implementation

if __name__ == "__main__":
    app()
```

---

### Streamlit

**Package**: `streamlit`
**Purpose**: Dashboard UI framework.

#### Installation

```bash
pip install streamlit
```

#### Usage Pattern

```python
import streamlit as st
import pandas as pd

st.title("APME Dashboard")

# Sidebar filters
severity = st.sidebar.selectbox("Severity", ["All", "Error", "Warning", "Info"])

# Load and display data
@st.cache_data
def load_scan_results(path: str) -> pd.DataFrame:
    return pd.read_json(path)

df = load_scan_results("scan-results.json")

# Filter
if severity != "All":
    df = df[df["severity"] == severity]

# Display
st.dataframe(df)

# Charts
st.bar_chart(df["type"].value_counts())
```

---

### ruamel.yaml

**Package**: `ruamel.yaml`
**Purpose**: YAML parsing that preserves comments and formatting.

#### Installation

```bash
pip install ruamel.yaml
```

#### Usage Pattern

```python
from ruamel.yaml import YAML
from pathlib import Path

yaml = YAML()
yaml.preserve_quotes = True
yaml.indent(mapping=2, sequence=4, offset=2)

# Load
path = Path("playbook.yml")
with open(path) as f:
    data = yaml.load(f)

# Modify (in-place)
for task in data[0]["tasks"]:
    if "copy" in task:
        # Transform short module to FQCN
        task["ansible.builtin.copy"] = task.pop("copy")

# Save (preserves comments!)
with open(path, "w") as f:
    yaml.dump(data, f)
```

#### Why ruamel.yaml?

- Preserves comments (PyYAML discards them)
- Maintains formatting and indentation
- Round-trip editing without data loss

---

### ansible-lint

**Package**: `ansible-lint`
**Purpose**: Complementary linting and auto-fixing in the `apme fix` pipeline (Phase 5).

#### Installation

```bash
pip install ansible-lint
```

Included as a core dependency in `pyproject.toml` — installed automatically with `pip install .` or `pip install -e .`.

#### Usage Pattern

ansible-lint is invoked as a subprocess in the fix pipeline, not imported as a Python library:

```python
from apme_engine.lint_runner import run_ansible_lint

# Report-only mode (dry-run)
result = run_ansible_lint(target_path, fix=False, profile="production")

# Fix mode (auto-correct violations)
result = run_ansible_lint(target_path, fix=True, profile="production")

# Custom config file
result = run_ansible_lint(target_path, fix=True, profile="production",
                          config_file="/path/to/.ansible-lint.yml")
```

#### CLI Integration

```bash
# Default: production profile, ansible-lint runs as Phase 5
apme-scan fix --apply /path/to/project

# Custom profile
apme-scan fix --apply --lint-profile shared /path/to/project

# Custom config file
apme-scan fix --apply --lint-config /path/to/.ansible-lint.yml /path/to/project

# Skip ansible-lint
apme-scan fix --apply --no-lint /path/to/project
```

#### Key Features Used

- `--fix` flag for auto-correcting violations
- `--profile` flag for selecting strictness level (min, basic, moderate, safety, shared, production)
- `-c` flag for custom config files
- Automatic detection of `.ansible-lint` config in the target repo

---

## Development Dependencies

### pytest

```bash
pip install pytest pytest-cov pytest-asyncio
```

```python
# conftest.py
import pytest
from pathlib import Path

@pytest.fixture
def sample_playbook(tmp_path: Path) -> Path:
    content = """
    - hosts: all
      tasks:
        - copy:
            src: /tmp/a
            dest: /tmp/b
    """
    path = tmp_path / "playbook.yml"
    path.write_text(content)
    return path
```

### Ruff

```bash
pip install ruff
```

```toml
# pyproject.toml
[tool.ruff]
line-length = 88
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]
```

### mypy

```bash
pip install mypy
```

```toml
# pyproject.toml
[tool.mypy]
python_version = "3.11"
strict = true
```

---

## Reference: x2a-convertor

**Repository**: https://github.com/ansible/x2a-convertor

APME draws inspiration from x2a-convertor's patterns but is a fresh implementation.

### Key Patterns to Adopt

1. **Module Mapping Structure**

```python
# From x2a-convertor's approach
MODULE_MAPPINGS = {
    "copy": "ansible.builtin.copy",
    "file": "ansible.builtin.file",
    "template": "ansible.builtin.template",
    # ... etc
}
```

2. **Collection Detection**

```python
def detect_collection(module_name: str) -> str | None:
    """Detect which collection a module belongs to."""
    # Check ansible.builtin first
    if module_name in BUILTIN_MODULES:
        return "ansible.builtin"
    # Check other collections
    for collection, modules in COLLECTION_MODULES.items():
        if module_name in modules:
            return collection
    return None
```

3. **Safe Transformation**

```python
def safe_transform(playbook_path: Path) -> tuple[bool, str]:
    """Transform with backup and validation."""
    # Create backup
    backup = playbook_path.with_suffix(".yml.bak")
    shutil.copy(playbook_path, backup)

    try:
        # Apply transforms
        transform(playbook_path)
        # Validate result
        if not validate_yaml(playbook_path):
            raise TransformError("Invalid YAML after transform")
        return True, ""
    except Exception as e:
        # Restore backup
        shutil.copy(backup, playbook_path)
        return False, str(e)
```

---

## Version Compatibility Matrix

| Dependency | Min Version | Max Version | Notes |
|------------|-------------|-------------|-------|
| Python | 3.11 | 3.12 | Type syntax requirements |
| ansible-risk-insight | 0.1.0 | latest | Core scanner |
| ansible-lint | latest | latest | Fix pipeline Phase 5 (auto-fix + production profile) |
| langgraph | 0.1.0 | latest | Agent workflows |
| typer | 0.9.0 | latest | CLI framework |
| streamlit | 1.28.0 | latest | Dashboard |
| ruamel.yaml | 0.18.0 | latest | YAML handling |
| pytest | 7.0.0 | latest | Testing |
| ruff | 0.1.0 | latest | Linting |
| mypy | 1.5.0 | latest | Type checking |
