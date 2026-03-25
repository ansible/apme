---
rule_id: M019
validator: native
description: !!omap and !!pairs YAML tags are deprecated; standard YAML mappings preserve order in Python 3.7+ (2.23)
scope: task
---

## !!omap / !!pairs YAML tags (M019)

!!omap and !!pairs YAML tags are deprecated; standard YAML mappings preserve order in Python 3.7+ (2.23)

**Removal version**: 2.23
**Fix tier**: 1
**Audience**: content

### Detection

Scan YAML content for !!omap or !!pairs tags

### Example: violation

```yaml
my_ordered_map: !!omap
  - key1: value1
  - key2: value2
```

### Example: pass

```yaml
my_ordered_map:
  key1: value1
  key2: value2
```

### Remediation

Remove the tag
