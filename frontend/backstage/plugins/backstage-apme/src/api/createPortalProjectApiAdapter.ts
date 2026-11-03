/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import type { DiscoveryApi, FetchApi } from "@backstage/core-plugin-api";
import {
  createDefaultApmeApiAdapter,
  type ApmeApiAdapter,
} from "@apme/client/transport";
import { createHostSessionSocket } from "@apme/client";
import { createApmeUiWorkflowAdapter } from "./createApmeUiWorkflowAdapter";

/** Companion lossless routes authorize the selected project before forwarding. */
export async function createPortalProjectApiAdapter(options: {
  discoveryApi: DiscoveryApi;
  fetchApi: FetchApi;
  projectId: string;
  enableUploads?: boolean;
}): Promise<ApmeApiAdapter> {
  const adapter = await createApmeUiWorkflowAdapter(options);
  const base = new URL(adapter.apiBase, adapter.origin);
  const projectPath = `/projects/${encodeURIComponent(options.projectId)}`;
  return createDefaultApmeApiAdapter({
    ...adapter,
    ...(options.enableUploads
      ? {
          openSessionSocket: (url: string) =>
            createHostSessionSocket(adapter, url),
        }
      : {}),
    async fetch(input, init) {
      const request = input instanceof Request ? input : undefined;
      const url = new URL(request?.url ?? String(input), base);
      const prefix = base.pathname.replace(/\/$/, "");
      const method = (init?.method ?? request?.method ?? "GET").toUpperCase();
      if (
        method !== "GET" ||
        url.origin !== base.origin ||
        !url.pathname.startsWith(`${prefix}/`)
      )
        return adapter.fetch(input, init);
      const path = url.pathname.slice(prefix.length);
      let next: string | undefined;
      if (
        path === "/rules" ||
        ["/ai/config", "/ai/providers", "/ai/engines"].includes(path)
      )
        next = `/shared${path}`;
      else if (
        path === projectPath ||
        ["activity", "violations", "dependencies", "graph", "sbom"].some(
          (suffix) => path === `${projectPath}/${suffix}`,
        )
      )
        next = `/shared${path}`;
      else if (/^\/activity\/[^/]+$/.test(path))
        next = `/shared${projectPath}/activity/${path.slice("/activity/".length)}`;
      if (!next) return adapter.fetch(input, init);
      url.pathname = prefix + next;
      return adapter.fetch(request ? new Request(url, request) : url, init);
    },
  });
}
