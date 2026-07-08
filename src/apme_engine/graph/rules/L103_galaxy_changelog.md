---
rule_id: L103
validator: native
description: Collection should have a CHANGELOG file.
scope: collection
---

## Galaxy changelog (L103)

Collections should include a CHANGELOG file in the root directory to document changes between versions.

Maps to ansible-lint `galaxy[no-changelog]`.

### Example: violation

A collection directory without any CHANGELOG, CHANGELOG.md, or CHANGELOG.rst file.

### Example: pass

A collection directory containing a `CHANGELOG.rst` or `CHANGELOG.md` file.
