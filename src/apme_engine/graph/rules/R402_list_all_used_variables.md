---
rule_id: R402
validator: native
description: Report variables used at end of sequence.
scope: task
status: planned
status_reason: >
  Informational/reporting rule requiring deeper graph analysis to enumerate
  all variables referenced across a task sequence. Planned for future
  implementation using VariableProvenanceResolver.
---

## List used variables (R402)

Report variables used at end of sequence. This is an **informational/audit
rule** that would enumerate all variables referenced across a task sequence
for visibility and documentation purposes.

### Status

**Planned** — no `_graph.py` implementation yet. This rule requires
`VariableProvenanceResolver` to enumerate all variable references across the
full task sequence and report them as an informational finding. It does not
flag violations — it reports data for audit purposes.

### Example: pass

```yaml
- name: Example play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
