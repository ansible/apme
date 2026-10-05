import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { bareRuleId } from "../data/ruleDescriptions";

describe("bareRuleId", () => {
  it("strips validator prefix", () => {
    expect(bareRuleId("native:L042")).toBe("L042");
    expect(bareRuleId("opa:L003")).toBe("L003");
  });

  it("returns the original if no prefix", () => {
    expect(bareRuleId("L042")).toBe("L042");
    expect(bareRuleId("SEC001")).toBe("SEC001");
  });

  it("handles edge cases", () => {
    expect(bareRuleId(":L042")).toBe(":L042");
    expect(bareRuleId("a:")).toBe("a:");
    expect(bareRuleId("")).toBe("");
  });
});

describe("getRuleDescription (dynamic fetch)", () => {
  let fetchSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    vi.resetModules();
    fetchSpy = vi.spyOn(globalThis, "fetch");
  });

  afterEach(() => {
    fetchSpy.mockRestore();
  });

  it("populates descriptions from /api/v1/rules and resolves prefixed IDs", async () => {
    fetchSpy.mockResolvedValueOnce({
      ok: true,
      json: () =>
        Promise.resolve([
          { rule_id: "L042", description: "Test rule description" },
          { rule_id: "L001", description: "Lint rule one" },
          { rule_id: "M010", description: "Python 2 interpreter" },
        ]),
    } as Response);

    const mod = await import("../data/ruleDescriptions");

    await vi.waitFor(() => {
      expect(mod.getRuleDescription("L042")).toBe("Test rule description");
    });

    expect(mod.getRuleDescription("native:L042")).toBe(
      "Test rule description",
    );
    expect(mod.getRuleDescription("M010")).toBe("Python 2 interpreter");
    expect(mod.getRuleDescription("SEC:L001")).toBe("");
    expect(mod.getRuleDescription("opa:L003")).toBe("");
    expect(mod.getRuleDescription("ZZZZ999")).toBe("");
  });

  it("logs a warning on fetch failure", async () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    fetchSpy.mockRejectedValueOnce(new Error("network error"));

    await import("../data/ruleDescriptions");

    await vi.waitFor(() => {
      expect(warnSpy).toHaveBeenCalledWith(
        "Failed to load rule descriptions from /api/v1/rules:",
        expect.any(Error),
      );
    });
    warnSpy.mockRestore();
  });
});
