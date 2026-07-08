---
rule_id: L054
validator: native
description: Role meta galaxy_info should include galaxy_tags.
scope: role
---

## Meta no tags (L054)

Role meta `galaxy_info` should include `galaxy_tags` or `categories` to
make the role discoverable on Galaxy.

Requires ROLE node context; cannot be tested in the playbook-only harness.

### Example: violation

```yaml
galaxy_info:
  role_name: my_role
  author: Example
  description: A role without tags
```

### Example: pass

```yaml
galaxy_info:
  role_name: my_role
  galaxy_tags:
    - web
```
