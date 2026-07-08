---
rule_id: L055
validator: native
description: Role meta video_links should be valid URLs.
scope: role
---

## Meta video links (L055)

Role meta `video_links` entries must be valid HTTP(S) URLs.

Requires ROLE node context; cannot be tested in the playbook-only harness.

### Example: violation

```yaml
galaxy_info:
  role_name: my_role
  video_links:
    - not-a-valid-url
    - ftp://invalid-scheme.example.com/video
```

### Example: pass

```yaml
galaxy_info:
  role_name: my_role
  video_links:
    - https://www.youtube.com/watch?v=example
```
