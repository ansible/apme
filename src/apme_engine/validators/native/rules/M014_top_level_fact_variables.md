---
rule_id: M014
validator: native
description: Use ansible_facts["name"] instead of injected ansible_* fact variables (removed in 2.24)
scope: task
---

## Top-level fact variables (M014)

Use ansible_facts["name"] instead of injected ansible_* fact variables (removed in 2.24)

**Removal version**: 2.24
**Fix tier**: 1
**Audience**: content

### Detection

Scan Jinja2 expressions for ansible_* variable references that are known facts

### Example: violation

```yaml
- name: Show hostname
  ansible.builtin.debug:
    msg: "Host is {{ ansible_hostname }}"
```

### Example: pass

```yaml
- name: Show hostname
  ansible.builtin.debug:
    msg: 'Host is {{ ansible_facts["hostname"] }}'
```

### Remediation

Regex substitution: ansible_FACTNAME -> ansible_facts['FACTNAME']
