# Contributing to APME

Thank you for your interest in contributing to APME! This document provides guidelines and best practices for contributing.

For full local development setup, tooling reference, and tox environments, see [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Table of Contents

- [License](#license)
- [Code of Conduct](#code-of-conduct)
- [Developer Certificate of Origin (DCO)](#developer-certificate-of-origin-dco)
- [Commit Signing](#commit-signing)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Making Changes](#making-changes)
- [Security Guidelines](#security-guidelines)
- [Pull Request Process](#pull-request-process)
  - [Branch Protection Policy](#branch-protection-policy)
- [Coding Standards](#coding-standards)

---

## License

This project is licensed under the **Apache License 2.0**. See [LICENSE](LICENSE) for the full text.

By contributing to APME, you agree that your contributions will be licensed under the Apache License 2.0. No Contributor License Agreement (CLA) is required.

---

## Code of Conduct

This project follows a standard code of conduct. Be respectful, inclusive, and professional in all interactions.

---

## Developer Certificate of Origin (DCO)

Contributors are expected to include a `Signed-off-by` trailer in the commit
message, certifying that they have the right to submit the work under the
project's Apache 2.0 license. This follows the
[Developer Certificate of Origin](https://developercertificate.org/) (DCO) and
is reviewed during the pull request process.

Add `-s` (or `--signoff`) to your commit command:

```bash
git commit -s -m "feat(engine): add new validation rule"
```

This appends a line like:

```
Signed-off-by: Your Name <your.email@example.com>
```

If you forget, you can amend the most recent commit:

```bash
git commit --amend -s --no-edit
```

---

## Commit Signing

We recommend cryptographically signing commits (and maintainers are expected to do so).
Signed commits show a "Verified" badge on GitHub and provide stronger authorship
guarantees.

### GPG signing

```bash
# Generate a key (if you don't have one)
gpg --full-generate-key   # choose RSA 4096, your GitHub email

# Tell git to use it
gpg --list-secret-keys --keyid-format=long
git config --global user.signingkey <KEY_ID>
git config --global commit.gpgsign true

# Export and add to GitHub → Settings → SSH and GPG keys
gpg --armor --export <KEY_ID>
```

### SSH signing (Git 2.34+)

```bash
git config --global gpg.format ssh
git config --global user.signingkey ~/.ssh/id_ed25519.pub
git config --global commit.gpgsign true
```

Upload the same public key to GitHub under **Settings → SSH and GPG keys →
New SSH key** with key type "Signing".

### Verifying locally

For GPG-signed commits:

```bash
git log --show-signature -1
```

For SSH-signed commits, Git also requires an `allowedSignersFile`:

```bash
# Create an allowed signers file mapping your email to your public key
echo "your.email@example.com $(cat ~/.ssh/id_ed25519.pub)" >> ~/.ssh/allowed_signers
git config --global gpg.ssh.allowedSignersFile ~/.ssh/allowed_signers

git log --show-signature -1
```

---

## Getting Started

### Prerequisites

- Python 3.10+
- Podman (for container development)
- [uv](https://docs.astral.sh/uv/) (package manager)
- Git

### Fork and Clone

```bash
# Fork via GitHub UI, then:
git clone https://github.com/YOUR_USERNAME/apme.git
cd apme
git remote add upstream https://github.com/ansible/apme.git
```

---

## Development Setup

### Install Developer Tools

```bash
# Install tox (sole developer orchestration tool)
uv tool install tox --with tox-uv

# Install prek (git hooks)
uv tool install prek
prek install
```

### Verify Setup

```bash
# List all available tox environments
tox l

# Run lint + typecheck
tox -e lint

# Run unit tests
tox -e unit
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for the full tox environment reference and pod lifecycle commands.

### Container Environment

```bash
# Build all containers
tox -e build

# Start the pod
tox -e up
```

### CLI Commands (User-Facing)

The `apme` entry point uses **`check`** to assess content and **`remediate`** to apply fixes. Engine and API layers may still use **scan** for internal pipeline concepts (for example `ScanOptions`, `scan_playbook`, `ScanResult`).

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
6. **Run quality gates**: `tox -e lint` and `tox -e unit`
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
2. **Git hooks catch common issues** — prek runs ruff, mypy, and pydoclint on commit
3. **Validate all input** — Especially paths and user-provided data
4. **No shell=True** — Never use with user input in subprocess calls

### Pre-commit Hooks

The following quality checks run automatically via prek on each commit:

- `ruff` — Lint + auto-fix
- `ruff-format` — Code formatting
- `mypy` — Strict type checking
- `pydoclint` — Docstring validation
- `uv-lock` — Lockfile consistency

If a hook fails:
```bash
# Run all hooks manually
tox -e lint
```

---

## Pull Request Process

### Before Submitting

- [ ] Quality gates pass (`tox -e lint`)
- [ ] Tests pass (`tox -e unit`)
- [ ] Documentation updated (if applicable)

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
- [ ] Quality gates pass
```

### Review Process

1. PR triggers CI checks
2. At least one maintainer review required
3. All discussions resolved
4. CI passes
5. Squash merge to `main`

### Branch Protection Policy

The `main` branch enforces these protections:

- **Require approvals** — at least 1 approving review
- **Require review from Code Owners** — CODEOWNERS-matched paths need approval from a code owner
- **Require status checks to pass** — `prek / prek`, `test / test`, `test / integration`, and `test / ui` must be green before merge
- **Require conversation resolution** — all review threads must be resolved
- **Require linear history** — no merge commits (enforced via GitHub setting)
- **Allow squash merging only** — rebase and merge commits are disabled in repo settings
- **No force pushes** — history is immutable once merged
- **No branch deletion** — `main` cannot be deleted

These settings are configured in GitHub repository settings under
**Settings → Branches → Branch protection rules**.

---

## Coding Standards

### Python

- **Style**: Enforced by Ruff (replaces flake8, isort, black)
- **Line length**: 120 characters
- **Type hints**: Required for all public functions (mypy strict)
- **Docstrings**: Google style, enforced by pydoclint

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
# Order: stdlib -> third-party -> local
# Sorted alphabetically within groups

from pathlib import Path
from typing import TYPE_CHECKING

import grpc
from ruamel.yaml import YAML

from apme_engine.runner import run_scan
from apme_engine.validators.base import ScanContext
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

---

## Questions?

- **Technical questions**: Open a GitHub Discussion
- **Bugs**: Open a GitHub Issue
- **Security issues**: See [SECURITY.md](SECURITY.md)

---

Thank you for contributing to APME!
