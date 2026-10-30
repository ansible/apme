import { getActivity, listProjectActivity, listProjects } from "@apme/client";
import { createPortalApmeApiAdapter } from "../plugins/backstage-apme/src/api/createPortalApmeApiAdapter";

const discoveryApi = {
  getBaseUrl: async () => "https://portal.test/api/catalog",
};

it("normalizes current Portal repository and history lists without promising exact totals", async () => {
  const fetch = vi.fn(
    async (input: RequestInfo | URL) =>
      new Response(
        JSON.stringify(
          String(input).includes("/activity")
            ? Array.from({ length: 30 }, (_, id) => ({ scan_id: `run${id}` }))
            : { items: [{ id: "project" }] },
        ),
      ),
  );
  const adapter = await createPortalApmeApiAdapter({
    discoveryApi,
    fetchApi: { fetch },
  });
  expect(
    await listProjects(25, 0, "created_at", "desc", adapter),
  ).toMatchObject({
    total: 1,
    total_is_exact: false,
    items: [{ id: "project" }],
  });
  const history = await listProjectActivity("team/repo", 25, 25, adapter);
  expect(history.total_is_exact).toBe(false);
  expect(history.items).toHaveLength(5);
  expect(history.items[0]).toEqual({ scan_id: "run25" });
  expect(
    fetch.mock.calls.every(([url]) =>
      String(url).startsWith("https://portal.test/api/catalog/apme/"),
    ),
  ).toBe(true);
});

it("preserves a future paginated Portal response and does not slice a server page twice", async () => {
  const upstream = new Response(
    JSON.stringify({
      items: [{ scan_id: "run25" }],
      total: 100,
      limit: 25,
      offset: 25,
    }),
  );
  const fetch = vi.fn().mockResolvedValue(upstream);
  const adapter = await createPortalApmeApiAdapter({
    discoveryApi,
    fetchApi: { fetch },
  });
  const response = await adapter.fetch(
    "https://portal.test/api/catalog/apme/projects/p/activity?limit=25&offset=25",
  );
  expect(response).toBe(upstream);
  expect((await response.json()).items).toHaveLength(1);
});

it.each([403, 404, 500])(
  "preserves a denied/failed response (%s) with no fallback request",
  async (status) => {
    const upstream = new Response("denied", { status });
    const fetch = vi.fn().mockResolvedValue(upstream);
    const adapter = await createPortalApmeApiAdapter({
      discoveryApi,
      fetchApi: { fetch },
    });
    expect(
      await adapter.fetch("https://portal.test/api/catalog/apme/projects"),
    ).toBe(upstream);
    expect(fetch).toHaveBeenCalledTimes(1);
  },
);

it("does not consume or transform a workflow event stream", async () => {
  const upstream = new Response("event: state\ndata: {}\n\n", {
    headers: { "Content-Type": "text/event-stream" },
  });
  const fetch = vi.fn().mockResolvedValue(upstream);
  const adapter = await createPortalApmeApiAdapter({
    discoveryApi,
    fetchApi: { fetch },
  });
  expect(
    await adapter.fetch(
      "https://portal.test/api/catalog/apme/projects/p/operation/events",
    ),
  ).toBe(upstream);
  expect(upstream.bodyUsed).toBe(false);
});

it("rejects a malformed list and preserves encoded activity identities", async () => {
  const fetch = vi.fn(
    async (input: RequestInfo | URL) =>
      new Response(
        JSON.stringify(
          String(input).includes("/projects?")
            ? { wrong: [] }
            : { scan_id: "run/one" },
        ),
      ),
  );
  const adapter = await createPortalApmeApiAdapter({
    discoveryApi,
    fetchApi: { fetch },
  });
  await expect(
    listProjects(25, 0, "created_at", "desc", adapter),
  ).rejects.toThrow("invalid repository/history list");
  await getActivity("run/one", adapter);
  expect(fetch).toHaveBeenLastCalledWith(
    "https://portal.test/api/catalog/apme/activity/run%2Fone",
    expect.anything(),
  );
});
