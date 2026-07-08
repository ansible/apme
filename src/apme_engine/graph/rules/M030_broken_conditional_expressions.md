---
rule_id: M030
validator: native
description: Conditional expressions that fail Jinja2 parsing will error in 2.23 instead of being silently ignored
scope: task
ansible_core_version: ">=2.23"
---

## Broken conditional expressions (M030)

Conditional expressions that fail Jinja2 parsing will error in 2.23 instead of being silently ignored

**Removal version**: 2.23
**Fix tier**: 2
**Audience**: content

### Detection

Parse when: values as Jinja2 expressions and flag parse failures

Parses each `when:` value as a Jinja2 expression and flags parse failures.

### Example: violation

```yaml
- hosts: localhost
  tasks:
    - name: Check status
      ansible.builtin.debug:
        msg: "status ok"
      when: "result.rc == 0 and ("
```

### Example: pass

```yaml
- hosts: localhost
  tasks:
    - name: Check status
      ansible.builtin.debug:
        msg: "status ok"
      when: result.rc == 0
```

### Remediation

Depends on specific breakage -- AI review needed
