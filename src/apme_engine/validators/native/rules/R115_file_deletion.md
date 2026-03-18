---
rule_id: R115
validator: native
description: File deletion (annotation-based).
---

## File deletion (R115)

File deletion with mutable path (annotation-based).

### Example: pass

```yaml
- name: Simple task
  ansible.builtin.debug:
    msg: hello
```
