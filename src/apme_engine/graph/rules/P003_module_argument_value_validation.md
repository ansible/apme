---
rule_id: P003
validator: ansible
description: Validate module argument values (Ansible required).
scope: task
status: delegated
status_reason: >
  L058/L059 check choice membership and selected argument-group constraints.
  They do not validate argument types, required_if, or required_one_of. Dynamic
  values skip choice checks; templated argument mappings and short names without
  defaults in custom collection contexts remain skipped.
---

## Module argument value (P003)

Validate module argument values (Ansible required). This policy check is
**delegated to the Ansible validator**. L058/L059 compare non-templated
choice values with the module's declared choices. L059 also checks mutually
exclusive and required-together groups. Neither validator checks general
argument types, `required_if`, or `required_one_of`. Both include inherited
module defaults and action groups when their metadata is available, with explicit
coverage notices otherwise. Dynamic values cannot establish definite presence
when they might become `omit`. Short names without defaults in custom collection
contexts remain skipped; FQCNs are checked.

### Status

**Delegated** — no native `_graph.py` implementation. Runtime findings use
L058 or L059. The checks cover choice membership and selected argument-group
constraints, not general value type validation.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
