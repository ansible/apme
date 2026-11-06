# REQ-023: Contract

## Transport

Use Validator.Validate and Validator.Health without introducing a service or
changing REST/protobuf fields. ValidateRequest.files is the authority for file
content; content_graph_data is optional for file rules. Files outside that
snapshot, process environment, and server home configuration are not implicit
inputs. All blocking YAML/schema evaluation runs in the executor.

## Rules

| ID | Name | Responsibility |
|----|------|----------------|
| L115 | molecule-config | YAML and effective configuration schema |
| L116 | molecule-reference | Configured local file references |
| L117 | molecule-sequence | Action names and action argument structure |
| L118 | molecule-context | Explain validation limited by unavailable context |
| L119 | molecule-playbook | YAML/structural errors in referenced Ansible playbooks |

All rules are enabled by default and tagged `molecule`. Initial findings use
warning-level severity with ordinary APME overrides and suppression. File-based
findings use an existing appropriate non-task scope and remain manual review;
they must never be routed as an Ansible task for automatic remediation.
Ordinary task rules keep their existing IDs and scope.

## Diagnostics

Each finding identifies rule_id, file, one-based line, YAML path, message,
source=native, and scope. A merged value points to the source layer that supplied
it. Reports never include substituted credential values. Incomplete assessment
is distinguishable from a clean assessment. One malformed scenario does not
prevent assessment of independent scenarios.

## Compatibility

The minimum compatibility target is Molecule 24.9.0+. Legacy configuration uses
24.9.0 (commit 4e2592e5d77b181133243b8b685933f6daff3897); native/shared-state
configuration uses 26.11.0 (commit ada645a132b9332c291696f553d93cce788b5410).
Select the family from merged user config before applying defaults. Explicit
legacy provisioner settings replace native equivalents during migration while
retaining their original file/line/path. Native references and sequences receive
the same checks as legacy inputs. Family selection does not assert the
installed Molecule version or Red Hat support for every upstream release.
