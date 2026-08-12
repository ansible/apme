# TASK-005: UI and Documentation

## Parent Requirement

REQ-016: Phase 2 SCM Providers (GitLab + Bitbucket)

## Status

Complete

## Description

Add optional `scm_provider` select in the UI, broaden SCM token copy, document
new env vars, and note Phase 2 completion in ADR-050.

## Prerequisites

- [x] Providers registered (TASK-002–004)

## Implementation Notes

1. Projects create/edit: provider select (auto / github / gitlab / bitbucket).
2. Update token placeholders and help text.
3. Document `APME_GITLAB_API_URL` / `APME_BITBUCKET_API_URL` in Gateway/deploy docs.
4. ADR-050 revision history entry.

## Files to Create/Modify

| File | Action | Purpose |
|------|--------|---------|
| `frontend/src/pages/ProjectsPage.tsx` | Modify | Provider select + copy |
| `frontend/src/pages/ProjectDetailPage.tsx` | Modify | Provider select + copy |
| Docs / Helm env examples | Modify | New env vars |
| `.sdlc/adrs/ADR-050-...md` | Modify | Revision note |

## Verification

- [x] UI builds / types OK
- [x] Docs mention new env vars

## Acceptance Criteria Reference

- Self-hosted requires explicit `scm_provider` (UX support)
