# Contributing to APME

Thank you for your interest in contributing to APME! This document provides guidelines and best practices for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Security Guidelines](#security-guidelines)
- [Pull Request Process](#pull-request-process)
- [Coding Standards](#coding-standards)

---

## Code of Conduct

This project follows a standard code of conduct. Be respectful, inclusive, and professional in all interactions.

---

## Getting Started

### Prerequisites

- Python 3.11+
- Podman (for container development)
- UV (recommended) or pip
- Git

### Fork and Clone

```bash
# Fork via GitHub UI, then:
git clone https://github.com/YOUR_USERNAME/aap-apme.git
cd aap-apme
git remote add upstream https://github.com/ORG/aap-apme.git
```

---

## Development Setup

### Local Environment

```bash
# Create virtual environment
python -m venv .venv
source .venv/bin/activate

# Install dependencies
uv pip install -e ".[dev]"

# Install pre-commit hooks (REQUIRED)
pre-commit install
pre-commit install --hook-type commit-msg
```

### Container Environment

```bash
# Build all containers
./containers/podman/build.sh

# Start the pod
./containers/podman/up.sh
```

### Verify Setup

```bash
# Run tests
pytest

# Run linting
ruff check src/

# Type check
mypy src/
```

---

## Making Changes

### Branch Naming

```
feature/DR-NNN-short-description
fix/issue-number-short-description
docs/update-section-name
```

### Workflow

1. **Check for existing issues/DRs** before starting work
2. **Create a branch** from `main`
3. **Read relevant specs** in `.sdlc/specs/`
4. **Make changes** following coding standards
5. **Write/update tests**
6. **Run pre-commit hooks** (automatic on commit)
7. **Create PR** with description

### Spec-Driven Development

For new features:

1. Check `.sdlc/specs/` for existing requirements
2. If no spec exists, create one using templates in `.sdlc/templates/`
3. Get spec reviewed before implementing
4. Reference spec in PR description

---

## Security Guidelines

**Read [SECURITY.md](SECURITY.md) before contributing.**

### Critical Rules

1. **NEVER commit secrets** — API keys, passwords, tokens, private keys
2. **Pre-commit hooks are mandatory** — They catch secrets before commit
3. **Validate all input** — Especially paths and user-provided data
4. **No shell=True** — Never use with user input in subprocess calls

### Pre-commit Checks

The following security checks run automatically:

- `gitleaks` — Detects secrets in code
- `detect-secrets` — Additional secret patterns
- `bandit` — Python security linter
- `detect-private-key` — Catches key files

If a hook fails:
```bash
# See what failed
pre-commit run --all-files

# If false positive for secrets, update baseline
detect-secrets scan --baseline .secrets.baseline
```

---

## Pull Request Process

### Before Submitting

- [ ] All tests pass (`pytest`)
- [ ] Linting passes (`ruff check src/`)
- [ ] Type checks pass (`mypy src/`)
- [ ] Pre-commit hooks pass
- [ ] Documentation updated (if applicable)
- [ ] CHANGELOG.md updated (for user-facing changes)

### PR Template

```markdown
## Summary
Brief description of changes.

## Related Specs
- REQ-NNN: [Requirement name]
- TASK-NNN: [Task name]
- DR-NNN: [Decision request, if applicable]

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation

## Testing
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated
- [ ] Manual testing performed

## Security Checklist
- [ ] No secrets in code
- [ ] Input validation added
- [ ] Pre-commit hooks pass
```

### Review Process

1. PR triggers CI checks
2. At least one maintainer review required
3. All discussions resolved
4. CI passes
5. Squash merge to `main`

---

## Coding Standards

### Python

- **Style**: Follow PEP 8, enforced by Ruff
- **Line length**: 88 characters (Black default)
- **Type hints**: Required for all public functions
- **Docstrings**: Google style for public APIs

```python
def scan_playbook(
    path: Path,
    *,
    options: ScanOptions | None = None,
) -> ScanResult:
    """Scan a playbook for violations.

    Args:
        path: Path to the playbook file.
        options: Optional scan configuration.

    Returns:
        ScanResult containing detected violations.

    Raises:
        FileNotFoundError: If path doesn't exist.
        ScanError: If scanning fails.
    """
```

### Imports

```python
# Order: stdlib → third-party → local
# Sorted alphabetically within groups

from pathlib import Path
from typing import TYPE_CHECKING

import grpc
from ruamel.yaml import YAML

from apme.scanner import ScanResult
from apme.scanner.issue_types import Issue
```

### Testing

- **Location**: `tests/` mirroring `src/` structure
- **Naming**: `test_*.py` files, `test_*` functions
- **Fixtures**: In `conftest.py`
- **Markers**: Use `@pytest.mark.integration` for slow tests

```python
def test_scan_detects_fqcn_issues(sample_playbook: Path) -> None:
    """Scanner should detect modules missing FQCN."""
    result = scanner.scan(sample_playbook)

    assert len(result.issues) >= 1
    assert any(i.type == IssueType.FQCN for i in result.issues)
```

### Commit Messages

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
type(scope): description

[optional body]

[optional footer]
```

Types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`

Examples:
```
feat(scanner): add SARIF output format

Implements TASK-003: Reporter with SARIF support for CI integration.

Closes #123
```

```
fix(opa): handle empty hierarchy payload

Previously, OPA validator crashed on empty projects.
Now returns empty violations list.
```

---

## Questions?

- **Technical questions**: Open a GitHub Discussion
- **Bugs**: Open a GitHub Issue
- **Security issues**: See [SECURITY.md](SECURITY.md)

---

Thank you for contributing to APME!
