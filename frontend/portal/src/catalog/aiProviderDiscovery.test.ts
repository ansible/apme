import { discoverAiModelsForEngine } from "./aiProviderDiscovery";

describe("AI provider model discovery", () => {
  const originalFetch = global.fetch;
  const fetchMock = jest.fn();
  beforeEach(() => {
    jest.resetAllMocks();
    global.fetch = fetchMock;
  });
  afterEach(() => {
    global.fetch = originalFetch;
  });

  it.each([
    { engine: "openai", root: "https://api.openai.com/v1" },
    { engine: "openrouter", root: "https://openrouter.ai/api/v1" },
    { engine: "vllm", root: "https://inference.example.com/v1" },
    { engine: "custom", root: "https://inference.example.com/v1" },
  ])(
    "discovers $engine models and drops missing identifiers",
    async ({ engine, root }) => {
      fetchMock.mockResolvedValue({
        ok: true,
        json: async () => ({
          data: [{ id: "model-a" }, { id: "" }, {}, { id: "model-b" }],
        }),
      });
      await expect(
        discoverAiModelsForEngine({
          engine,
          ...(engine === "vllm" || engine === "custom"
            ? { base_url: ` ${root}/ ` }
            : {}),
          api_key: " test-key ",
        }),
      ).resolves.toEqual([
        { id: "model-a", name: "model-a", provider: engine, engine },
        { id: "model-b", name: "model-b", provider: engine, engine },
      ]);
      expect(fetchMock).toHaveBeenCalledWith(`${root}/models`, {
        headers: {
          Accept: "application/json",
          Authorization: "Bearer test-key",
        },
      });
    },
  );

  it.each(["", "   "])(
    "rejects a blank engine before making an HTTP request (%j)",
    async (engine) => {
      await expect(discoverAiModelsForEngine({ engine })).rejects.toThrow(
        "engine is required",
      );
      expect(fetchMock).not.toHaveBeenCalled();
    },
  );

  it("supports Ollama name/model fields and explicit HTTPS base URLs", async () => {
    fetchMock.mockResolvedValue({
      ok: true,
      json: async () => ({
        models: [{ name: "qwen" }, { model: "llama" }, {}],
      }),
    });
    await expect(
      discoverAiModelsForEngine({
        engine: " ollama ",
        base_url: " https://ollama.example.com/ ",
      }),
    ).resolves.toEqual([
      { id: "qwen", name: "qwen", provider: "ollama", engine: "ollama" },
      { id: "llama", name: "llama", provider: "ollama", engine: "ollama" },
      { id: "", name: "", provider: "ollama", engine: "ollama" },
    ]);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://ollama.example.com/api/tags",
      undefined,
    );
  });

  it.each(["ollama", "openai", "custom"])(
    "handles an empty %s model response without inventing models",
    async (engine) => {
      fetchMock.mockResolvedValue({ ok: true, json: async () => ({}) });
      await expect(
        discoverAiModelsForEngine({
          engine,
          base_url: "https://models.example.com",
          api_key: " ",
        }),
      ).resolves.toEqual([]);
    },
  );

  it("uses the HTTPS fallback for an unknown OpenAI-compatible engine when no base is supplied", async () => {
    fetchMock.mockResolvedValue({ ok: true, json: async () => ({ data: [] }) });
    await expect(
      discoverAiModelsForEngine({ engine: "custom" }),
    ).resolves.toEqual([]);
    expect(fetchMock).toHaveBeenCalledWith(
      "https://openrouter.ai/api/v1/models",
      { headers: { Accept: "application/json" } },
    );
  });

  it.each(["Access denied", ""])(
    "preserves HTTP failure details (%j)",
    async (detail) => {
      fetchMock.mockResolvedValue({
        ok: false,
        status: 403,
        statusText: "Forbidden",
        text: async () => detail,
      });
      await expect(
        discoverAiModelsForEngine({ engine: "openai" }),
      ).rejects.toThrow(
        `Provider discovery failed (403): ${detail || "Forbidden"}`,
      );
    },
  );

  it("propagates a transport error without returning a successful empty discovery", async () => {
    const error = new Error("Network unavailable");
    fetchMock.mockRejectedValue(error);
    await expect(discoverAiModelsForEngine({ engine: "openai" })).rejects.toBe(
      error,
    );
  });
});
