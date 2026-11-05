---
rule_id: L114
validator: native
description: Collection version must be valid semantic version 1.0.0 or newer.
scope: collection
---

## Collection version (L114)

Checks an owned collection's `version` in `galaxy.yml` against semantic
versioning and requires `1.0.0` or newer. A missing version in available
metadata is reported by L095. When collection metadata is unavailable, L114
reports that the version could not be read.

### Example: violation

```yaml
namespace: acme
name: platform
version: 0.9.0
```

### Example: pass

```yaml
namespace: acme
name: platform
version: 1.0.0
```
