---
rule_id: R104
validator: native
description: Download from unauthorized source (annotation-based).
scope: task
---

## Unauthorized download (R104)

Download from unauthorized source. Flags HTTP (non-HTTPS) URLs.

### Example: violation

```yaml
- name: Download from HTTP
  ansible.builtin.get_url:
    url: http://example.com/file.tar.gz
    dest: /tmp/file.tar.gz
```

### Example: pass

```yaml
- name: Download from HTTPS
  ansible.builtin.get_url:
    url: https://example.com/file.tar.gz
    dest: /tmp/file.tar.gz
```
