---
rule_id: R106
validator: native
description: Inbound transfer (annotation-based).
scope: task
---

## Inbound transfer (R106)

Flags inbound transfers where the source URL contains Jinja2 template
syntax. A parameterized source is a supply-chain risk when variables are
externally controlled.

### Example: violation

```yaml
- name: Download from parameterized source
  ansible.builtin.get_url:
    url: "{{ download_base_url }}/package.tar.gz"
    dest: /tmp/package.tar.gz
```

### Example: pass

```yaml
- name: Download from fixed URL
  ansible.builtin.get_url:
    url: https://example.com/stable.tar.gz
    dest: /tmp/stable.tar.gz
```
