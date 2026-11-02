# REQ-021 Package and Host Contracts

`@apme/client` exports existing Gateway helpers/types and `./transport`.
`@apme/ui-workflow` preserves its root entry point and adds `./headless` and the isolated `./graph` presentation entry.
`@apme/backstage-plugin` exports a routable Local page, an embeddable project
workflow, API ref/factory and the original `createApmeUiWorkflowAdapter` Portal
transport helper. New package names do not replace the existing Portal plugin
ID. Its `./alpha` export declares the standard NFS API and Projects page.

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

`@apme/backstage-plugin-backend` supplies the personal Local backend plugin and
`createUploadSessionRouter` for explicit Portal authorization (ADR-074). Its
artifact is released with the other three packages. The client adapter can
provide `openSessionSocket`; embedded uploads require that transport and an
explicit host permission. Commands and streamed responses carry existing
Gateway session frames; unknown ownership/resume bindings fail closed.

Two additional Portal-only artifacts transfer the original guest integration:
`@apme/portal-plugin` exports static NFS declarations through `./alpha`, its
manifest through `./manifest`, and a legacy development-shell API through `./api`.
`@apme/portal-plugin-backend` exports catalog and scaffolder modules through
`./catalog` and `./scaffolder`. Their internal common contracts are bundled, and
Portal SDK/AAP dependencies are host-provided peers. The four standalone packages
remain free of `@ansible/*` dependencies. The six tarballs share a product release.

The adapters own `/api/catalog/apme/*` handlers, settings and operation descriptors;
the `apme` settings permission resource remains compatible with the host. Scan
operations require `apme.scanning`; repository registration/removal and settings
retain their independent permission checks and do not require a scanning license.
Settings get/save operations use the same validation and persistence as HTTP.
The registration template ships with the catalog module and requires no repository
catalog file. Native composition activates descriptors statically: runtime manifest
mounting, settings/nav blueprints and installed-plugin discovery remain host work.

`tox -e portal-adoption -- --version X.Y.Z` generates a reviewable downstream
patch. APME must release those artifacts before Portal can pin their immutable
URLs and delete its old APME workspace after workflow acceptance.
