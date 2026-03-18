---
rule_id: R114
validator: native
description: File change (annotation-based).
---

## File change (R114)

File change with mutable path/src (annotation-based). Depends on FILE_CHANGE + is_mutable_path/is_mutable_src annotation.

### Example: pass

```yaml
- name: Copy file
  ansible.builtin.copy:
    src: files/config.yml
    dest: /etc/app/config.yml
```
