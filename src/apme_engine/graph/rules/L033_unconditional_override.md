---
rule_id: L033
validator: native
description: Overriding vars without conditions.
scope: task
---

## Unconditional override (L033)

Overriding vars without conditions.

### Example: violation

```yaml
- name: Test play
  hosts: localhost
  vars:
    x: a
  tasks:
    - name: Override unconditionally
      ansible.builtin.set_fact:
        x: b
```

### Example: pass

```yaml
- name: Test play
  hosts: localhost
  vars:
    x: a
  tasks:
    - name: Conditional override
      ansible.builtin.set_fact:
        x: b
      when: some_condition is defined
```
