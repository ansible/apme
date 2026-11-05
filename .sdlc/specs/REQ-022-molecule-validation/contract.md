# REQ-022: Contract

## Transport

Use Validator.Validate and Validator.Health without introducing a service or
changing REST/protobuf fields. ValidateRequest.files is the authority for file
content; content_graph_data is optional for file rules. Files outside that
snapshot, process environment, and server home configuration are not implicit
inputs. All blocking YAML/schema evaluation runs in the executor.

## Rules

| ID | Name | Responsibility |
|----|------|----------------|
| L112 | molecule-config | YAML and effective configuration schema |
| L113 | molecule-reference | Configured local file references |
| L114 | molecule-sequence | Action names and action argument structure |
| L115 | molecule-context | Explain validation limited by unavailable context |
| L116 | molecule-playbook | YAML/structural errors in referenced Ansible playbooks |

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

The first profile is Molecule 24.9.0, documented as distributed with Red Hat
ADT/AAP 2.5. The schema is pinned to upstream commit
4e2592e5d77b181133243b8b685933f6daff3897. A broad numeric condition such as
molecule>=6 is not a Red Hat lifecycle/support assertion.
