---
rule_id: L077
validator: native
description: Roles should have meta/argument_specs.yml for fail-fast parameter validation.
scope: role
---

## Role argument specs (L077)

Roles should declare `argument_specs` for fail-fast parameter validation (Ansible 2.11+). This can be inline in `meta/main.yml` or in a standalone `meta/argument_specs.yml` file.

### Example: violation

```yaml
# roles/myrole/meta/main.yml
galaxy_info:
  author: acme
dependencies: []
```

### Example: pass

```yaml
# roles/myrole/meta/main.yml
galaxy_info:
  author: acme
dependencies: []
argument_specs:
  main:
    short_description: Configure the service.
    options:
      service_port:
        type: int
        description: Port to listen on.
        default: 8080
```
