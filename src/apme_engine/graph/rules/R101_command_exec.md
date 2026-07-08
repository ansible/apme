---
rule_id: R101
validator: native
description: Task executes parameterized command (annotation-based)
scope: task
ai_prompt: |
  This rule flags tasks that execute commands built from variables. Evaluate
  whether the variable comes from a trusted source (role defaults, inventory,
  well-known facts) or from untrusted user input. If the command construction
  is safe and intentional, add "# noqa: R101" to the task line — but DO NOT
  modify the command itself (no adding default filters, no re-quoting, no
  refactoring). Your explanation MUST justify why the suppression is safe,
  e.g. "variable comes from role defaults, not user input" or "command is
  constructed from well-known facts." Only refactor the command if the
  variable usage is clearly unsafe.
---

## Command exec (R101)

The rule checks whether a task executes a parameterized command that could be overwritten (e.g. variable in command args). It relies on annotations from the engine (CMD_EXEC + is_mutable_cmd). Depends on annotator.

### Example: pass

```yaml
- name: Run fixed command
  ansible.builtin.command:
    cmd: whoami
```
