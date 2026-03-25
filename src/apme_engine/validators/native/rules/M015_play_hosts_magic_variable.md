---
rule_id: M015
validator: native
description: Use ansible_play_batch instead of deprecated play_hosts variable (removed in 2.23)
scope: task
---

## play_hosts magic variable (M015)

Use ansible_play_batch instead of deprecated play_hosts variable (removed in 2.23)

**Removal version**: 2.23
**Fix tier**: 1
**Audience**: content

### Detection

Scan Jinja2 expressions for play_hosts variable

### Example: violation

```yaml
- name: Show play hosts
  ansible.builtin.debug:
    msg: "Hosts: {{ play_hosts }}"
```

### Example: pass

```yaml
- name: Show play hosts
  ansible.builtin.debug:
    msg: "Hosts: {{ ansible_play_batch }}"
```

### Remediation

Simple string replacement in Jinja2 expressions
