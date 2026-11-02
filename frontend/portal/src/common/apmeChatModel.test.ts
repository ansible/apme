/*
 * Copyright Red Hat
 */

import {
  formatApmeAbbenayChatModelId,
  pickApmeChatModelId,
  resolveApmeChatModelIdFromProviders,
} from "./apmeChatModel";

describe("apmeChatModel", () => {
  it("formatApmeAbbenayChatModelId joins provider and model", () => {
    expect(formatApmeAbbenayChatModelId("test", "gpt-oss-120b")).toBe(
      "test/gpt-oss-120b",
    );
  });

  it("formatApmeAbbenayChatModelId keeps fully qualified ids", () => {
    expect(
      formatApmeAbbenayChatModelId("ignored", "openrouter/anthropic/claude"),
    ).toBe("openrouter/anthropic/claude");
  });

  it("resolveApmeChatModelIdFromProviders uses first model per provider", () => {
    expect(
      resolveApmeChatModelIdFromProviders([
        { id: "beta", engine: "openai", models: ["m1"] },
        { id: "alpha", engine: "openai", models: ["m2", "m3"] },
      ]),
    ).toBe("alpha/m2");
  });

  it("pickApmeChatModelId prefers stored id when listed", () => {
    expect(
      pickApmeChatModelId("test/gpt-4o", ["other/model", "test/gpt-4o"]),
    ).toBe("test/gpt-4o");
  });

  it("pickApmeChatModelId falls back when stored id is stale", () => {
    expect(
      pickApmeChatModelId("vertex-claude/claude-sonnet-4-6", [
        "test/deepseek-r1-distill-qwen-14b",
      ]),
    ).toBe("test/deepseek-r1-distill-qwen-14b");
  });
});
