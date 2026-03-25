---
rule_id: M029
validator: native
description: Inventory scripts must include _meta.hostvars in JSON output (enforced in 2.23)
scope: task
---

## Inventory script missing _meta (M029)

Inventory scripts must include _meta.hostvars in JSON output (enforced in 2.23)

**Removal version**: 2.23
**Fix tier**: 3
**Audience**: content

### Detection

Analyze inventory script output for missing _meta key

### Example: violation

```yaml
#!/usr/bin/env python3
# inventory script without _meta
import json
print(json.dumps({"all": {"hosts": ["host1"]}}))
```

### Example: pass

```yaml
#!/usr/bin/env python3
import json
print(json.dumps({"all": {"hosts": ["host1"]}, "_meta": {"hostvars": {}}}))
```

### Remediation

Informational only -- requires modifying external scripts
