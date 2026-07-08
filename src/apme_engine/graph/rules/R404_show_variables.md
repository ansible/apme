---
rule_id: R404
validator: native
description: Expose variable_set for the task.
scope: task
status: planned
status_reason: >
  Informational/debug rule that would expose the resolved variable_set for
  each task. Disabled by default. Planned for future implementation using
  VariableProvenanceResolver.
---

## Show variables (R404)

Expose variable_set for the task. This is an **informational/debug rule**
intended for development and troubleshooting. Disabled by default.

### Status

**Planned** — no `_graph.py` implementation yet. This rule would use
`VariableProvenanceResolver` to expose the full variable set available to
each task for debugging purposes. It is disabled by default and intended
for development workflows only.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
