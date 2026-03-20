---
rule_id: R109
validator: native
description: Key/config change (annotation-based).
scope: task
---

## Key config change (R109)

Key/config change with mutable key (annotation-based). Depends on CONFIG_CHANGE + is_mutable_key annotation.

### Example: pass

```yaml
- name: Set config line
  ansible.builtin.lineinfile:
    path: /etc/ssh/sshd_config
    regexp: '^#?PermitRootLogin'
    line: 'PermitRootLogin no'
```
