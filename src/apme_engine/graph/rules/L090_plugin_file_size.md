---
rule_id: L090
validator: native
description: Plugin entry files should be small; move helpers to module_utils.
scope: collection
---

## Plugin file size (L090)

Plugin entry files should be small. Move helper functions and utilities to `module_utils` or `plugin_utils`.

### Example: violation

A plugin module file exceeding 500 lines.

### Example: pass

A plugin module file under 500 lines with helpers in `module_utils/`.
