---
rule_id: R104
validator: native
description: Download from unauthorized source (annotation-based).
scope: task
ai_prompt: |
  This rule flags downloads from non-HTTPS URLs. If the URL targets
  localhost, a private network, or an internal mirror that does not support
  TLS, add "# noqa: R104" — but DO NOT modify the URL itself. Your
  explanation MUST justify why plain HTTP is acceptable, e.g. "URL targets
  localhost" or "internal mirror without TLS support." Otherwise, change
  the URL scheme from http:// to https://.
---

## Unauthorized download (R104)

Download from unauthorized source. Flags HTTP (non-HTTPS) URLs.

### Example: violation

```yaml
- name: Download from HTTP
  ansible.builtin.get_url:
    url: http://example.com/file.tar.gz
    dest: /tmp/file.tar.gz
```

### Example: pass

```yaml
- name: Download from HTTPS
  ansible.builtin.get_url:
    url: https://example.com/file.tar.gz
    dest: /tmp/file.tar.gz
```
