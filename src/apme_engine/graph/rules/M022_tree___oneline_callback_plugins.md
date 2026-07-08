---
rule_id: M022
validator: native
description: tree and oneline callback plugins are removed in 2.23; choose an alternative
scope: task
ansible_core_version: ">=2.23"
---

## tree / oneline callback plugins (M022)

tree and oneline callback plugins are removed in 2.23; choose an alternative

**Removal version**: 2.23
**Fix tier**: 3
**Audience**: content

### Detection

Detect callback_plugins: tree/oneline or stdout_callback = tree/oneline in ansible.cfg

Detects `stdout_callback`, `callback_whitelist`, or `callbacks_enabled` referencing
`tree` or `oneline` in task environment variables or configuration.

### Example: violation

```yaml
- hosts: localhost
  tasks:
    - name: Run command with deprecated callback
      ansible.builtin.command: echo hello
      environment:
        ANSIBLE_STDOUT_CALLBACK: tree
```

### Example: pass

```yaml
- hosts: localhost
  tasks:
    - name: Run command with supported callback
      ansible.builtin.command: echo hello
      environment:
        ANSIBLE_STDOUT_CALLBACK: yaml
```

### Remediation

Informational only -- no drop-in replacement
