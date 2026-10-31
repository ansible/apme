# REQ-021 Package and Host Contracts

`@apme/client` exports existing Gateway helpers/types and `./transport`.
`@apme/ui-workflow` preserves its root entry point and adds `./headless` and the isolated `./graph` presentation entry.
`@apme/backstage-plugin` exports a routable Local page, an embeddable project
workflow, API ref/factory and the original `createApmeUiWorkflowAdapter` Portal
transport helper. New package names do not replace the existing Portal plugin
ID or promise its full legacy export surface yet.

An `ApmeApiAdapter` supplies `fetch`, `apiBase` and `origin`. Hosts provide an
authenticated fetch and an explicit backend path. Requests must not retry at a
direct Gateway origin after permission denial. Gateway operation state remains
authoritative; controllers do not create a parallel lifecycle or durable store.

Project editing is opt-in: `ApmeHostApi.canManageProjects`, workbench
`canManageProjects`, project tabs `canManageProject`, and standalone editor
`canManage` default to denied for embedded hosts. Local's personal API factory
enables it. The editor calls the existing authenticated Gateway PATCH contract
through the supplied adapter; Portal must authorize the selected resource and
expose an update route before enabling it. Blank, untouched tokens are omitted;
explicit removal sends an empty token. Global settings remain independently
gated and Local mounts them at the host Settings page via the `./settings` export.

Product release tarballs use the product version and immutable GitHub Release
URLs for package-to-package dependencies. Local verification can pack the same
artifacts with local tarball references for a clean install smoke check. Preserve
the `apme-ui-workflow-X.Y.Z.tgz` asset name expected by existing consumers.

`@apme/backstage-backend` supplies the personal Local backend plugin and
`createUploadSessionRouter` for explicit Portal authorization (ADR-074). Its
artifact is released with the other three packages. The client adapter can
provide `openSessionSocket`; embedded uploads require that transport and an
explicit host permission. Commands and streamed responses carry existing
Gateway session frames; unknown ownership/resume bindings fail closed.
