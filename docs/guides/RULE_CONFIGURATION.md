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

Organization-specific checks do **not** go into the built-in OPA or Native
bundles (ADR-042). Ship a **Plugin sidecar** that implements the `Plugin`
gRPC service.

### Custom OPA (Rego) as a Plugin image

Do **not** volume-mount extra `.rego` files into the built-in `opa` container
and do **not** call `OpaValidator(bundle_path=...)` for org policy in
production. Bake a private bundle into a Plugin image, add a container to
`containers/podman/pod.yaml`, and point Engine at it with
`APME_PLUGIN_<NAME>_ADDRESS`.

Copy-paste example (banned `community.general.*` prefixes in `data.json`):
[`examples/plugins/opa-custom/`](../../examples/plugins/opa-custom/).

```bash
tox -e build
tox -e build-plugins
# uncomment plugin-opa-custom in containers/podman/pod.yaml
```

Step-by-step (including ansible-security-scanner):
[PLUGIN_SIDECARS.md](PLUGIN_SIDECARS.md).

### Third-Party Plugin Services (ADR-042)

Organization-specific checks do **not** go into built-in OPA or Native bundles.
Ship a sidecar that implements the `Plugin` gRPC service (`Validate`, optional
`Transform`, `Describe`, `Health`). The Engine discovers plugins from
environment variables and fans `Validate` out next to built-in validators.

```bash
export APME_PLUGIN_ORGPOLICY_ADDRESS=127.0.0.1:50100
```

- Rule IDs must use `EXT-<plugin_name>-<NNN>` (for example `EXT-orgpolicy-001`)
- `Transform` receives the **node YAML fragment** (`ContentNode.yaml_lines`); the Engine applies it with `ContentGraph.apply_yaml`
- Plugins are always optional: a missing or failing plugin is skipped and never fails Engine `Health`
- Plugin ports are **50100–50199** (see [ADR-042](../../.sdlc/adrs/ADR-042-third-party-plugin-services.md))
- Reference implementations: [`examples/plugins/orgpolicy/`](../../examples/plugins/orgpolicy/) (host process), [`opa-custom/`](../../examples/plugins/opa-custom/) (OPA image), [`secscan/`](../../examples/plugins/secscan/) ([ansible-security-scanner](https://github.com/cpeoples/ansible-security-scanner) image)
- Podman pod: uncomment sidecars in [`containers/podman/pod.yaml`](../../containers/podman/pod.yaml) after `tox -e build-plugins` — [PLUGIN_SIDECARS.md](PLUGIN_SIDECARS.md)
- Attach in cluster via the APME Operator `Apme.spec.plugins[]` ([apme-operator#39](https://github.com/ansible/apme-operator/issues/39))

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
