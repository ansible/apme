---
rule_id: R108
validator: native
description: Privilege escalation (annotation-based).
---

## Privilege escalation (R108)

Task uses privilege escalation (become: true).

### Example: violation

```yaml
- name: Run as root
  ansible.builtin.command:
    cmd: systemctl restart nginx
  become: true
```

### Example: pass

```yaml
- name: Run without privilege escalation
  ansible.builtin.command:
    cmd: whoami
```
