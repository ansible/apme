# APME Gateway REST API (OpenAPI)

Machine-readable baseline for the Gateway HTTP surface under `/api/v1`
(ADR-060). Portal, the Backstage APME plugin, and other external consumers
can use [`openapi.v1.json`](openapi.v1.json) offline without a running stack.

Runtime FastAPI still serves live `/docs` and `/openapi.json` when the
Gateway is running.

## What this file guarantees

- **Freshness:** CI fails if the committed JSON does not match
  `create_app().openapi()` (`tox -e openapi -- --check`).
- **Versioning policy:** Additive-only changes under `/api/v1` without a
  version bump — enforced by ADR-060 review, **not** by OpenAPI
  break-diff tooling (that remains future work).

## Known limitations (read before codegen)

- **WebSockets are not in the artifact.** FastAPI OpenAPI export omits
  `/api/v1/ws/session` and `/api/v1/projects/{id}/ws/operate`. Those
  remain live Gateway surfaces; treat them as out-of-band from this file.
- **SSE / some binary or stream responses may be under-specified.**
  Paths such as operation event streams may appear as
  `application/json` with an empty schema even though the runtime uses
  `text/event-stream` (or other media types). Prefer handler docs and
  ADR-052 for event shapes until schemas are annotated.
- **Do not hand-edit** `openapi.v1.json` — regenerate from the app.

## Regenerate

After changing Gateway routes or Pydantic response models:

```bash
tox -e openapi
```

Commit the updated `openapi.v1.json` with your PR.

## Check (CI / prek)

```bash
tox -e openapi -- --check
```

The `openapi-check` prek hook and the `prek` workflow both run this check.
