---
rule_id: M029
validator: native
description: Inventory scripts must include _meta.hostvars in JSON output (enforced in 2.23)
scope: playbook
status: active
ansible_core_version: ">=2.23"
---

## Inventory script missing _meta (M029)

Inventory scripts must include `_meta.hostvars` in JSON output (enforced in 2.23).

**Removal version**: 2.23
**Fix tier**: 3
**Audience**: content

### Detection

Scans for Python files in `inventory/` and `inventories/` directories adjacent
to playbooks. Files that appear to be dynamic inventory scripts (contain
`--list`, `--host`, or `argparse` patterns) but do not reference `_meta` in
their source are flagged.

**Limitations**: This is a heuristic static analysis — it checks source-level
`_meta` references rather than actual JSON output. Scripts that construct the
key dynamically may produce false positives.

### Remediation

Add `_meta` with `hostvars` to the inventory script's JSON output:

```python
result = {
    "group1": {"hosts": ["host1", "host2"]},
    "_meta": {
        "hostvars": {
            "host1": {"ansible_host": "10.0.0.1"},
            "host2": {"ansible_host": "10.0.0.2"},
        }
    },
}
```
