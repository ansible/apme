---
rule_id: L074
validator: native
description: Role names should not contain dashes.
scope: role
---

## No dashes in role names (L074)

Role names should not contain dashes as they cause issues with collection packaging.

### Example: violation

```yaml
- name: Use role with dashes
  hosts: localhost
  roles:
    - my-web-role
```

### Example: pass

```yaml
- name: Use role without dashes
  hosts: localhost
  roles:
    - my_web_role
```
