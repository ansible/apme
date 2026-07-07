---
rule_id: M029
validator: native
description: Inventory scripts must include _meta.hostvars in JSON output (enforced in 2.23)
scope: task
status: stub
status_reason: >
  Detection requires executing the inventory script at runtime to inspect its
  JSON output. Static analysis cannot determine _meta presence. Disabled by
  design until a runtime-analysis approach is approved.
ansible_core_version: ">=2.23"
---

## Inventory script missing _meta (M029)

Inventory scripts must include `_meta.hostvars` in JSON output (enforced in 2.23).

**Removal version**: 2.23
**Fix tier**: 3
**Audience**: content

### Status

**Stub** — no `_graph.py` implementation. This rule is intentionally disabled
(`enabled=False`) because detection requires *executing* the inventory script
and inspecting its JSON output at runtime. Static analysis of the script source
cannot determine whether `_meta` is present in the output. A runtime-analysis
approach would need its own ADR.

### Detection

Analyze inventory script output for missing `_meta` key.

### Remediation

Informational only — requires modifying external scripts.
