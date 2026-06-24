# APME Dependencies

## Core Dependencies

These are declared in `pyproject.toml` under `[project.dependencies]` and are required
for all installations (CLI daemon and Podman pod alike).

### Engine (Integrated)

The scanning engine lives in `src/apme_engine/engine/` — fully integrated, not a pip
dependency (see [ADR-003](/.sdlc/adrs/ADR-003-vendor-ari-engine.md)).

**Purpose**: Parses Ansible content, builds a `ContentGraph`, resolves variables,
annotates risks, and produces a hierarchy payload for validators.

#### Usage Pattern

```python
from apme_engine.runner import run_scan

# Scan a project directory
context = run_scan(
    target_path="/path/to/project",
    project_root="/path/to/project",
    include_scandata=True,
    dependency_dir="/path/to/venv/lib/python3.12/site-packages",
)

# context.hierarchy_payload — JSON-serializable dict for OPA/Ansible
# context.scandata — legacy SingleScan (native validator now uses ContentGraph)
```

#### Key Classes

- `AnsibleProjectLoader` — Main loader class (`engine/scanner.py`)
- `ContentGraph` — Graph model for nodes/edges (`engine/content_graph.py`)
- `ScanContext` — Result container with hierarchy_payload + scandata (`validators/base.py`)

#### Collection Dependencies

The engine never downloads collections. The `VenvSessionManager` (owned by Primary)
installs collections into session-scoped venvs via the Galaxy Proxy before the engine
runs. The engine receives a `dependency_dir` pointing to the venv's `site-packages`
for pre-installed collection content.

---

### gRPC Stack

| Package | Version | Purpose |
|---------|---------|---------|
| `grpcio` | `>=1.80.0` | gRPC runtime for all inter-service communication |
| `grpcio-health-checking` | `>=1.80.0` | Standard health-check protocol |
| `protobuf` | `>=6.31.1,<7` | Protocol buffer serialization |

All gRPC servers use `grpc.aio` (fully async). Proto definitions in `proto/apme/v1/`,
generated stubs in `src/apme/v1/`.

---

### Web / HTTP

| Package | Version | Purpose |
|---------|---------|---------|
| `fastapi` | `>=0.100` | REST API framework (Gateway + Galaxy Proxy) |
| `starlette` | `>=1.3.1` | ASGI framework (underlying FastAPI) |
| `uvicorn[standard]` | `>=0.20` | ASGI server for Galaxy Proxy and Gateway |
| `httpx` | latest | HTTP client (Galaxy API calls in proxy) |

---

### Data / Serialization

| Package | Version | Purpose |
|---------|---------|---------|
| `jsonpickle` | latest | Serializes complex Python objects (scandata) for legacy paths |
| `PyYAML` | latest | YAML parsing (read-only; writes use ruamel.yaml) |
| `ruamel.yaml` | latest | Comment-preserving YAML for remediation transforms |
| `networkx` | `>=3.1` | Graph algorithms for ContentGraph operations |

---

### Ansible / Security

| Package | Version | Purpose |
|---------|---------|---------|
| `ansible-core` | `>=2.17.14` | Required for Ansible validator (plugin loader, syntax check) |
| `pip-audit` | `>=2.7` | Python CVE scanning (Dep Audit validator) |

---

### Utilities

| Package | Version | Purpose |
|---------|---------|---------|
| `filelock` | latest | Venv session locking |
| `rapidfuzz` | latest | Fuzzy string matching (module name suggestions) |
| `joblib` | latest | Caching and parallel execution helpers |
| `packaging` | `>=23` | Version parsing and comparison |

---

### ruamel.yaml

**Purpose**: YAML parsing that preserves comments and formatting. Used by the
remediation engine's transforms — never PyYAML for writes.

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
        task["ansible.builtin.copy"] = task.pop("copy")

# Save (preserves comments!)
with open(path, "w") as f:
    yaml.dump(data, f)
```

---

## Optional Dependencies

### AI (`[project.optional-dependencies.ai]`)

| Package | Purpose |
|---------|---------|
| `abbenay-client` | gRPC client for the Abbenay AI provider (Tier 2 remediation) |

### Gateway (`[project.optional-dependencies.gateway]`)

| Package | Purpose |
|---------|---------|
| `sqlalchemy>=2.0` | ORM for scan history persistence |
| `aiosqlite>=0.20` | Async SQLite driver |
| `python-multipart>=0.0.31` | File upload handling |

---

## Development Dependencies (`[project.optional-dependencies.dev]`)

| Package | Purpose |
|---------|---------|
| `pytest` | Test framework |
| `pytest-cov` | Coverage reporting |
| `pytest-mock` | Mock utilities |
| `pytest-asyncio>=0.24` | Async test support |
| `pytest-xdist>=3.5` | Parallel test execution |
| `pytest-playwright` | Browser/UI tests |
| `ruff` | Linter and formatter |
| `mypy` | Static type checker |
| `pydoclint>=0.8.0` | Docstring linter |
| `types-protobuf` | Type stubs for protobuf |
| `types-PyYAML` | Type stubs for PyYAML |
| `grpc-stubs` | Type stubs for gRPC |
| `grpcio-tools>=1.80.0` | Proto code generation |

### Tool Configuration

```toml
# pyproject.toml
[tool.ruff]
target-version = "py310"
line-length = 120

[tool.mypy]
python_version = "3.10"
strict = true
```

---

## Frontend Dependencies

The React SPA (`frontend/`) uses:

| Package | Purpose |
|---------|---------|
| React 18 | UI framework |
| Vite | Build tool |
| PatternFly 6 | Red Hat design system (components, charts, table, icons) |
| React Router 6 | Client-side routing |
| SWR | Data fetching |
| i18next | Internationalization |

---

## Version Compatibility Matrix

| Dependency | Min Version | Notes |
|------------|-------------|-------|
| Python | 3.10 | `requires-python = ">=3.10"` |
| ansible-core | 2.17.14 | Multi-version venvs (2.17/2.18/2.19/2.20) |
| grpcio | 1.80.0 | Required for proto v6 compatibility |
| protobuf | 6.31.1 | Major version 6 only (not 7) |
| ruamel.yaml | 0.18.0 | YAML round-trip |
| pytest | 7.0.0 | Testing |
| ruff | latest | Linting (120-char, py310 target) |
| mypy | latest | Type checking (strict) |
