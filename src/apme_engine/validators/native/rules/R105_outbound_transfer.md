---
rule_id: R105
validator: native
description: Outbound transfer (annotation-based).
---

## Outbound transfer (R105)

Outbound transfer to parameterized URL (annotation-based). Depends on OUTBOUND + is_mutable_dest annotation.

### Example: pass

```yaml
- name: Fixed URL request
  ansible.builtin.uri:
    url: https://api.example.com/status
    method: GET
```
