---
rule_id: R101
validator: native
description: Task executes parameterized command (annotation-based)
scope: task
---

## Command exec (R101)

The rule checks whether a task executes a parameterized command that could be overwritten (e.g. variable in command args). It relies on annotations from the engine (CMD_EXEC + is_mutable_cmd). Depends on annotator.

### Example: pass

```yaml
- name: Run fixed command
  ansible.builtin.command:
    cmd: whoami
```
