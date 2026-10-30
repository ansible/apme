# REQ-021 Package and Host Contracts

`@apme/client` exports existing Gateway helpers/types and `./transport`.
`@apme/ui-workflow` preserves its root entry point and adds `./headless`.
`@apme/backstage-plugin` exports a routable Local page, an embeddable project
workflow, API ref/factory and the original `createApmeUiWorkflowAdapter` Portal
transport helper. New package names do not replace the existing Portal plugin
ID or promise its full legacy export surface yet.

An `ApmeApiAdapter` supplies `fetch`, `apiBase` and `origin`. Hosts provide an
authenticated fetch and an explicit backend path. Requests must not retry at a
direct Gateway origin after permission denial. Gateway operation state remains
authoritative; controllers do not create a parallel lifecycle or durable store.

Product release tarballs use the product version and immutable GitHub Release
URLs for package-to-package dependencies. Local verification can pack the same
artifacts with local tarball references for a clean install smoke check. Preserve
the `apme-ui-workflow-X.Y.Z.tgz` asset name expected by existing consumers.
