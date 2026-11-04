// @vitest-environment node
import {
  createDefaultApmeApiAdapter,
  deleteGalaxyServer,
  getHealth,
  markAllNotificationsRead,
  saveAiProvider,
} from "@apme/client";

function openResponse(status = 200) {
  const cancel = vi.fn();
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(new TextEncoder().encode("response body"));
    },
    cancel,
  });
  return { cancel, response: new Response(body, { status }) };
}

it("cancels successful mutation response bodies", async () => {
  const galaxy = openResponse();
  const notifications = openResponse();
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(galaxy.response)
    .mockResolvedValueOnce(notifications.response);
  const adapter = createDefaultApmeApiAdapter({ fetch });

  await deleteGalaxyServer(7, adapter);
  await markAllNotificationsRead(adapter);

  expect(galaxy.cancel).toHaveBeenCalledOnce();
  expect(notifications.cancel).toHaveBeenCalledOnce();
});

it.each(["not ok", "degraded", "unknown"])(
  "rejects a non-healthy Gateway status (%s)",
  async (status) => {
    const fetch = vi.fn(async () =>
      Response.json({ status, database: "ok", components: [] }),
    );
    await expect(
      getHealth(createDefaultApmeApiAdapter({ fetch })),
    ).rejects.toThrow("Gateway health check did not report ok");
  },
);

it("preserves abort errors while saving provider model configuration", async () => {
  const aborted = new DOMException("Request cancelled", "AbortError");
  const fetch = vi
    .fn()
    .mockResolvedValueOnce(new Response(null, { status: 204 }))
    .mockResolvedValueOnce(Response.json({ providers: {} }))
    .mockRejectedValueOnce(aborted);

  await expect(
    saveAiProvider(
      "example",
      { engine: "openai" },
      ["model-a"],
      createDefaultApmeApiAdapter({ fetch }),
    ),
  ).rejects.toBe(aborted);
});
