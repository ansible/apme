# Rule Configuration Guide

This guide covers how to customize APME's rule behavior, including enabling/disabling rules, creating custom rules, and understanding AI confidence scoring.

## Rule Blacklisting

APME provides multiple mechanisms to disable rules that don't apply to your environment.

### Per-Project Configuration

Create `.apme/rules.yml` in your project root to disable specific rules:

```yaml
# .apme/rules.yml
rules:
  L026:
    enabled: false   # Disable FQCN requirement
  R108:
    enabled: false   # Disable shell injection check
  M003:
    enabled: false   # Disable modernization rule
```

**Configuration options per rule:**

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `enabled` | bool | `true` | Set to `false` to skip this rule |
| `severity` | string | (rule default) | Override: `info`, `low`, `medium`, `high`, `critical` |
| `enforced` | bool | `false` | If `true`, bypasses all fingerprint suppression modes from `.apme/suppressions.yml` during CLI suppression processing |

### Inline Suppression

Suppress rules on specific tasks using `# noqa`:

```yaml
- name: Run dangerous command  # noqa: R108, L030
  ansible.builtin.shell: rm -rf /tmp/*
```

This works for **native graph rules** (applied during rule evaluation) and for
**OPA task-scoped findings** (applied by the engine after validator fan-out,
using the node's `yaml_lines` from the ContentGraph when the finding's `path`
is that node's id — e.g. `# noqa: L068` on a `lineinfile` task). Put the
comment on the same task/node that owns the finding (typically the `name:`
line or the module key line).

Findings without a graph node `path`, or on nodes without `yaml_lines`
(for example some file-level or playbook-scoped hits), are not suppressed
by `# noqa`.

**Note:** `enforced: true` affects `.apme/suppressions.yml` fingerprint
suppressions during CLI suppression processing. Inline `# noqa` comments are
honored at scan time for native and OPA task findings, so this setting does
not currently override those inline suppressions.

Fingerprint suppressions live in `.apme/suppressions.yml`. The CLI can manage
that file with `apme suppress add`, `apme suppress list`, and
`apme suppress remove`. The file stores entries under a top-level
`suppressions:` list with fields such as `fingerprint`, `rule_id`, `mode`,
`reason`, and `created`.

### CLI Flags

Skip entire validation categories at scan time:

```bash
# Skip dependency scans (collection health + Python audit)
apme check --skip-dep-scan /path/to/project

# Skip only collection health scanning
apme check --skip-collection-scan /path/to/project

# Skip only Python CVE audit
apme check --skip-python-audit /path/to/project
```

### Gateway API Override

For enterprise deployments, rules can be overridden via the Gateway API:

```http
PUT /api/v1/rules/L026/config
Content-Type: application/json

{
  "enabled_override": false,
  "severity_override": 2
}
```

**Severity values:** 0=unspecified, 1=info, 2=low, 3=medium, 4=high, 5=error, 6=critical

## Custom Rules (BYO)

APME supports adding custom rules through multiple mechanisms.

### OPA Custom Bundles

The OPA validator supports custom Rego rule bundles. This is the primary method for adding custom rules today.

**1. Create a custom bundle directory:**

```
my-custom-rules/
├── custom_rules.rego
└── custom_rules.md
```

**2. Write your Rego rule:**

```rego
# my-custom-rules/custom_rules.rego
package apme.custom

import future.keywords.in

violations[v] {
    # Your rule logic here
    task := input.hierarchy.tasks[_]
    task.module == "ansible.builtin.command"
    not task.options.creates
    not task.options.removes
    
    v := {
        "rule_id": "CUSTOM-001",
        "level": "warning",
        "message": "Command task should have creates or removes",
        "file": task.file,
        "line": task.line
    }
}
```

**3. Create documentation sidecar:**

```markdown
---
rule_id: CUSTOM-001
validator: opa
description: Command tasks should be idempotent
scope: task
---

## Custom Rule 001

Command tasks should specify `creates` or `removes` for idempotency.
```

**4. Use your custom bundle:**

When using the OPA validator programmatically:

```python
from apme_engine.validators.opa import OpaValidator

validator = OpaValidator(
    bundle_path="/path/to/my-custom-rules",
    entrypoint="data.apme.custom.violations"
)
```

### Third-Party Plugin Services (Future)

ADR-042 defines a plugin architecture for enterprise deployments. Plugins:

- Implement a `Plugin` gRPC service
- Use rule IDs like `EXT-<plugin_name>-<NNN>`
- Support both validation AND transformation
- Discovered via `APME_PLUGIN_<NAME>_ADDRESS` environment variables

**Status:** Design complete, implementation pending.

## AI Confidence Scoring

APME's AI remediation engine provides confidence scores for proposed fixes.

### How It Works

1. **Tier 2 Remediation:** When a violation cannot be auto-fixed (Tier 1), it's escalated to AI
2. **AI Analysis:** The AI provider analyzes the code context and proposes a fix
3. **Confidence Score:** Each proposal includes a confidence score (0.0 to 1.0)
4. **User Review:** Low-confidence proposals can be flagged for manual review

### Confidence Levels

| Score Range | Interpretation |
|-------------|----------------|
| 0.85 - 1.0 | High confidence — likely correct fix |
| 0.70 - 0.84 | Medium confidence — review recommended |
| < 0.70 | Low confidence — manual review required |

### Where Scores Appear

**Database:** The `proposals` table stores confidence per AI fix:

```sql
SELECT rule_id, file, confidence, status 
FROM proposals 
WHERE scan_id = ?;
```

**API:** The Gateway exposes confidence in operation responses:

```json
{
  "proposals": [
    {
      "rule_id": "L039",
      "file": "tasks/main.yml",
      "confidence": 0.92,
      "status": "pending"
    }
  ]
}
```

**Aggregated Statistics:**

```http
GET /api/v1/stats/ai-acceptance

{
  "rules": [
    {
      "rule_id": "L039",
      "approved": 45,
      "rejected": 3,
      "pending": 2,
      "avg_confidence": 0.89
    }
  ]
}
```

### Default Confidence

The AI provider returns a default confidence of 0.85 when no explicit score is provided. Confidence aggregation uses the average across all changes in a proposal.

## Rule ID Conventions

APME uses prefixed numeric IDs (per ADR-008):

| Prefix | Category | Examples |
|--------|----------|----------|
| **L** | Lint (style, correctness) | L002–L059 |
| **M** | Modernize (ansible-core migration) | M001–M004 |
| **R** | Risk/security (annotation-based) | R101–R501 |
| **P** | Policy (requires ansible runtime) | P001–P004 |
| **A** | AAP-specific (platform compatibility) | A001–A099 |
| **SEC** | Secrets (Gitleaks) | SEC:* |

Custom rules should use the `CUSTOM-` or `EXT-` prefix to avoid conflicts.

## Related Documentation

- [Rule Catalog](../rules/RULE_CATALOG.md) — Complete list of built-in rules
- [ADR-008](../../.sdlc/adrs/ADR-008-rule-id-conventions.md) — Rule ID conventions
- [ADR-041](../../.sdlc/adrs/ADR-041-rule-catalog-override-architecture.md) — Override architecture
- [ADR-042](../../.sdlc/adrs/ADR-042-third-party-plugin-services.md) — Plugin architecture
