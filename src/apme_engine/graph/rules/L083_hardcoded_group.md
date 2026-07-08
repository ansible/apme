---
rule_id: L083
validator: native
description: Do not hardcode host group names in roles.
scope: task
---

## Hardcoded group names (L083)

Do not hardcode host group names in roles. Pass host list variables or parameterize group names.

Only fires inside `roles/` directories. Checks `yaml_lines` for `groups['literal']` patterns.

### Example: violation

```yaml
- name: Check if host is in group
  ansible.builtin.debug:
    msg: "Host is a db server"
  when: inventory_hostname in groups['db_servers']
```

### Example: pass

```yaml
- name: Check if host is in group
  ansible.builtin.debug:
    msg: "Host is in target group"
  when: inventory_hostname in groups[target_group]
```
