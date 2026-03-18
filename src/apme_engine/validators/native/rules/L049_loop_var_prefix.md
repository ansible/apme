---
rule_id: L049
validator: native
description: Loop variable should use prefix (e.g. item_).
---

## Loop var prefix (L049)

Loop variable should use prefix (e.g. item_).

### Example: violation

```yaml
- name: Process items
  ansible.builtin.debug:
    msg: "{{ item }}"
  loop:
    - a
    - b
```

### Example: pass

```yaml
- name: Process items
  ansible.builtin.debug:
    msg: "{{ item_name }}"
  loop:
    - a
    - b
  loop_control:
    loop_var: item_name
```
