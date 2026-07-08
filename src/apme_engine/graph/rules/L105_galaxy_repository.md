---
rule_id: L105
validator: native
description: galaxy.yml should have a repository key.
scope: collection
---

## Galaxy repository (L105)

The `galaxy.yml` file should include a `repository` key pointing to the source code repository. This helps users find the project and contribute.

Maps to ansible-lint `galaxy[no-repository]`.

Applies to collection validation and checks the parsed `galaxy.yml` metadata on `COLLECTION` graph nodes.

### Example: violation

```yaml
# galaxy.yml without repository key
namespace: my_namespace
name: my_collection
version: 1.0.0
```

### Example: pass

```yaml
# galaxy.yml with repository key
namespace: my_namespace
name: my_collection
version: 1.0.0
repository: https://github.com/my_namespace/my_collection
```
