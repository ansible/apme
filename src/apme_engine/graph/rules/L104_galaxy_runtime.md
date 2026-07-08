---
rule_id: L104
validator: native
description: Collection should have meta/runtime.yml.
scope: collection
---

## Galaxy runtime (L104)

Collections should include a `meta/runtime.yml` file that specifies `requires_ansible` and routing information.

Maps to ansible-lint `galaxy[no-runtime]`.

### Example: violation

A collection directory without `meta/runtime.yml`.

### Example: pass

A collection with `meta/runtime.yml` containing:

```yaml
requires_ansible: ">=2.15.0"
```
