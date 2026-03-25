---
rule_id: M027
validator: native
description: Mixing inline k=v arguments with args: mapping is deprecated (2.23)
scope: task
---

## Legacy k=v merged with args (M027)

Mixing inline k=v arguments with args: mapping is deprecated (2.23)

**Removal version**: 2.23
**Fix tier**: 2
**Audience**: content

### Detection

Detect tasks with both inline k=v and args: mapping

### Example: violation

```yaml
- name: Copy file
  ansible.builtin.copy: src=a
  args:
    dest: /tmp/b
```

### Example: pass

```yaml
- name: Copy file
  ansible.builtin.copy:
    src: a
    dest: /tmp/b
```

### Remediation

Move k=v params into args mapping
