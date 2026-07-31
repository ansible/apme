/** @vitest-environment node */
import { describe, it, expect } from "vitest";
import {
  filterByRuleKeepingNodeContext,
  matchesRuleFilters,
  presentRuleIds,
  reviewNodeKey,
} from "../../packages/ui-workflow/src/remediation/ruleFilter";

const findings = [
  {
    rule_id: "native:L050",
    path: "play.yml/plays[0]/tasks[0]",
    severity: "high",
  },
  {
    rule_id: "M001",
    path: "play.yml/plays[0]/tasks[0]",
    severity: "medium",
  },
  {
    rule_id: "R099",
    path: "play.yml/plays[0]/tasks[1]",
    severity: "low",
  },
];

describe("presentRuleIds", () => {
  it("collects bare IDs, splits coupled, strips prefixes", () => {
    expect(
      presentRuleIds([
        { rule_id: "native:L050" },
        { rule_id: "M001" },
        { rule_id: "L050,M001" },
        { rule_id: "" },
      ]),
    ).toEqual(["L050", "M001"]);
  });
});

describe("matchesRuleFilters", () => {
  it("passes all when nothing selected", () => {
    expect(matchesRuleFilters({ rule_id: "L050" }, new Set())).toBe(true);
  });

  it("matches bare and prefixed IDs", () => {
    const selected = new Set(["L050"]);
    expect(matchesRuleFilters({ rule_id: "native:L050" }, selected)).toBe(true);
    expect(matchesRuleFilters({ rule_id: "L050,M001" }, selected)).toBe(true);
    expect(matchesRuleFilters({ rule_id: "M001" }, selected)).toBe(false);
  });
});

describe("filterByRuleKeepingNodeContext", () => {
  it("returns row-level filter when no rules selected", () => {
    const result = filterByRuleKeepingNodeContext(
      findings,
      new Set(),
      (f) => f.severity === "high",
    );
    expect(result.map((f) => f.rule_id)).toEqual(["native:L050"]);
  });

  it("keeps all findings on a node when any match the rule", () => {
    const result = filterByRuleKeepingNodeContext(
      findings,
      new Set(["L050"]),
    );
    expect(result.map((f) => f.rule_id)).toEqual(["native:L050", "M001"]);
    expect(result.every((f) => reviewNodeKey(f) === findings[0]!.path)).toBe(
      true,
    );
  });

  it("other filters qualify nodes but do not strip siblings", () => {
    // Only high passes "other"; node still qualifies via L050 high, so M001 stays.
    const result = filterByRuleKeepingNodeContext(
      findings,
      new Set(["L050"]),
      (f) => f.severity === "high",
    );
    expect(result.map((f) => f.rule_id)).toEqual(["native:L050", "M001"]);
  });

  it("drops nodes with no rule match", () => {
    const result = filterByRuleKeepingNodeContext(
      findings,
      new Set(["L050"]),
    );
    expect(result.some((f) => f.rule_id === "R099")).toBe(false);
  });
});
