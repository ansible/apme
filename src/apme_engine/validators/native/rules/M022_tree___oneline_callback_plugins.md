---
rule_id: M022
validator: native
description: tree and oneline callback plugins are removed in 2.23; choose an alternative
scope: task
---

## tree / oneline callback plugins (M022)

tree and oneline callback plugins are removed in 2.23; choose an alternative

**Removal version**: 2.23
**Fix tier**: 3
**Audience**: content

### Detection

Detect callback_plugins: tree/oneline or stdout_callback = tree/oneline in ansible.cfg

Detects `stdout_callback`, `callback_whitelist`, or `callbacks_enabled` referencing
`tree` or `oneline` in task environment variables or configuration. Examples are
ansible.cfg-based and cannot be expressed as playbook YAML for automated testing.

### Remediation

Informational only -- no drop-in replacement
