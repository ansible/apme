---
rule_id: R501
validator: native
description: Suggest collection/role dependency.
---

## Dependency suggestion (R501)

Suggest collection/role dependency for unresolved modules with possible candidates. Requires unresolved module with possible_candidates (harness may resolve short names).

### Example: pass

```yaml
- name: Copy file with FQCN
  ansible.builtin.copy:
    src: files/config.yml
    dest: /etc/config.yml
```
