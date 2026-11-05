# TASK-001: Repair Runtime Module and Inbound Risk Findings

## Parent Requirement

REQ-022: Close Rule Parity and Risk Assessment Gaps

## Status

Complete

## Description

Emit L037 from semantic ansible-core plugin resolution and make R118 receive
inbound risk annotations from the active OPA payload path.

## Prerequisites

- None

## Implementation Notes

1. Include task collection order and scan-root Ansible configuration in plugin
   resolution; emit L037 only for confirmed unresolved, non-removed modules.
2. Build R118 annotation payloads from APME's inbound risk profiles during OPA
   hierarchy serialization.
3. Require `remote_src: true` for unarchive, retain potentially remote
   templates, and preserve the annotation fields consumed by R118.
4. Update rule docs and the coverage review to describe the active behavior.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `src/apme_engine/validators/ansible/rules/M001_M004_introspect.py` | Modify | Emit semantic L037 results |
| `src/apme_engine/engine/graph_opa_payload.py` | Modify | Populate inbound risk annotations for OPA |
| `src/apme_engine/validators/ansible/rules/L037.md` | Add | Document the authoritative runtime implementation |
| `docs/rules/APME_ANSIBLE_LINT_ARI_REVIEW.md` | Modify | Update findings after repair |

## Verification

- `tox -e lint`
- `tox -e unit`
- Confirm valid short names resolve without L037 and unknown names produce L037.
- Confirm R118 receives annotations for inbound profile modules and excludes
  local unarchive sources.

## Acceptance Criteria Reference

- REQ-022: semantic module resolution and operational inbound transfer results.

## Notes

Review validation added `tests/test_rule_parity_followups.py`, including real
Ansible module/action resolution with project configuration, remote/local
source classification, unarchive boolean handling, and credential redaction.
The review repaired bare-host SCP source detection and initialized the plugin
loaders used by L058/L059 so omitted required arguments are checked against
real module specs, including documentation fragments.
