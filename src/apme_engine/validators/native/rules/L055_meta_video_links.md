---
rule_id: L055
validator: native
description: Role meta video_links should be valid URLs.
---

## Meta video links (L055)

Role meta video_links should be valid URLs.

### Example: pass

```yaml
galaxy_info:
  role_name: my_role
  video_links:
    - https://www.youtube.com/watch?v=example
```
