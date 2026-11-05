/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import type { DiscoveryApi, FetchApi } from "@backstage/core-plugin-api";
import {
  createDefaultApmeApiAdapter,
  type ApmeApiAdapter,
} from "@apme/ui-workflow/headless";

/**
 * Adapter for `@apme/ui-workflow` that talks to the catalog APME proxy
 * (`…/api/catalog/apme` → Gateway), not the Gateway origin directly.
 *
 * Uses the absolute discovery URL (same as {@link ApmeApiClient}). In
 * `yarn start` / `make react`, the FE origin (:3001) does not proxy `/api/*`
 * — relative `/api/catalog/...` returns the SPA HTML shell, so model list
 * and operation calls silently fail JSON parse.
 */
function resolveWorkflowOrigin(apiBase: string, catalogBase: string): string {
  if (apiBase.startsWith("http")) {
    return new URL(apiBase).origin;
  }
  if (typeof window !== "undefined") {
    return window.location.origin;
  }
  return catalogBase;
}

export async function createApmeUiWorkflowAdapter(options: {
  discoveryApi: DiscoveryApi;
  fetchApi: FetchApi;
}): Promise<ApmeApiAdapter> {
  const catalogBase = await options.discoveryApi.getBaseUrl("catalog");
  const apiBase = `${catalogBase.replace(/\/$/, "")}/apme`;
  return createDefaultApmeApiAdapter({
    apiBase,
    fetch: options.fetchApi.fetch.bind(options.fetchApi),
    // Prefer backend origin for absolute SSE URLs when apiBase is absolute.
    origin: resolveWorkflowOrigin(apiBase, catalogBase),
  });
}
