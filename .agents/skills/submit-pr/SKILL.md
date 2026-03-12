---
name: submit-pr
description: Prepare and submit a pull request for the APME project. Syncs with upstream, creates a feature branch, runs pre-commit checks (prek/ruff), updates documentation and ADRs as needed, commits with conventional commits, then creates the PR via gh. Use when the user asks to submit, create, or open a pull request, or says "submit PR", "open PR", "create PR".
---

# Submit PR

## Workflow

### Step 1: Sync with upstream and create a feature branch

Always start from the latest upstream main:

```bash
git fetch upstream
git checkout -b <branch-name> upstream/main
```

Use a descriptive branch name (e.g., `feat/add-ruff-prek`, `fix/parser-context-manager`).

If changes already exist on the current branch (e.g., from an in-progress session), cherry-pick or rebase them onto the new branch.

### Step 2: Run pre-commit checks

```bash
prek run --all-files
```

If `prek` is not installed, fall back to:

```bash
ruff check src/ tests/ && ruff format --check src/ tests/
```

If violations are found:
1. Run `ruff check --fix src/ tests/` and `ruff format src/ tests/` to auto-fix
2. Manually fix any remaining violations
3. Re-run until clean

### Step 3: Update documentation

Check whether your changes affect areas covered by existing docs. Update any that apply:

| Doc | When to update |
|-----|----------------|
| `docs/DEVELOPMENT.md` | New dev workflows, setup changes, new rule patterns |
| `docs/ARCHITECTURE.md` | Container topology, gRPC contract changes, new services |
| `docs/DATA_FLOW.md` | Request lifecycle, serialization, payload shape changes |
| `docs/DEPLOYMENT.md` | Podman pod spec, container config, env vars |
| `docs/LINT_RULE_MAPPING.md` | New or renamed rule IDs |
| `docs/DESIGN_VALIDATORS.md` | Validator abstraction changes |
| `docs/DESIGN_REMEDIATION.md` | Remediation engine changes |

If a new rule was added, regenerate the catalog:

```bash
python scripts/generate_rule_catalog.py
```

### Step 4: Update SDLC artifacts (if applicable)

If the change involves an architectural decision (new service, new protocol, new deployment strategy, new tooling adoption), create an ADR in `.sdlc/adrs/` using the `adr-new` skill. The file should follow the naming convention `ADR-NNN-slug.md`.

If open questions or decisions emerged during the session, create a Decision Request using the `dr-new` skill.

If requirements or tasks were affected, update them using the `req-new` or `task-new` skills.

The agent should invoke these skills proactively when context warrants it, informing the user of any artifacts created. All artifacts are reviewed in the PR diff.

### Step 5: Commit with conventional commits

Use the [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/) format:

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

Common types for this project:

| Type | When to use |
|------|-------------|
| `feat` | New feature (rule, validator, CLI subcommand, service) |
| `fix` | Bug fix |
| `docs` | Documentation only |
| `style` | Code style/formatting (no logic change) |
| `refactor` | Code restructuring (no feature or fix) |
| `test` | Adding or updating tests |
| `build` | Build system, dependencies, containers |
| `ci` | CI/CD configuration |
| `chore` | Maintenance tasks |

Scopes reflect project areas: `engine`, `native`, `opa`, `ansible`, `gitleaks`, `daemon`, `cli`, `formatter`, `remediation`, `cache`, `proto`.

Examples:
- `feat(native): add L060 jinja2-spacing rule`
- `fix(engine): use context manager for file reads in parser`
- `build: add ruff linter and prek pre-commit hooks`
- `docs: add prek section to DEVELOPMENT.md`

### Step 6: Push and create the pull request

```bash
git push -u origin HEAD

gh pr create --repo upstream-owner/repo --title "conventional commit style title" --body "$(cat <<'EOF'
## Summary
- Concise description of what changed and why

## Changes
- List of notable changes

## Test plan
- [ ] prek run --all-files passes
- [ ] pytest passes
- [ ] Docs updated (if applicable)
- [ ] ADR added (if applicable)
EOF
)"
```

The PR targets upstream's `main` branch from the fork. Return the PR URL to the user.

### Maintaining the PR

When pushing additional commits to an existing PR, **always update the PR body** to reflect the new changes:

```bash
gh pr edit <pr-number> --body "$(cat <<'EOF'
...updated body...
EOF
)"
```

The Summary, Changes, and Test plan sections must stay current with all commits on the branch, not just the initial one.
