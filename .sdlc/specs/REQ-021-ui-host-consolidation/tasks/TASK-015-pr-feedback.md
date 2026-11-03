# TASK-015: PR Feedback and CI Corrections

## Parent Requirement

[REQ-021](../requirement.md), AC1–AC5; ADR-073 and ADR-074.

## Status

Complete — implementation and local regression coverage; remote results are recorded in PR #832.

## Description

Address PR #832 review feedback, accepting cidrblock's requested reusable view
exports, explicit host capability selection and audience-oriented documentation.
Fix fresh-checkout Portal test setup (SDK module resolution and an explicit
SQLite native driver build) and upload security/resource findings,
read-only project controls, package output containment and CI configuration.
Live two-host runtime acceptance and native retirement are tracked in
[issue #833](https://github.com/ansible/apme/issues/833).

## Verification

- Frontend type/format, workflow, upload authorization/rate-limit and cleanup tests.
- Packed consumer subpath/type/browser checks and fresh-SDK Portal regressions.
- Repository lint; remote CI including Portal package tests and CodeQL.
- ADR/issue traceability, review replies and thread-state verification.

## Evidence

- 90 frontend/workflow tests; standalone and six-artifact packed-consumer checks.
- Fresh-checkout Portal backend/common tests (459) and frontend tests (517).
- RHDH image build through `tox -e build-ui` (Buildah runner override).
- Repository lint and full Python unit coverage run through tox.
- ADR-066/073/074 revised; remaining live-host acceptance tracked in #833.

The upload transport review objection is left for human review: ADR-029 allows
Gateway REST/WebSocket client ingress, while Gateway-to-Engine remains gRPC.
ADR-074 now makes that boundary explicit.
