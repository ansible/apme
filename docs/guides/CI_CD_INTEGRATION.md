# CI/CD Integration Guide

This guide covers how to integrate APME into your CI/CD pipelines for automated Ansible content validation.

## Overview

APME provides several features designed for CI/CD integration:

| Feature | Command | CI Use Case |
|---------|---------|-------------|
| Exit codes | All commands | Fail pipeline on violations |
| JSON output | `--json` | Parse results programmatically |
| Check mode | `format --check` | Fail if formatting needed |
| Diff output | `--diff` | Show proposed changes |
| Verbose | `-v` / `-vv` | Debug timing and details |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success, no violations |
| 1 | Violations found |
| 2 | Error (invalid input, connection failure) |

## GitHub Actions

### Basic Workflow

Create `.github/workflows/apme.yml`:

```yaml
name: APME Scan

on:
  pull_request:
    paths:
      - '**.yml'
      - '**.yaml'
      - 'roles/**'
      - 'playbooks/**'
      - 'collections/**'

jobs:
  apme-check:
    runs-on: ubuntu-latest
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Install UV
        uses: astral-sh/setup-uv@v4

      - name: Install APME
        run: uv tool install apme-engine

      - name: Run APME check
        run: apme check --json . > apme-results.json

      - name: Upload results
        uses: actions/upload-artifact@v4
        if: always()
        with:
          name: apme-results
          path: apme-results.json
```

### With Container Image

> **Note:** The CLI container image is designed for pod deployments where the Primary
> service runs separately. For standalone CI usage, either use pip installation (shown
> above) or override the container's default Primary address with `APME_PRIMARY_ADDRESS=""`:

```yaml
name: APME Scan (Container)

on:
  pull_request:
    paths:
      - '**.yml'
      - '**.yaml'

jobs:
  apme-check:
    runs-on: ubuntu-latest
    container:
      image: ghcr.io/ansible/apme-cli:latest
      env:
        APME_PRIMARY_ADDRESS: ""  # Enable daemon auto-start mode
    steps:
      - name: Checkout
        uses: actions/checkout@v4

      - name: Run APME check
        run: apme check .
```

### Format Check (Fail on Style Issues)

```yaml
name: APME Format Check

on:
  pull_request:

jobs:
  format-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv tool install apme-engine
      
      - name: Check YAML formatting
        run: apme format --check .
```

### Full Validation Pipeline

```yaml
name: APME Full Validation

on:
  pull_request:
  push:
    branches: [main]

jobs:
  validate:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      
      - name: Install APME
        run: uv tool install apme-engine

      - name: Format check
        run: apme format --check .

      - name: Lint check
        run: apme check --json . | tee apme-results.json

      - name: Summary
        if: always()
        run: |
          echo "## APME Results" >> $GITHUB_STEP_SUMMARY
          if [ -f apme-results.json ]; then
            VIOLATIONS=$(jq '.violations | length' apme-results.json)
            echo "Found **$VIOLATIONS** violations" >> $GITHUB_STEP_SUMMARY
          fi
```

### PR Comment with Results

```yaml
name: APME PR Review

on:
  pull_request:

permissions:
  pull-requests: write

jobs:
  apme-review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4
      - run: uv tool install apme-engine

      - name: Run APME
        id: apme
        continue-on-error: true
        run: |
          apme check --json . > results.json
          echo "exit_code=$?" >> $GITHUB_OUTPUT

      - name: Comment on PR
        uses: actions/github-script@v7
        with:
          script: |
            const fs = require('fs');
            let results = { violations: [] };
            try {
              const content = fs.readFileSync('results.json', 'utf8');
              if (content.trim()) {
                results = JSON.parse(content);
              }
            } catch (e) {
              console.log('Could not parse results.json:', e.message);
            }
            const violations = results.violations || [];
            
            let body = '## APME Scan Results\n\n';
            
            if (violations.length === 0) {
              body += ':white_check_mark: No violations found!\n';
            } else {
              body += `:warning: Found **${violations.length}** violation(s)\n\n`;
              body += '| Rule | File | Line | Message |\n';
              body += '|------|------|------|--------|\n';
              
              for (const v of violations.slice(0, 20)) {
                body += `| ${v.rule_id} | ${v.file} | ${v.line} | ${v.message} |\n`;
              }
              
              if (violations.length > 20) {
                body += `\n_...and ${violations.length - 20} more_\n`;
              }
            }
            
            github.rest.issues.createComment({
              issue_number: context.issue.number,
              owner: context.repo.owner,
              repo: context.repo.repo,
              body: body
            });

      - name: Fail if violations
        if: steps.apme.outputs.exit_code != '0'
        run: exit 1
```

### Caching for Faster Runs

```yaml
jobs:
  apme-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v4

      - name: Cache APME
        uses: actions/cache@v4
        with:
          path: |
            ~/.cache/uv
            ~/.apme-data
          key: apme-${{ runner.os }}-${{ hashFiles('**/requirements.yml', '**/galaxy.yml') }}
          restore-keys: |
            apme-${{ runner.os }}-

      - run: uv tool install apme-engine
      - run: apme check .
```

## GitLab CI

### Basic Pipeline

Create `.gitlab-ci.yml`:

```yaml
stages:
  - validate

variables:
  PIP_CACHE_DIR: "$CI_PROJECT_DIR/.cache/pip"

cache:
  paths:
    - .cache/

apme-check:
  stage: validate
  image: python:3.12-slim
  before_script:
    - pip install uv
    - uv tool install apme-engine
  script:
    - apme check .
  rules:
    - if: $CI_PIPELINE_SOURCE == "merge_request_event"
      changes:
        - "**/*.yml"
        - "**/*.yaml"
        - roles/**/*
        - playbooks/**/*
```

### With Container Image

> **Note:** The CLI container requires `APME_PRIMARY_ADDRESS=""` for standalone CI use.

```yaml
apme-check:
  stage: validate
  image: ghcr.io/ansible/apme-cli:latest
  variables:
    APME_PRIMARY_ADDRESS: ""  # Enable daemon auto-start mode
  script:
    - apme check --json . > apme-results.json
  artifacts:
    paths:
      - apme-results.json
    when: always
```

### Full Pipeline with Format Check

```yaml
stages:
  - lint
  - validate

apme-format:
  stage: lint
  image: ghcr.io/ansible/apme-cli:latest
  variables:
    APME_PRIMARY_ADDRESS: ""
  script:
    - apme format --check .
  rules:
    - if: $CI_MERGE_REQUEST_IID

apme-check:
  stage: validate
  image: ghcr.io/ansible/apme-cli:latest
  variables:
    APME_PRIMARY_ADDRESS: ""
  script:
    - apme check --json . | tee apme-results.json || true  # Capture results even on violations
    - |
      if [ ! -s apme-results.json ]; then
        echo "APME failed to produce output"
        exit 2
      fi
      VIOLATIONS=$(python3 -c "import json; print(len(json.load(open('apme-results.json')).get('violations', [])))")
      echo "APME found $VIOLATIONS violations"
      if [ "$VIOLATIONS" -gt 0 ]; then
        exit 1
      fi
  artifacts:
    paths:
      - apme-results.json
    when: always
  rules:
    - if: $CI_MERGE_REQUEST_IID
```

### Merge Request Integration

```yaml
apme-review:
  stage: validate
  image: ghcr.io/ansible/apme-cli:latest
  variables:
    APME_PRIMARY_ADDRESS: ""
  script:
    - apme check --json . > results.json || true
    - |
      python3 -c "
      import json
      import sys

      try:
          with open('results.json') as f:
              content = f.read().strip()
              if not content:
                  print('APME produced no output')
                  sys.exit(2)
              data = json.loads(content)
      except (json.JSONDecodeError, FileNotFoundError) as e:
          print(f'Failed to parse results: {e}')
          sys.exit(2)

      violations = data.get('violations', [])
      count = len(violations)
      print(f'APME found {count} violations')
      if count > 0:
          with open('apme-summary.md', 'w') as f:
              f.write(f'## APME found {count} violations\n\n')
              for v in violations:
                  f.write(f\"- **{v['rule_id']}**: {v['file']}:{v['line']} - {v['message']}\n\")
          sys.exit(1)
      "
    - cat apme-summary.md 2>/dev/null || true
  artifacts:
    paths:
      - results.json
      - apme-summary.md
    when: always
```

## Jenkins

### Declarative Pipeline

```groovy
pipeline {
    agent any
    
    stages {
        stage('Setup') {
            steps {
                sh 'pip install uv'
                sh 'uv tool install apme-engine'
            }
        }
        
        stage('Format Check') {
            steps {
                sh 'apme format --check .'
            }
        }
        
        stage('APME Scan') {
            steps {
                sh 'apme check --json . > apme-results.json'
            }
            post {
                always {
                    archiveArtifacts artifacts: 'apme-results.json'
                }
            }
        }
    }
    
    post {
        failure {
            script {
                def results = readJSON file: 'apme-results.json'
                def count = results.violations?.size() ?: 0
                echo "APME found ${count} violations"
            }
        }
    }
}
```

### With Docker Agent

> **Note:** The CLI container requires `APME_PRIMARY_ADDRESS=""` for standalone CI use.

```groovy
pipeline {
    agent {
        docker {
            image 'ghcr.io/ansible/apme-cli:latest'
        }
    }
    
    environment {
        APME_PRIMARY_ADDRESS = ''  // Enable daemon auto-start mode
    }
    
    stages {
        stage('Scan') {
            steps {
                sh 'apme check .'
            }
        }
    }
}
```

## Azure DevOps

### Basic Pipeline

Create `azure-pipelines.yml`:

```yaml
trigger:
  paths:
    include:
      - '**/*.yml'
      - '**/*.yaml'
      - 'roles/**'
      - 'playbooks/**'

pool:
  vmImage: 'ubuntu-latest'

steps:
  - task: UsePythonVersion@0
    inputs:
      versionSpec: '3.12'

  - script: |
      pip install uv
      uv tool install apme-engine
    displayName: 'Install APME'

  - script: apme format --check .
    displayName: 'Format Check'

  - script: apme check --json . > $(Build.ArtifactStagingDirectory)/apme-results.json
    displayName: 'APME Scan'

  - task: PublishBuildArtifacts@1
    inputs:
      pathToPublish: '$(Build.ArtifactStagingDirectory)/apme-results.json'
      artifactName: 'apme-results'
    condition: always()
```

## Generic Container Usage

For any CI system that supports containers:

> **Note:** Set `APME_PRIMARY_ADDRESS=""` to enable daemon auto-start mode. Without
> this, the container expects a Primary service at `primary:50051`.

```bash
# Pull the CLI image
docker pull ghcr.io/ansible/apme-cli:latest

# Run scan (mount project as /workspace)
docker run --rm -e APME_PRIMARY_ADDRESS="" \
  -v "$(pwd):/workspace:ro" ghcr.io/ansible/apme-cli:latest check /workspace

# Run with JSON output
docker run --rm -e APME_PRIMARY_ADDRESS="" \
  -v "$(pwd):/workspace:ro" ghcr.io/ansible/apme-cli:latest check --json /workspace

# Format check
docker run --rm -e APME_PRIMARY_ADDRESS="" \
  -v "$(pwd):/workspace:ro" ghcr.io/ansible/apme-cli:latest format --check /workspace

# Run remediation (needs write access)
docker run --rm -e APME_PRIMARY_ADDRESS="" \
  -v "$(pwd):/workspace:rw" ghcr.io/ansible/apme-cli:latest remediate /workspace
```

## Best Practices

### 1. Fail Fast

Run format check before full validation:

```yaml
- run: apme format --check .  # Fast, catches style issues
- run: apme check .           # Full validation
```

### 2. Cache Aggressively

APME caches collection metadata and session venvs. Cache these directories:

| Path | Contents |
|------|----------|
| `~/.apme-data` | APME data directory (daemon state, collection cache, session venvs) |
| `~/.cache/uv` | UV package cache |

### 3. Use JSON Output for Parsing

```bash
# Get violation count
apme check --json . | jq '.violations | length'

# Get unique rules triggered
apme check --json . | jq '[.violations[].rule_id] | unique'

# Filter by severity
apme check --json . | jq '[.violations[] | select(.severity == "error")]'
```

### 4. Selective Scanning

For large monorepos, scan only changed paths:

```yaml
- name: Checkout with full history
  uses: actions/checkout@v4
  with:
    fetch-depth: 0  # Required for git diff against origin/main

- name: Fetch base branch
  run: git fetch origin main

- name: Get changed files
  id: changes
  run: |
    # grep -E returns exit 1 when no matches; use || true to avoid step failure
    FILES=$(git diff --name-only origin/main...HEAD | grep -E '\.(yml|yaml)$' || true)
    FILES=$(echo "$FILES" | tr '\n' ' ')
    echo "files=$FILES" >> $GITHUB_OUTPUT

- name: Scan changed files
  if: steps.changes.outputs.files != ''
  run: apme check ${{ steps.changes.outputs.files }}
```

### 5. Baseline and Delta

For existing projects with many violations, track progress:

```bash
# Generate baseline
apme check --json . > baseline.json

# In CI, compare against baseline
apme check --json . > current.json
NEW_VIOLATIONS=$(jq -s '.[1].violations - .[0].violations | length' baseline.json current.json)
if [ "$NEW_VIOLATIONS" -gt 0 ]; then
  echo "New violations introduced!"
  exit 1
fi
```

### 6. Skip Expensive Checks in PR

```yaml
# Fast PR check (skip dependency scanning)
- run: apme check --skip-dep-scan .

# Full scan on main branch
- run: apme check .
  if: github.ref == 'refs/heads/main'
```

## Troubleshooting

### Common Issues

| Issue | Solution |
|-------|----------|
| Slow first run | Cache `~/.apme-data` and `~/.cache/uv` |
| Permission denied | Ensure workspace is readable; use `:ro` mount |
| Collection not found | Run `ansible-galaxy collection install` first or use `--skip-collection-scan` |
| Exit code 2 | Check for syntax errors in input files |

### Debug Mode

```bash
# Verbose output
apme check -v .

# Very verbose (per-rule timing)
apme check -vv .
```

## Related Documentation

- [Rule Catalog](../rules/RULE_CATALOG.md) — Available rules and severity levels
- [Deployment](DEPLOYMENT.md) — Container deployment details
- [Development](DEVELOPMENT.md) — Contributing and testing
