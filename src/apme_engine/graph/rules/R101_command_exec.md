---
rule_id: R101
validator: native
description: Task executes a parameterized command.
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

The rule checks mapped command modules and flags a command value containing Jinja syntax. It uses APME's local module risk profile to identify the command field; it does not consume ARI annotations.

### Example: pass

```yaml
- name: Run fixed command
  ansible.builtin.command:
    cmd: whoami
```
