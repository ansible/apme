---
rule_id: L096
validator: native
description: meta/runtime.yml should declare requires_ansible.
scope: collection
---

## Meta runtime (L096)

The `requires_ansible` key must be present in `meta/runtime.yml`. Its absence prevents Ansible from enforcing version compatibility at install time.

Maps to ansible-lint `meta-runtime` rule. Checks `COLLECTION` nodes for the presence of `requires_ansible` in parsed runtime metadata.

### Example: violation

```yaml
# meta/runtime.yml without requires_ansible
plugin_routing: {}
```

### Example: pass

```yaml
# meta/runtime.yml with valid version specifier
requires_ansible: ">=2.15.0"
```
