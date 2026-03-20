---
rule_id: L029
validator: native
description: Prefer command over shell when no shell features are needed
scope: task
---

## Command instead of shell (L029)

Prefer `ansible.builtin.command` over `ansible.builtin.shell` when the task does not require shell features (pipes, redirects, etc.). Using command is more predictable and avoids shell injection.

### Example: violation

```yaml
- name: Cat /etc/foo.conf
  ansible.builtin.shell: cat /etc/foo.conf
```

### Example: pass

```yaml
- name: Cat /etc/foo.conf
  ansible.builtin.command: cat /etc/foo.conf
```
