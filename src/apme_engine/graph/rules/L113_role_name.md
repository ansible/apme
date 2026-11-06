---
rule_id: L113
validator: native
description: Role names should use lowercase letters, digits, and underscores.
scope: role
---

## Role name (L113)

Checks each owned role directory against ansible-lint's role-name convention:
`^[a-z][a-z0-9_]*$`. The directory name is checked, unless
`galaxy_info.role_name` supplies an override. As ansible-lint does, the rule
strips a leading `ansible-role-` before checking either name. Referenced
external roles are not checked, and role references written directly in
playbooks are not independently checked by this graph rule.

### Example: violation

```text
roles/Web-Server/
```

### Example: pass

```text
roles/web_server/
```

The prefix is also accepted:

```text
roles/ansible-role-web_server/
```
