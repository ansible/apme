---
rule_id: R117
validator: native
description: Role is from Galaxy/external source.
scope: role
---

## External role (R117)

Detects when a play references a role from Galaxy or an external source (identified by `galaxy_info` in `meta/main.yml`). External roles introduce supply-chain risk and should be reviewed, pinned to versions, and tracked in dependency manifests.

Requires role metadata; cannot be tested in the playbook-only harness.

### Example: violation

```yaml
galaxy_info:
  role_name: nginx
  author: geerlingguy
  description: Installs and configures Nginx
  license: BSD
  min_ansible_version: "2.9"
```

### Example: pass

```yaml
- name: Local play
  hosts: localhost
  tasks:
    - name: Run local task
      ansible.builtin.debug:
        msg: "No external roles referenced"
```
