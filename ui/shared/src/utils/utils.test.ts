import { describe, it, expect } from "vitest";
import {
  formatDuration,
  formatRelativeTime,
  ruleCategory,
  groupByFile,
  SEVERITY_LABELS,
  STATUS_LABELS,
} from "./index";

describe("formatDuration", () => {
  it("formats milliseconds under 1 second", () => {
    expect(formatDuration(42)).toBe("42ms");
    expect(formatDuration(999)).toBe("999ms");
  });

  it("formats seconds under 1 minute", () => {
    expect(formatDuration(1500)).toBe("1.5s");
    expect(formatDuration(59000)).toBe("59.0s");
  });

  it("formats minutes and seconds", () => {
    expect(formatDuration(90000)).toBe("1m 30s");
    expect(formatDuration(125000)).toBe("2m 5s");
  });
});

describe("formatRelativeTime", () => {
  it("returns 'just now' for recent times", () => {
    const now = new Date().toISOString();
    expect(formatRelativeTime(now)).toBe("just now");
  });

  it("returns minutes ago", () => {
    const fiveMinAgo = new Date(Date.now() - 5 * 60 * 1000).toISOString();
    expect(formatRelativeTime(fiveMinAgo)).toBe("5m ago");
  });

  it("returns hours ago", () => {
    const twoHoursAgo = new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString();
    expect(formatRelativeTime(twoHoursAgo)).toBe("2h ago");
  });

  it("returns days ago", () => {
    const threeDaysAgo = new Date(Date.now() - 3 * 24 * 60 * 60 * 1000).toISOString();
    expect(formatRelativeTime(threeDaysAgo)).toBe("3d ago");
  });
});

describe("ruleCategory", () => {
  it("maps L-prefixed rules to Lint", () => {
    expect(ruleCategory("L002")).toBe("Lint");
    expect(ruleCategory("L056")).toBe("Lint");
  });

  it("maps M-prefixed rules to Modernize", () => {
    expect(ruleCategory("M001")).toBe("Modernize");
  });

  it("maps R-prefixed rules to Risk", () => {
    expect(ruleCategory("R101")).toBe("Risk");
  });

  it("maps P-prefixed rules to Policy", () => {
    expect(ruleCategory("P001")).toBe("Policy");
  });

  it("maps SEC-prefixed rules to Secrets", () => {
    expect(ruleCategory("SEC:aws-key")).toBe("Secrets");
  });

  it("returns Other for unknown prefixes", () => {
    expect(ruleCategory("X999")).toBe("Other");
  });
});

describe("groupByFile", () => {
  it("groups violations by file path", () => {
    const violations = [
      { file: "a.yml", rule_id: "L002" },
      { file: "b.yml", rule_id: "L003" },
      { file: "a.yml", rule_id: "L004" },
    ];
    const groups = groupByFile(violations);
    expect(groups.size).toBe(2);
    expect(groups.get("a.yml")?.length).toBe(2);
    expect(groups.get("b.yml")?.length).toBe(1);
  });

  it("returns empty map for empty input", () => {
    expect(groupByFile([]).size).toBe(0);
  });
});

describe("constant labels", () => {
  it("SEVERITY_LABELS covers all levels", () => {
    expect(SEVERITY_LABELS.error).toBe("Error");
    expect(SEVERITY_LABELS.warning).toBe("Warning");
    expect(SEVERITY_LABELS.hint).toBe("Hint");
  });

  it("STATUS_LABELS covers all statuses", () => {
    expect(STATUS_LABELS.completed).toBe("Passed");
    expect(STATUS_LABELS.failed).toBe("Failed");
    expect(STATUS_LABELS.running).toBe("Running");
  });
});
