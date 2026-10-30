// @vitest-environment node
import {
  createDefaultApmeApiAdapter,
  getAiProviderSettings,
  saveAiProvider,
} from "@apme/client";

it("merges original plugin provider shapes without exposing keys", async () => {
  const adapter = createDefaultApmeApiAdapter({
    fetch: vi.fn(
      async (input) =>
        new Response(
          JSON.stringify(
            String(input).endsWith("/providers")
              ? [{ id: "managed", engine: "openai" }]
              : {
                  config: {
                    providers: {
                      managed: {
                        engine: "openai",
                        models: { model: {} },
                        apiKey: "private-key",
                      },
                      system: { engine: "ollama" },
                    },
                  },
                },
          ),
        ),
    ),
  });
  const providers = await getAiProviderSettings(adapter);
  expect(providers).toEqual([
    { id: "managed", engine: "openai", models: ["model"], source: "managed" },
    { id: "system", engine: "ollama", models: [], source: "config" },
  ]);
  expect(JSON.stringify(providers)).not.toContain("private-key");
});
it("reports a partial save if model configuration fails after credentials were stored", async () => {
  const calls: string[] = [];
  const adapter = createDefaultApmeApiAdapter({
    fetch: vi.fn(async (input) => {
      calls.push(String(input));
      return calls.length === 1
        ? new Response("{}")
        : new Response("Unavailable", { status: 503 });
    }),
  });
  await expect(
    saveAiProvider(
      "provider",
      { engine: "openai", apiKey: "new-key", secretStore: "file" },
      ["model"],
      adapter,
    ),
  ).rejects.toThrow("credentials were saved");
  expect(calls).toEqual([
    "/api/v1/ai/provider/provider/configure",
    "/api/v1/ai/config",
  ]);
});

it("preserves other providers, provider options and retained model metadata when saving", async () => {
  const updates: Record<string, unknown>[] = [];
  const fetch = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.body) updates.push(JSON.parse(String(init.body)));
    if (updates.length === 1 && init?.method === "POST")
      return new Response(null, { status: 204 });
    if (!init?.method)
      return new Response(
        JSON.stringify({
          config: {
            setting: true,
            providers: {
              provider: {
                engine: "openai",
                base_url: "https://existing",
                models: { old: { context_length: 4096 } },
              },
              other: { engine: "ollama", models: { untouched: {} } },
            },
          },
        }),
      );
    return new Response("{}");
  });
  await saveAiProvider(
    "provider",
    { engine: "openai", secretStore: "file", apiKey: "write-only" },
    ["old", "new"],
    createDefaultApmeApiAdapter({ fetch }),
  );
  expect(updates[0]).toEqual({
    engine: "openai",
    secretStore: "file",
    apiKey: "write-only",
  });
  expect(updates[1]).toEqual({
    location: "user",
    config: {
      setting: true,
      providers: {
        provider: {
          engine: "openai",
          base_url: "https://existing",
          models: { old: { context_length: 4096 }, new: {} },
        },
        other: { engine: "ollama", models: { untouched: {} } },
      },
    },
  });
});
