# @apme/client

The existing APME Gateway client, extracted for native UI and Backstage consumers.
This package has no React, Backstage, Portal, AAP or UI dependency. It exports
the `/api/v1` helpers and types, plus HTTP/WS/SSE addressing under `./transport`.

```ts
import { createDefaultApmeApiAdapter, listProjects } from '@apme/client';

const adapter = createDefaultApmeApiAdapter({
  apiBase: 'https://host.example/api/catalog/apme',
  fetch: hostAuthenticatedFetch,
});
const projects = await listProjects(25, 0, 'created_at', 'desc', adapter);
```

Pass adapters explicitly in host applications. A module default remains for
compatible native/non-React callers; a React provider never mutates that default.
Gateway contracts are preserved, including structured results and SCM ownership.

Build and pack from the repository root through `tox -e frontend-pack`. Product
release versions follow ADR-066/073. See `frontend/backstage/README.md`.
