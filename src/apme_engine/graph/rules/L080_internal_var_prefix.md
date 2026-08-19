---
rule_id: L080
validator: native
description: Internal role variables should be prefixed with _ (underscore).
scope: task
---

## Internal variable prefix (L080)

Internal role variables set via `set_fact` should be prefixed with `_` to signal they are private to the role.

Only fires inside `roles/` directories. Checks `set_fact` keys for a missing leading underscore.

### Example: violation

```yaml
- name: Set unprefixed variable in role
  ansible.builtin.set_fact:
    temp_value: "something"
```

### Example: pass

```yaml
- name: Set prefixed variable in role
  ansible.builtin.set_fact:
    _temp_value: "something"
```

Renaming a `set_fact` key without updating every reader (`when`, later
tasks, templates) leaves consumers on the old name — often a role default
that stays `false`. L080 is not auto-remediated until a project-level
rewrite can update all references.
