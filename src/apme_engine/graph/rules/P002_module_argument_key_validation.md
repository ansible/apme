---
rule_id: P002
validator: ansible
description: Validate module argument keys (Ansible required).
scope: task
status: delegated
status_reason: >
  L058/L059 validate unsupported keys, required keys, and choice values when
  module specs resolve. They skip tasks with templated arguments and short
  names in custom collection contexts, and do not merge module_defaults or
  action groups.
---

## Module argument key (P002)

Validate module argument keys (Ansible required). This policy check is
**delegated to the Ansible validator**. L058 uses documented module options;
L059 captures the module argument spec. They check unsupported keys, required
keys, and choice values when specs are available. Tasks are skipped if any
module option contains `{{`; short names in a task's custom collection context
are skipped because the argspec pass does not resolve that plugin. FQCNs are
checked. `module_defaults` and action groups are not merged into the task options.

### Status

**Delegated** — no native `_graph.py` implementation. The runtime finding is
L058 or L059. Checks are limited to unsupported keys and required-key
presence; the validators do not merge module defaults or action groups.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
