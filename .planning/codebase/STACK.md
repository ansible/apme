# Technology Stack

**Analysis Date:** 2026-03-25

## Languages

**Primary:**
- Python 3.10+ - Core APME engine, validators (native, OPA, Ansible, Gitleaks), primary orchestrator, gateway, galaxy proxy
- TypeScript 5.7 - Frontend React UI (strict mode enabled)
- Protocol Buffers 3 - gRPC service definitions

**Secondary:**
- Shell - Build/deployment scripts in `containers/podman/`
- YAML - Configuration, Ansible playbooks for testing
- NGINX - UI reverse proxy configuration

## Runtime

**Environment:**
- Python 3.12 with `uv` package manager (via `ghcr.io/astral-sh/uv:python3.12-bookworm-slim` base image)
- Node 22-alpine - Frontend build environment only (not production runtime)
- Podman 4.0+ for container orchestration

**Package Manager:**
- `uv` - Python dependency management and locking
  - Lockfile: `uv.lock` (present, frozen for reproducible builds)
  - No `pip` or `poetry`; exclusively `uv sync` in Containerfiles
- npm - JavaScript dependency management (requires `npm ci` in UI build)
  - Lockfile: `frontend/package-lock.json`

## Frameworks

**Core:**
- gRPC (v1.78.0+) with async support (`grpc.aio`) - All inter-service communication per ADR-001
- FastAPI (v0.100+) - REST API gateway and proxy services
- SQLAlchemy (v2.0+) - ORM for gateway database persistence
- uvicorn (v0.20+) - Async ASGI server for FastAPI apps

**Frontend:**
- React 18.3 - UI framework
- React Router 6.28 - Client-side routing
- PatternFly React 6.3 - Ansible UI design system
- Zustand 5.0 - Client state management
- SWR 2.2 - Data fetching/caching library
- Vite 6.0 - Build tool and dev server

**Testing:**
- pytest (with pytest-asyncio, pytest-mock, pytest-cov) - Python unit/integration tests
- pytest-playwright - Browser automation for E2E tests
- Vitest 4.1 - JavaScript/TypeScript test runner
- @testing-library/react - React component testing utilities

**Build/Dev:**
- Ruff (with isort plugin) - Python linting and formatting
- mypy - Python type checking (strict mode)
- pydoclint - Google-style docstring validation
- grpcio-tools (v1.78.0+) - gRPC code generation from `.proto` files

## Key Dependencies

**Critical:**
- `grpcio` (v1.78.0+) - gRPC framework; hardcoded minimum version for async stability
- `protobuf` (v6.31.1 to <7) - Protocol buffer runtime; version locked to major 6
- `fastapi` (v0.100+) - REST API routing and validation
- `sqlalchemy` (v2.0+) - Database ORM; major version 2 required for async support
- `aiosqlite` (v0.20+) - Async SQLite driver for gateway persistence

**Data Processing:**
- `PyYAML` - YAML parsing (Ansible playbooks)
- `ruamel.yaml` - Extended YAML support
- `jsonpickle` - Python object serialization
- `rapidfuzz` - String fuzzy matching for rule matching
- `joblib` - Parallel processing for rule evaluation

**HTTP/Network:**
- `httpx` - Async HTTP client for Galaxy API calls and external integrations

**Infrastructure:**
- `filelock` - File-based locking for session venv management
- `python-multipart` (v0.0.6+) - Multipart form handling for FastAPI file uploads

**Optional (extras):**
- `abbenay-client` (v2026.3.8a0) - AI remediation engine client via gRPC
  - Installed via: `pip install apme-engine[ai]`
  - Sources: GitHub releases (not PyPI)
  - Provides: LLM-driven code patch generation

**Frontend:**
- `@patternfly/react-*` (v6.3) - Ansible-branded UI components (charts, tables, icons)
- `react-hook-form` (v7.72) - Form state management
- `styled-components` (v6.1) - CSS-in-JS styling
- `i18next` - Internationalization framework
- `jszip` - Client-side file zipping for session downloads

## Configuration

**Environment:**
- `pyproject.toml` - Single source of truth for Python dependencies, versions, and build config
  - Managed by setuptools backend; installed packages via `uv sync`
  - Optional extras: `ai`, `proxy`, `gateway`, `dev`
- `.env` file (NOT in git) - Runtime environment variables for secrets and configuration
  - Key vars in pod.yaml: `OPENROUTER_API_KEY`, `APME_ABBENAY_TOKEN`, `APME_AI_MODEL`

**Build:**
- `Dockerfile` files in `containers/*/` - Multi-stage builds for all services
- `containers/base/Dockerfile` - Shared dependency layer for all Python services
  - Mounts `uv` cache during build for faster dependency resolution
  - Installs all extras: ai, gateway, proxy
  - Final image contains `/app/.venv` with activated Python environment
- `containers/podman/pod.yaml` - Pod manifest defining all service containers and their networking
  - Generated from template with `__APME_CACHE_PATH__` and `${APME_ROOT}` substitution
  - Specifies ports, volumes, and environment variables for each service

**Linting & Type Checking:**
```toml
[tool.ruff]
target-version = "py310"
line-length = 120
select = ["E", "F", "W", "I", "UP", "B", "SIM", "D"]  # Error, Flake8, Warning, isort, Upgrade, Bugbear, Simplify, Docstring

[tool.mypy]
strict = true
disallow_any_explicit = true
```

**Testing Configuration:**
```toml
[tool.pytest.ini_options]
testpaths = ["tests", "src/apme_engine/validators/native/rules"]
pythonpath = ["src"]
markers = [
    "integration: marks tests as integration (may use network or external deps)",
    "ui: marks tests as browser/Playwright tests (require running UI + gateway stack)",
]
asyncio_mode = "auto"
```

## Platform Requirements

**Development:**
- Python 3.10+ (recommended 3.12 to match container base)
- Node 18+ (for frontend development; 22+ for production builds)
- Podman 4.0+ or Docker with podman-compatible syntax
- `uv` package manager (installed from `ghcr.io/astral-sh/uv` or standalone)

**Production:**
- Container runtime (Podman or Docker)
- Kubernetes optional (pod.yaml is Podman-native; needs conversion for K8s)
- SQLite 3.30+ (embedded in `aiosqlite`)
- Abbenay daemon 2026.3.8-alpha (for AI features; optional if not using tier-2/tier-3 fixes)

**Deployment Targets:**
- Podman Pods (primary target; references `apme-pod` in `containers/podman/pod.yaml`)
- OpenShift (Podman-based clusters supported)
- Kubernetes (requires pod.yaml → Deployment conversion; not tested)

---

*Stack analysis: 2026-03-25*
