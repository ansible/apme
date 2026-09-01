---
rule_id: R402
validator: native
description: Report variables used at end of sequence.
scope: play
status: implemented
---

## List used variables (R402)

Report variables used at end of sequence. This is an **informational/audit
rule** that enumerates all variables visible in scope across all tasks within
a play. It uses `VariableProvenanceResolver` to resolve variable definitions
and their origins (local, play, role, etc.).

Severity: **INFO** — this rule does not flag violations. It reports variable
usage data for audit and documentation purposes.

Traversal follows only structural ``CONTAINS`` edges plus dynamic
``include_tasks`` / ``import_tasks`` links. It does **not** follow
``DATA_FLOW`` or ``NOTIFY`` edges, so tasks in other plays that consume a
registered fact are excluded from the play-level report. Inherited
``no_log`` from enclosing plays and include/import ancestors is honored when
redacting variable names in included tasks.

### Example: report produced

```yaml
- name: Example play
  hosts: localhost
  connection: local
  vars:
    http_port: 8080
  tasks:
    - name: Show port
      ansible.builtin.debug:
        msg: "Port is {{ http_port }}"
```

Disabled by default. Opt in via ``rule_id_list`` when loading graph rules.

### Example: pass

```yaml
- name: No variables play
  hosts: localhost
  connection: local
  tasks:
    - name: Ok
      ansible.builtin.command: whoami
```
