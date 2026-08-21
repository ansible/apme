---
rule_id: L039
validator: native
description: Variable use may be undefined.
scope: task
ai_prompt: |
  L039 flags variables that may be undefined at static analysis time. If the
  variable appears to come from inventory, role parameters, extra_vars, or is
  a registered variable from a prior task, add "# noqa: L039" to the task
  line — but your explanation MUST justify why (e.g. "variable comes from
  role defaults" or "registered by prior task"). If the variable appears
  genuinely undefined with no clear source, skip the finding and let the
  user handle it manually. Do NOT add default() filters unless the user's
  intent is clear.
---

## Undefined variable (L039)

Variable use may be undefined.

### Example: violation

```yaml
- name: Use undefined var
  ansible.builtin.debug:
    msg: "{{ never_defined_var_xyz }}"
```

### Example: pass

```yaml
- name: Test play
  hosts: localhost
  vars:
    my_var: value
  tasks:
    - name: Use defined var
      ansible.builtin.debug:
        msg: "{{ my_var }}"
```
