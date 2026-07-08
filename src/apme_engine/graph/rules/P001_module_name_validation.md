---
rule_id: P001
validator: ansible
description: Validate module name (Ansible required).
scope: task
status: delegated
status_reason: >
  Emitted by the Ansible validator via find_plugin_with_context() argspec
  validation (L058/L059). Not a native GraphRule — the Ansible validator
  owns module name resolution.
---

## Module name validation (P001)

Validate module name (Ansible required). This policy check is **delegated to
the Ansible validator** which resolves module names via
`find_plugin_with_context()`. The equivalent runtime checks are emitted as
L058/L059 by the Ansible validator's argspec validation pipeline.

### Status

**Delegated** — no native `_graph.py` implementation. The Ansible validator
owns module name resolution via L058 (docstring-based) and L059
(mock/patch-based) argspec validation. P001 is a policy-level alias for
these checks; the runtime rule_id in scan output is L058 or L059.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
