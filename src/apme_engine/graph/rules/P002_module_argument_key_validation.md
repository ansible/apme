---
rule_id: P002
validator: ansible
description: Validate module argument keys (Ansible required).
scope: task
status: delegated
status_reason: >
  L058/L059 validate unsupported keys, required keys, and choice values when
  module specs resolve, including inherited module_defaults and action groups.
  Unavailable defaults context produces explicit coverage notices. Templated
  task-level argument mappings and short names without defaults in custom
  collection contexts remain skipped.
---

## Module argument key (P002)

Validate module argument keys (Ansible required). This policy check is
**delegated to the Ansible validator**. L058 uses documented module options;
L059 captures the module argument spec. They check unsupported keys, required
keys, and choice values when specs are available. Arguments include inherited
`module_defaults` and action groups. Unknown defaults context produces an explicit
coverage notice. Dynamic values skip choice checks and cannot establish definite
presence when they might become `omit`. Templated task-level argument mappings
and short names without defaults in custom collection contexts remain skipped.

### Status

**Delegated** — no native `_graph.py` implementation. The runtime finding is
L058 or L059. Checks are limited to unsupported keys and required-key
presence, including the statically available defaults context.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
