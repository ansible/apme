/*
 * Copyright Red Hat
 */

import { mergeApmeAiProviderLists } from "./index";

describe("mergeApmeAiProviderLists", () => {
  it("tags /providers entries as managed and config-only as config", () => {
    const merged = mergeApmeAiProviderLists(
      [{ id: "ui-prov", engine: "openai", models: ["gpt-4o"] }],
      [
        { id: "ui-prov", engine: "openai", models: ["gpt-4o", "gpt-4"] },
        { id: "cm-prov", engine: "anthropic", models: ["claude-3"] },
      ],
    );

    expect(merged).toEqual([
      {
        id: "cm-prov",
        engine: "anthropic",
        models: ["claude-3"],
        source: "config",
      },
      {
        id: "ui-prov",
        engine: "openai",
        models: ["gpt-4o"],
        source: "managed",
      },
    ]);
  });

  it("fills empty managed models from config when same id", () => {
    const merged = mergeApmeAiProviderLists(
      [{ id: "shared", engine: "openai", models: [] }],
      [{ id: "shared", engine: "openai", models: ["gpt-4o"] }],
    );

    expect(merged).toEqual([
      {
        id: "shared",
        engine: "openai",
        models: ["gpt-4o"],
        source: "managed",
      },
    ]);
  });

  it("returns config-only list when /providers is empty", () => {
    const merged = mergeApmeAiProviderLists(
      [],
      [{ id: "cm-only", engine: "ollama", models: ["llama"] }],
    );

    expect(merged).toEqual([
      {
        id: "cm-only",
        engine: "ollama",
        models: ["llama"],
        source: "config",
      },
    ]);
  });
});
