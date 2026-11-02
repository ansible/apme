# TASK-016: Shared UI Layout and Density

## Parent Requirement

[REQ-021](../requirement.md), AC2–AC3; ADR-073.

## Status

Complete

## Description

Apply consistent spacing and visual hierarchy to the shared APME views. Consolidate
project-list actions, simplify table links and reduce row padding, align rule
filters, group settings in bounded form sections, and place paste/upload inputs
alongside each other on wider screens. Preserve host theme tokens, existing
navigation, permissions and workflow behavior. Styles are scoped to APME and ship
with the plugin for both host compositions.

## Verification

- Frontend type/format checks and existing 90 workflow/transport regressions.
- Clean packed-consumer install, public types and browser bundle including CSS.
- RHDH dynamic plugin export and Local image build.
- Visual inspection of Local projects, project workflow, rules, health, settings
  and paste/upload in light/dark themes and at narrow widths using fixture data.
- Repository lint and Python unit suite: 4,976 passed, 85.58% coverage.

## Architecture

Presentation-only changes within ADR-073's shared artifact contract. No host
identity checks, new API calls, service boundary changes or new ADR required.

## Evidence

The standalone and Portal packed-consumer smoke checks passed, including CSS
resolution in the browser bundles. The Local image built through `tox -e build-ui`
with the Buildah runner override. Browser inspection used an isolated Local host
with fixture data; live Gateway/Engine acceptance remains tracked in #833.
