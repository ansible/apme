# TASK-014: Local APME Section Navigation

## Parent Requirement

[REQ-021](../requirement.md), AC2 and AC4; [ADR-073](../../../adrs/ADR-073-ui-host-composition.md).

## Status

Complete

## Description

Give Projects, Rules, Health and Paste / upload separate Local pages and group
them beneath a collapsible APME sidebar entry. Move global APME Settings into
that group. Keep the host's appearance/account preferences separately available
as User settings. Preserve project-specific settings inside project details.

## Implementation Notes

- Local page declarations share the authenticated host API and theme wrapper.
- Add explicit section selection and optional section-navigation visibility to
  the shared workbench; existing embedded hosts keep their current defaults.
- Local uses direct routes and hides the redundant workbench section buttons.
- Redirect old query section links and `/settings/apme` to the new Local pages.
- Keep sidebar links, selection and collapsed/hover behavior on host components.
  The grouping and new routes belong to Local; Portal composition remains host-owned.

## Verification

- Frontend type/format/build checks and existing workflow regression tests.
- Clean Local image build through tox.
- Browser inspection of all five routes, sidebar grouping/selection, collapse,
  old-link redirects, User settings, theme and narrow-screen navigation.
- Repository lint.

## Completion Evidence

2026-11-02: frontend type/format/build checks, all 75 existing host/workflow tests,
repository lint and the clean Local image build pass. Chromium inspection confirms
the five routes and selected sidebar links, grouped navigation, click/Space-key
collapse and expansion, the collapsed product icon and explicit reopening, and
narrow-screen menu navigation to Health. Light/dark theme switching works in the
standalone upload page. Old query-section links and `/settings/apme` redirect to
the new pages; User settings retains its General, Authentication Providers and
Feature Flags tabs without the old global APME settings tab. The duplicate
workbench section buttons are absent on Local routes.

The preview has no Gateway/Engine, so this establishes navigation/rendering
behavior, not scan/remediation or settings mutation acceptance. Portal's
composition is unchanged, and embedded workbench defaults pass existing tests.
