---
rule_id: L060
validator: native
description: Line too long (exceeds 160 characters).
scope: playbook
---

## Line length (L060)

Lines should not exceed 160 characters. Long lines reduce readability and make diffs harder to review.

### Example: fail

```yaml
- name: Deploy application
  ansible.builtin.get_url:
    url: https://releases.example.com/very/long/path/to/artifact/that/exceeds/the/maximum/allowed/line/length/for/ansible/playbooks/and/should/be/shortened/somehow/application.tar.gz
    dest: /opt/app
```

### Example: pass

```yaml
- name: Deploy application
  ansible.builtin.get_url:
    url: >-
      https://releases.example.com/app.tar.gz
    dest: /opt/app
```
