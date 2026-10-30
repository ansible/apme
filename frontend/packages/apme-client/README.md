# @apme/client

The existing APME Gateway client, extracted for native UI and Backstage consumers.
This package has no React, Backstage, Portal, AAP or UI dependency. It exports
the `/api/v1` helpers and types, plus HTTP/WS/SSE addressing under `./transport`.

```ts
import { createDefaultApmeApiAdapter, listProjects } from "@apme/client";

const adapter = createDefaultApmeApiAdapter({
    apiBase: "https://host.example/api/catalog/apme",
    fetch: hostAuthenticatedFetch,
});
const projects = await listProjects(25, 0, "created_at", "desc", adapter);
```

Pass adapters explicitly in host applications. A module default remains for
compatible native/non-React callers; a React provider never mutates that default.
Gateway contracts are preserved, including structured results and SCM ownership.

Build and pack from the repository root through `tox -e frontend-pack`. Product
release versions follow ADR-066/073. See `frontend/backstage/README.md`.

AI provider normalization and merging in `src/aiProviders.ts` are imported from
`ansible/ansible-backstage-plugins` commit
`6ab7fce5a37406c8958d1dc0291a4bc96eb1d3f5`,
`workspaces/apme/plugins/backstage-apme-common/src/types/index.ts`, with its
Apache-2.0 header preserved. Public summaries never include credential fields.

`createHostSessionSocket` carries the unchanged FixSession session protocol over
an authenticated host HTTP channel. Hosts explicitly inject it through
`openSessionSocket`; ordinary client requests never choose another backend after
an authorization or transport failure.
