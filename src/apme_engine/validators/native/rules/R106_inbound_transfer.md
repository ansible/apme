---
rule_id: R106
validator: native
description: Inbound transfer (annotation-based).
---

## Inbound transfer (R106)

Inbound transfer from parameterized source (annotation-based). Depends on INBOUND + is_mutable_src annotation.

### Example: pass

```yaml
- name: Download from fixed URL
  ansible.builtin.get_url:
    url: https://example.com/stable.tar.gz
    dest: /tmp/stable.tar.gz
```
