---
rule_id: M020
validator: native
description: Use !vault instead of deprecated !vault-encrypted tag (2.23)
scope: task
---

## !vault-encrypted tag (M020)

Use !vault instead of deprecated !vault-encrypted tag (2.23)

**Removal version**: 2.23
**Fix tier**: 1
**Audience**: content

### Detection

Scan YAML content for !vault-encrypted tag

Scans raw YAML content for the `!vault-encrypted` tag. YAML tags are consumed
by the parser before reaching task-level `yaml_lines`, so doc examples cannot be
tested in the integration harness. The rule operates on pre-parsed raw content.

### Remediation

Direct tag substitution
