---
rule_id: L098
validator: native
description: YAML files should not have duplicate mapping keys.
scope: playbook
---

## YAML key duplicates (L098)

Duplicate mapping keys in YAML can cause silent data loss because only the last value is kept. This rule detects duplicate keys at the same indentation level.

Maps to ansible-lint `yaml[key-duplicates]`.

Requires raw YAML content before parsing. Uses `yaml_lines` when available.

### Example: violation

```yaml
- hosts: localhost
  tasks:
    - name: Install packages
      ansible.builtin.dnf:
        name: httpd
        state: present
        name: nginx
```

### Example: pass

```yaml
- hosts: localhost
  tasks:
    - name: Install packages
      ansible.builtin.dnf:
        name: httpd
        state: present
```
