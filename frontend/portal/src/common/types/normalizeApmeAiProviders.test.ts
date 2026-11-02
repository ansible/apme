import { mergeApmeAiProviderLists, normalizeApmeAiProviders } from "./index";

describe("AI provider response normalization", () => {
  it.each([
    {
      name: "bare array",
      value: [{ id: "provider", engine: "openai", models: ["model"] }],
    },
    {
      name: "wrapped array",
      value: {
        providers: [{ id: "provider", engine: "openai", models: ["model"] }],
      },
    },
    {
      name: "provider record",
      value: {
        providers: {
          provider: {
            engine: "openai",
            models: { model: { context_length: 4096 } },
          },
        },
      },
    },
    {
      name: "nested config record",
      value: {
        config: {
          providers: {
            provider: {
              engine: "openai",
              models: ["model"],
              apiKey: "must-not-leak",
            },
          },
        },
        path: "/private/config",
      },
    },
    {
      name: "nested config array",
      value: {
        config: {
          providers: [{ id: "provider", engine: "openai", models: ["model"] }],
        },
      },
    },
  ])(
    "accepts the $name shape and exposes only public metadata",
    ({ value }) => {
      expect(normalizeApmeAiProviders(value)).toEqual([
        { id: "provider", engine: "openai", models: ["model"] },
      ]);
      expect(JSON.stringify(normalizeApmeAiProviders(value))).not.toContain(
        "must-not-leak",
      );
    },
  );

  it.each([
    null,
    undefined,
    false,
    17,
    "invalid",
    {},
    { config: null },
    { config: "invalid" },
    { providers: null },
    { providers: false },
  ])("returns no providers for an unsupported envelope %j", (value) => {
    expect(normalizeApmeAiProviders(value)).toEqual([]);
  });

  it("normalizes incomplete entries without treating non-string models or engines as valid", () => {
    expect(
      normalizeApmeAiProviders([
        null,
        undefined,
        "provider",
        17,
        {},
        { id: 42, engine: 17, models: ["valid", null, 17] },
        { id: "record", models: { first: {}, second: {} } },
      ]),
    ).toEqual([
      { id: "", engine: "", models: [] },
      { id: "", engine: "", models: [] },
      { id: "provider", engine: "", models: [] },
      { id: "17", engine: "", models: [] },
      { id: "", engine: "", models: [] },
      { id: "42", engine: "", models: ["valid"] },
      { id: "record", engine: "", models: ["first", "second"] },
    ]);
  });

  it("keeps provider IDs for malformed record values and drops private configuration fields", () => {
    expect(
      normalizeApmeAiProviders({
        providers: {
          empty: null,
          primitive: "invalid",
          private: {
            engine: "openai",
            models: [],
            secretName: "secret",
            apiKey: "must-not-leak",
          },
        },
      }),
    ).toEqual([
      { id: "empty", engine: "", models: [] },
      { id: "primitive", engine: "", models: [] },
      { id: "private", engine: "openai", models: [] },
    ]);
  });

  it("keeps managed ownership, fills only missing models, sorts case-insensitively, and does not mutate either input", () => {
    const managed = [
      { id: "z", engine: "openai", models: [] },
      { id: "A", engine: "ollama", models: ["managed-model"] },
      { id: "unconfigured", engine: "openai", models: [] },
    ];
    const configured = [
      { id: "z", engine: "openai", models: ["configured-model"] },
      { id: "A", engine: "ollama", models: ["ignored-model"] },
      { id: "b", engine: "openai", models: [] },
    ];
    const original = JSON.stringify({ managed, configured });
    expect(mergeApmeAiProviderLists(managed, configured)).toEqual([
      {
        id: "A",
        engine: "ollama",
        models: ["managed-model"],
        source: "managed",
      },
      { id: "b", engine: "openai", models: [], source: "config" },
      { id: "unconfigured", engine: "openai", models: [], source: "managed" },
      {
        id: "z",
        engine: "openai",
        models: ["configured-model"],
        source: "managed",
      },
    ]);
    expect(JSON.stringify({ managed, configured })).toBe(original);
    expect(mergeApmeAiProviderLists([], [])).toEqual([]);
  });
});
