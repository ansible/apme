---
rule_id: L043
validator: native
description: Avoid {{ foo }}; prefer explicit form.
---

## Deprecated bare vars (L043)

Avoid {{ foo }}; prefer explicit form.

### Example: violation

```yaml
- name: Bare var
  ansible.builtin.debug:
    msg: "{{ my_var }}"
```

### Example: pass

```yaml
- name: Filtered var
  ansible.builtin.debug:
    msg: "{{ my_var | default('') }}"
```
