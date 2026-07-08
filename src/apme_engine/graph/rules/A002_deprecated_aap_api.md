---
rule_id: A002
validator: native
description: Tasks should not use deprecated AAP API endpoints or deprecated ansible.hub modules.
scope: task
---

## Deprecated AAP API (A002)

Tasks should not use deprecated AAP API endpoints or modules. AAP 2.5 introduced the platform gateway with new API paths. Legacy endpoints are deprecated and will be removed in AAP 2.7.

### API Path Changes

| Deprecated | Replacement | Service |
|------------|-------------|---------|
| `/api/v2/job_templates/` | `/api/controller/v2/job_templates/` | Controller |
| `/api/v2/inventories/` | `/api/controller/v2/inventories/` | Controller |
| `/api/v2/collections/` | `/api/hub/v2/collections/` | Hub |
| `/api/v2/namespaces/` | `/api/hub/v2/namespaces/` | Hub |

### Deprecated Modules

| Deprecated | Replacement | Removed In |
|------------|-------------|------------|
| `ansible.hub.ah_token` | `ansible.platform.token` | AAP 2.7 |
| `ansible.hub.ah_user` | `ansible.platform.user` | AAP 2.7 |

### Example: fail

```yaml
- name: Get job template (deprecated path)
  ansible.builtin.uri:
    url: "https://controller.example.com/api/v2/job_templates/"
    method: GET

- name: Create token (deprecated module)
  ansible.hub.ah_token:
    state: present
```

### Example: pass

```yaml
- name: Get job template (gateway path)
  ansible.builtin.uri:
    url: "https://controller.example.com/api/controller/v2/job_templates/"
    method: GET

- name: Create token (platform collection)
  ansible.platform.token:
    state: present
```

### Rationale

- Legacy `/api/v2/` endpoints will be removed in AAP 2.7
- Platform gateway provides unified access to all AAP services
- `ansible.platform` collection replaces deprecated `ansible.hub` modules
- Early migration prevents breaking changes during AAP upgrades
