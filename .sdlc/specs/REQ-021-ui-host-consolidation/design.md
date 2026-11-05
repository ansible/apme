# REQ-021 Design

## Package boundaries

- `frontend/packages/apme-client` (`@apme/client`): extract the existing native
  Gateway client/types and the workflow's plain transport helpers. No React,
  Backstage, PatternFly or Portal dependency. Preserve native imports through
  re-exports and the existing workflow API exports.
- `frontend/packages/ui-workflow`: retain shared controllers and presentation;
  add a headless export with no CSS or runtime presentation dependency.
- `frontend/backstage/plugins/backstage-apme` (`@apme/backstage-plugin`): build
  on the original plugin's discovery/fetch adapter, use BUI for project host
  controls and the existing workflow for assessment/remediation. Expose the
  existing Portal catalog adapter alongside standard Local proxy discovery.
- `frontend/backstage/dev`: a development harness with Backstage API context,
  React 18, BUI and Router 6. It is not a separate supported product SPA.

The native React 19 workspace and the React 18 host workspace temporarily have
separate lockfiles. Shared client/workflow source is authoritative in APME. The
Portal repository still owns its existing composition until its SDK/capability
adapters consume released replacement packages; do not claim that migration is
already complete.

## Transport

Standard Local integration uses Backstage's proxy discovery and authenticated
fetch to reach a configured Gateway. Portal uses its existing catalog APME
backend and authorization. Shared UI receives an adapter rather than choosing
between policies. HTTP and SSE use the same host fetch. Existing WebSocket
upload security remains a separate transport work item before exposing uploads
through Portal.

## Compatibility

Current RHDH main and Portal main both declare Backstage 1.54.9, React 18.3.1
(Portal allows React 18), Router 6.30.6 and BUI 0.17.1. Start with that common
baseline and Node 24.15.0. Upstream latest Backstage 1.56.2/BUI 0.19.0 and React
19 do not establish support in those current hosts. Refresh pins when both
hosts can consume the newer tuple. See the workspace README for exact evidence.

## Delivery

First extract and pack the shared packages and prove project list/create/check/
remediation through the host transport. Next port remaining native and Portal
domain surfaces, integrate Portal resource permissions and validate real host
workflows. Finally remove native deployment/UI artifacts after AC4 is met.
