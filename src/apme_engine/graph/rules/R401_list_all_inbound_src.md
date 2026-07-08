---
rule_id: R401
validator: native
description: Report inbound transfer sources.
scope: playbook
---

## List inbound sources (R401)

Audit/reporting rule that aggregates all inbound-transfer source URLs in
the playbook. Fires on the PLAYBOOK node when any task uses an inbound-
transfer module (get_url, git, subversion, unarchive) with a source field.

### Example: violation

```yaml
- name: Download playbook
  hosts: localhost
  tasks:
    - name: Fetch artifact
      ansible.builtin.get_url:
        url: https://releases.example.com/app-1.0.tar.gz
        dest: /tmp/app.tar.gz
```

### Example: pass

```yaml
- name: No downloads
  hosts: localhost
  tasks:
    - name: Debug message
      ansible.builtin.debug:
        msg: hello
```
