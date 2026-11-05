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
import { allApmeProjectViews, type ApmeProjectView } from "../capabilities";

export interface ApmeHostApi {
  getAdapter(): Promise<ApmeApiAdapter>;
  /** Supported read views, selected explicitly by the host; omission preserves existing views. */
  projectViews?: readonly ApmeProjectView[];
  /** Local single-person deployment enables this; Portal owns its RBAC decision. */
  canManageRules?: boolean;
  /** Opt-in project metadata/credential editing; Portal must authorize it. */
  canManageProjects?: boolean;
  /** Independent destructive permission; embedded hosts default to denied. */
  canDeleteProjects?: boolean;
  canViewSettings?: boolean;
  canManageSettings?: boolean;
  supportsGalaxyCertificateSettings?: boolean;
  /** Local uploads require explicit opt-in and the authenticated backend plugin. */
  canUpload?: boolean;
  /** Host-authorized project-scoped dependency triage; defaults to read-only. */
  canAcknowledgeFindings?: boolean;
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
  factory: ({ discoveryApi, fetchApi, configApi }): ApmeHostApi => {
    const canUpload =
      configApi.getOptionalBoolean("apme.uploads.enabled") === true;
    return {
      projectViews: allApmeProjectViews,
      canManageRules: true,
      canManageProjects: true,
      canDeleteProjects: true,
      canViewSettings: true,
      canManageSettings: true,
      supportsGalaxyCertificateSettings: true,
      canUpload,
      canAcknowledgeFindings: true,
      async getAdapter() {
        const configured = configApi.getOptionalString("apme.apiBase");
        const apiBase =
          configured ??
          `${(await discoveryApi.getBaseUrl("proxy")).replace(/\/$/, "")}/apme`;
        // Both REST and fetch-based SSE must use the authenticated host fetch.
        const adapterOptions = {
          apiBase,
          fetch: fetchApi.fetch.bind(fetchApi),
          origin: /^https?:\/\//i.test(apiBase)
            ? new URL(apiBase).origin
            : window.location.origin,
        };
        if (!canUpload) return createDefaultApmeApiAdapter(adapterOptions);
        const uploadApiBase =
          configApi.getOptionalString("apme.uploadApiBase") ??
          (await discoveryApi.getBaseUrl("apme"));
        return createDefaultApmeApiAdapter({
          ...adapterOptions,
          openSessionSocket: (url) =>
            createHostSessionSocket(
              createDefaultApmeApiAdapter({
                apiBase: uploadApiBase,
                fetch: fetchApi.fetch.bind(fetchApi),
              }),
              url,
            ),
        });
      },
    };
  },
});
