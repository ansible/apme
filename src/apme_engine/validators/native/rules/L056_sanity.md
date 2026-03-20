---
rule_id: L056
validator: native
description: Path may match ignore pattern.
scope: playbook
---

## Sanity (L056)

Path may match ignore pattern.

### Example: pass

```yaml
- name: Simple task
  ansible.builtin.debug:
    msg: hello
```
