---
rule_id: L054
validator: native
description: Role meta galaxy_info should include galaxy_tags.
scope: role
---

## Meta no tags (L054)

Role meta galaxy_info should include galaxy_tags.

### Example: pass

```yaml
galaxy_info:
  role_name: my_role
  galaxy_tags:
    - web
```
