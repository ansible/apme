import { ConfigReader } from "@backstage/config";
import { createDefaultApmeApiAdapter, getHealth } from "@apme/client";
import { apmeHostApiFactory } from "../plugins/backstage-apme/src/api/apmeApi";
import { createApmeUiWorkflowAdapter } from "../plugins/backstage-apme/src/api/createApmeUiWorkflowAdapter";
import { renderHook, act, waitFor } from "@testing-library/react";
import {
  ApmeApiProvider,
  useProjectOperationActions,
  useProjectOperationState,
} from "@apme/ui-workflow/headless";
import { createElement, type ReactNode } from "react";

describe("host transports", () => {
  it("discovers the Local host proxy and preserves authenticated fetch for REST", async () => {
    const fetch = vi.fn().mockResolvedValue(new Response('{"status":"ok"}'));
    const discoveryApi = {
      getBaseUrl: vi.fn().mockResolvedValue("https://local.test/api/proxy/"),
    };
    const api = apmeHostApiFactory.factory({
      discoveryApi,
      fetchApi: { fetch },
      configApi: new ConfigReader({}),
    });
    const adapter = await api.getAdapter();
    expect(adapter.apiBase).toBe("https://local.test/api/proxy/apme");
    expect(discoveryApi.getBaseUrl).toHaveBeenCalledWith("proxy");
    await expect(getHealth(adapter)).resolves.toEqual({ status: "ok" });
    expect(fetch).toHaveBeenCalledWith(
      "https://local.test/api/proxy/apme/health",
      expect.anything(),
    );
  });

  it("keeps Portal on its catalog-authorized operation path, including encoded IDs", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response('{"operation_id":"op"}'));
    const adapter = await createApmeUiWorkflowAdapter({
      discoveryApi: {
        getBaseUrl: async () => "https://portal.test/api/catalog/",
      },
      fetchApi: { fetch },
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(ApmeApiProvider, { adapter, children });
    const { result } = renderHook(
      () => useProjectOperationActions("team/repo"),
      { wrapper },
    );
    await act(() => result.current.start("check", { assess_pause: true }));
    expect(fetch).toHaveBeenCalledWith(
      "https://portal.test/api/catalog/apme/projects/team%2Frepo/operation",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          action: "check",
          options: { assess_pause: true },
        }),
      }),
    );
  });

  it("does not fall back around a denied Portal request", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValue(new Response("Forbidden", { status: 403 }));
    const adapter = createDefaultApmeApiAdapter({
      apiBase: "https://portal.test/api/catalog/apme",
      fetch,
    });
    await expect(getHealth(adapter)).rejects.toThrow("403");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("uses the same host fetch for SSE and aborts the subscription on unmount", async () => {
    let eventSignal: AbortSignal | undefined;
    const snapshot = {
      project_id: "repo",
      operation_id: "op",
      status: "checking",
      progress: [],
      proposals: [],
    };
    const fetch = vi.fn(
      async (input: RequestInfo | URL, init?: RequestInit) => {
        if (String(input).includes("/events")) {
          eventSignal = init?.signal ?? undefined;
          return new Response(
            new ReadableStream({
              start(controller) {
                controller.enqueue(
                  new TextEncoder().encode(
                    `event: state\ndata: ${JSON.stringify(snapshot)}\n\n`,
                  ),
                );
              },
            }),
            { headers: { "Content-Type": "text/event-stream" } },
          );
        }
        return new Response(JSON.stringify(snapshot));
      },
    );
    const adapter = createDefaultApmeApiAdapter({
      apiBase: "https://portal.test/api/catalog/apme",
      fetch,
    });
    const wrapper = ({ children }: { children: ReactNode }) =>
      createElement(ApmeApiProvider, { adapter, children });
    const { unmount } = renderHook(() => useProjectOperationState("repo"), {
      wrapper,
    });
    await waitFor(() => expect(eventSignal).toBeDefined());
    expect(
      fetch.mock.calls.some(([url]) =>
        String(url).startsWith("https://portal.test/api/catalog/apme/"),
      ),
    ).toBe(true);
    unmount();
    expect(eventSignal?.aborted).toBe(true);
  });
});
