/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import {
  configApiRef,
  createApiFactory,
  createApiRef,
  discoveryApiRef,
  fetchApiRef,
  type ApiFactory,
  type ConfigApi,
  type DiscoveryApi,
  type FetchApi,
} from "@backstage/core-plugin-api";
import {
  createDefaultApmeApiAdapter,
  type ApmeApiAdapter,
} from "@apme/client/transport";

export interface ApmeHostApi {
  getAdapter(): Promise<ApmeApiAdapter>;
  /** Local single-person deployment enables this; Portal owns its RBAC decision. */
  canManageRules?: boolean;
  canViewSettings?: boolean;
  canManageSettings?: boolean;
  supportsGalaxyCertificateSettings?: boolean;
}

export const apmeHostApiRef = createApiRef<ApmeHostApi>({
  id: "plugin.apme.host-transport",
});

/** Local uses the host proxy. Portal supplies its catalog/RBAC adapter instead. */
export const apmeHostApiFactory: ApiFactory<
  ApmeHostApi,
  ApmeHostApi,
  {
    discoveryApi: DiscoveryApi;
    fetchApi: FetchApi;
    configApi: ConfigApi;
  }
> = createApiFactory({
  api: apmeHostApiRef,
  deps: {
    discoveryApi: discoveryApiRef,
    fetchApi: fetchApiRef,
    configApi: configApiRef,
  },
  factory: ({ discoveryApi, fetchApi, configApi }): ApmeHostApi => ({
    canManageRules: true,
    canViewSettings: true,
    canManageSettings: true,
    supportsGalaxyCertificateSettings: true,
    async getAdapter() {
      const configured = configApi.getOptionalString("apme.apiBase");
      const apiBase =
        configured ??
        `${(await discoveryApi.getBaseUrl("proxy")).replace(/\/$/, "")}/apme`;
      // Both REST and fetch-based SSE must use the authenticated host fetch.
      return createDefaultApmeApiAdapter({
        apiBase,
        fetch: fetchApi.fetch.bind(fetchApi),
        origin: /^https?:\/\//i.test(apiBase)
          ? new URL(apiBase).origin
          : window.location.origin,
      });
    },
  }),
});
