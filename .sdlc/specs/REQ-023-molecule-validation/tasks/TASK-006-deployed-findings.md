# TASK-006: Correct Deployed Molecule Findings

## Parent Requirement

[REQ-023: Static Molecule Validation](../requirement.md)

## Status

Complete

## Description

Review deployed Native output showing six collection imports classified as
missing local files, two notices for Molecule's generated inventory path in
executor arguments, and one shared optional-stage reference without scenario
context. Preserve coverage notices where collection/generated content cannot
be inspected and explicit warnings for absent optional stage files.

## Implementation Notes

- Resolve collection imports from uploaded standard layouts and source metadata;
  use L118 when collection content is unavailable.
- Recognize generated runtime paths symbolically; never read host state or claim
  the generated files exist.
- Keep explicit missing optional-stage warnings with scenario attribution.
- Address verified review findings about quadratic origin merging and excessive
  inline suppression scope. Preserve full source excerpts for fingerprints.
- Keep collection playbooks out of L057 runtime dispatch, including collections
  identified by JSON metadata and extensionless intermediate imports. Forward
  the complete normalized uploaded snapshot into static exclusion lookup.
  Standalone callers without the complete byte map receive an explicit L057
  coverage error and skip runtime syntax checks when Molecule content is detected.
- Cache valid/invalid collection metadata, preserve collection shadowing, validate
  collection identifiers, and resolve sibling imports in enclosing collection layouts.

## Verification

- [x] `tox -e unit -- tests/test_molecule_validation.py tests/test_L057_molecule_playbooks.py --no-cov`
- [x] `tox -e lint`
- [x] Full `tox -e unit` with local OPA and container-only skips documented

## Completion Checklist

- [x] Regression fixtures cover deployed output, collection layouts, unavailable
      content, runtime variables, missing optional references, source attribution,
      suppression isolation, and bounded merge traversal
- [x] Governing contract, guide, rule documentation, and ADR-076 updated
- [x] Changes validated for PR #859
