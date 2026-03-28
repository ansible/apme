---
rule_id: M029
validator: native
description: Inventory scripts must include _meta.hostvars in JSON output (enforced in 2.23)
scope: task
---

## Inventory script missing _meta (M029)

Inventory scripts must include _meta.hostvars in JSON output (enforced in 2.23)

**Removal version**: 2.23
**Fix tier**: 3
**Audience**: content

### Detection

Analyze inventory script output for missing _meta key

This rule is currently disabled (`enabled=False`) because inventory script
analysis requires executing the script, which is a runtime concern. Detection
approach is documented for future implementation.

### Remediation

Informational only -- requires modifying external scripts
