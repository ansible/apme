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
import { createHostSessionSocket } from "@apme/client";

export interface ApmeHostApi {
  getAdapter(): Promise<ApmeApiAdapter>;
  /** Local single-person deployment enables this; Portal owns its RBAC decision. */
  canManageRules?: boolean;
  /** Opt-in project metadata/credential editing; Portal must authorize it. */
  canManageProjects?: boolean;
  canViewSettings?: boolean;
  canManageSettings?: boolean;
  supportsGalaxyCertificateSettings?: boolean;
  canUpload?: boolean;
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
    canManageProjects: true,
    canViewSettings: true,
    canManageSettings: true,
    supportsGalaxyCertificateSettings: true,
    canUpload: true,
    async getAdapter() {
      const configured = configApi.getOptionalString("apme.apiBase");
      const apiBase =
        configured ??
        `${(await discoveryApi.getBaseUrl("proxy")).replace(/\/$/, "")}/apme`;
      const uploadApiBase =
        configApi.getOptionalString("apme.uploadApiBase") ??
        (await discoveryApi.getBaseUrl("apme"));
      // Both REST and fetch-based SSE must use the authenticated host fetch.
      return createDefaultApmeApiAdapter({
        apiBase,
        fetch: fetchApi.fetch.bind(fetchApi),
        openSessionSocket: (url) =>
          createHostSessionSocket(
            createDefaultApmeApiAdapter({
              apiBase: uploadApiBase,
              fetch: fetchApi.fetch.bind(fetchApi),
            }),
            url,
          ),
        origin: /^https?:\/\//i.test(apiBase)
          ? new URL(apiBase).origin
          : window.location.origin,
      });
    },
  }),
});
