---
rule_id: P001
validator: ansible
description: Validate module name (Ansible required).
scope: task
status: delegated
status_reason: >
  Module resolution is emitted by L037 when Ansible's plugin loader cannot
  resolve a module, while M001 reports non-canonical short-name resolution.
  Argspec rules L058/L059 validate arguments only after resolution.
---

## Module name validation (P001)

Validates task module names through ansible-core's
`find_plugin_with_context()`. An unresolved module is reported as L037; M001
reports when a resolvable short name has a canonical FQCN. Argspec rules
L058/L059 perform separate argument checks after a module resolves.

### Status

**Delegated** — no native `_graph.py` implementation. The Ansible validator
emits L037 for unresolved modules and M001 for canonical FQCN suggestions.
The L058/L059 argspec validators do not report unresolved names themselves.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
