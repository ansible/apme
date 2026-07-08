---
rule_id: L099
validator: native
description: Prefer double quotes for YAML string values.
scope: playbook
---

## YAML quoted strings (L099)

For consistency, prefer double quotes over single quotes for YAML string values.

Maps to ansible-lint `yaml[quoted-strings]`.

Requires raw YAML content before parsing. Detects single-quoted strings in `yaml_lines`.

### Example: violation

```yaml
- hosts: localhost
  tasks:
    - name: Install package
      ansible.builtin.dnf:
        name: 'httpd'
        state: 'present'
```

### Example: pass

```yaml
- hosts: localhost
  tasks:
    - name: Install package
      ansible.builtin.dnf:
        name: "httpd"
        state: present
```
