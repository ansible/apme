---
rule_id: R108
validator: native
description: Privilege escalation (annotation-based).
scope: play
ai_prompt: |
  This rule flags privilege escalation (become: true). Many tasks
  legitimately require become — package installation, service management,
  file ownership changes, and system configuration all need root. If the
  task genuinely needs elevated privileges, add "# noqa: R108" to the task
  line — but DO NOT modify the task itself. Your explanation MUST justify
  why become is required, e.g. "task installs packages via dnf which
  requires root" or "task manages systemd services." Only attempt to
  remove become if the task clearly does not need elevated privileges.
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
