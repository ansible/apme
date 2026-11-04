/*
 * Copyright Red Hat
 */

import {
  createApiFactory,
  configApiRef,
  discoveryApiRef,
  fetchApiRef,
  type ApiFactory,
  type ConfigApi,
  type DiscoveryApi,
  type FetchApi,
} from "@backstage/core-plugin-api";
import { apmeApiRef, ApmeApiClient, type ApmeApi } from "./ApmeApi";

/**
 * Registers plugin.apme.api.
 * Monolith: packages/app/src/apis.ts. RHDH: apiFactories in the host
 * pluginConfig (see app-config.janus-idp.yaml for the snippet to copy).
 * Do not also list on createPlugin({ apis }) — that duplicates the factory in dynamic mode.
 */
export const apmeApiFactory: ApiFactory<
  ApmeApi,
  ApmeApi,
  {
    discoveryApi: DiscoveryApi;
    fetchApi: FetchApi;
    configApi: ConfigApi;
  }
> = createApiFactory({
  api: apmeApiRef,
  deps: {
    discoveryApi: discoveryApiRef,
    fetchApi: fetchApiRef,
    configApi: configApiRef,
  },
  factory: ({ discoveryApi, fetchApi, configApi }) => {
    const mockMode =
      configApi.getOptionalBoolean("ansible.apme.mockMode") ?? false;
    if (mockMode) {
      // Keep the optional mock client out of the production bundle.
      // eslint-disable-next-line @typescript-eslint/no-require-imports -- lazy CommonJS load avoids bundling mock-only code.
      const { MockApmeApiClient } =
        require("./mock/MockApmeApiClient") as typeof import("./mock/MockApmeApiClient");
      return new MockApmeApiClient();
    }
    const submitTimeoutMs =
      configApi.getOptionalNumber("ansible.apme.submitTimeoutMs") ?? 300_000;
    return new ApmeApiClient({ discoveryApi, fetchApi, submitTimeoutMs });
  },
});
