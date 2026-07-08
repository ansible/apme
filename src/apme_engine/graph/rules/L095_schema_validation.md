---
rule_id: L095
validator: native
description: YAML file does not match expected schema structure.
scope: playbook
---

## Schema validation (L095)

Basic structural schema validation for playbooks and galaxy.yml. Checks for required keys and rejects unknown play-level keys.

Maps to ansible-lint `schema` rule.

Checks `PLAY` nodes for unknown play-level keys and `COLLECTION` nodes for missing required `galaxy.yml` keys (`namespace`, `name`, `version`).

### Example: violation

```yaml
# galaxy.yml missing required namespace key
name: my_collection
version: 1.0.0
```

### Example: pass

```yaml
# galaxy.yml with all required keys
namespace: my_namespace
name: my_collection
version: 1.0.0
```
