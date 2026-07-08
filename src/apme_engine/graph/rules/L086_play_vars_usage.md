---
rule_id: L086
validator: native
description: Avoid playbook/play vars for routine config; use inventory vars.
scope: play
---

## Play vars usage (L086)

Avoid defining many variables at the play level (`vars:` section). When a play has more than 5 inline variables, it suggests routine configuration that belongs in inventory `group_vars` or `host_vars` instead.

### Example: violation

```yaml
- name: Deploy app
  hosts: all
  vars:
    db_host: db.example.com
    db_port: 5432
    db_name: myapp
    db_user: admin
    app_port: 8080
    app_workers: 4
  tasks:
    - name: Show config
      ansible.builtin.debug:
        msg: "Connecting to {{ db_host }}:{{ db_port }}"
```

### Example: pass

```yaml
- name: Deploy app
  hosts: all
  vars:
    deploy_version: "2.1.0"
  tasks:
    - name: Show version
      ansible.builtin.debug:
        msg: "Deploying {{ deploy_version }}"
```
