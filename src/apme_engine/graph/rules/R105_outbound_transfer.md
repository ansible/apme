---
rule_id: R105
validator: native
description: Outbound transfer (annotation-based).
scope: task
ai_prompt: |
  This rule flags outbound data transfers to parameterized URLs. Evaluate
  whether the destination is a well-known, trusted endpoint (e.g., an
  internal API, a monitoring webhook). If the transfer is intentional and
  the destination is trusted, add "# noqa: R105" — but DO NOT modify the
  task itself. Your explanation MUST justify why the destination is safe,
  e.g. "sends to internal monitoring API" or "webhook URL is defined in
  group_vars." If the destination variable could be attacker-controlled,
  flag it for the user.
---

## Outbound transfer (R105)

Flags outbound data transfers (uri PUT/POST/PATCH) where the destination
URL contains Jinja2 template syntax. A parameterized destination is a
data-exfiltration risk when variables are externally controlled.

### Example: violation

```yaml
- name: Send data to parameterized endpoint
  ansible.builtin.uri:
    url: "{{ webhook_url }}/api/data"
    method: POST
    body: "{{ payload }}"
    body_format: json
```

### Example: pass

```yaml
- name: Fixed URL request
  ansible.builtin.uri:
    url: https://api.example.com/status
    method: GET
```
