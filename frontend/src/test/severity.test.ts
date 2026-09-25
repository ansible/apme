import { describe, it, expect } from "vitest";
import {
  isSecRule,
  severityClass,
  severityLabel,
  severityDisplayLabel,
  bareRuleId,
} from "../../packages/ui-workflow/src/shared/severity";

describe("isSecRule strict SEC: semantics", () => {
  it("escalates only the strict SEC: form", () => {
    expect(isSecRule("SEC:L001")).toBe(true);
    expect(isSecRule("SEC:001")).toBe(true);
  });

  it("does not escalate bare or lookalike prefixes", () => {
    expect(isSecRule("SEC")).toBe(false);
    expect(isSecRule("SEC001")).toBe(false);
    expect(isSecRule("SECOND-foo")).toBe(false);
    expect(isSecRule("SECURE-bar")).toBe(false);
    expect(isSecRule("sec:L001")).toBe(false);
    expect(isSecRule("")).toBe(false);
    expect(isSecRule(undefined)).toBe(false);
  });
});

describe("severity helpers honor strict SEC:", () => {
  it("forces critical across class/label/display", () => {
    expect(severityClass("info", "SEC:L001")).toBe("critical");
    expect(severityLabel("info", "SEC:L001")).toBe("CRITICAL");
    expect(severityDisplayLabel("info", "SEC:L001")).toBe("Critical");
  });

  it("falls through to level for lookalikes", () => {
    expect(severityClass("info", "SECOND-foo")).toBe("info");
    expect(severityClass("low", "SECURE-bar")).toBe("low");
    expect(severityClass("low", "SEC001")).toBe("low");
    expect(severityLabel("low", "SECOND-foo")).toBe("LOW");
    expect(severityDisplayLabel("low", "SECURE-bar")).toBe("Low");
  });
});

describe("bareRuleId", () => {
  it("strips first-colon prefix only", () => {
    expect(bareRuleId("native:L042")).toBe("L042");
    expect(bareRuleId("L042")).toBe("L042");
    expect(bareRuleId("a:b:c")).toBe("b:c");
  });
});
