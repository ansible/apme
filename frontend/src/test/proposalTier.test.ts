import { describe, it, expect } from "vitest";
import type { OperationProposal } from '@apme/ui-workflow';
import {
  gateLabel,
  isAiRemediationProposal,
  proposalHasVisibleDiff,
  proposalNodeTitle,
  proposalsGateKey,
  resolveProposalReviewGate,
  splitRuleIds,
} from '../../packages/ui-workflow/src/remediation/proposalTier';
import {
  effectiveFixType,
  fixMethodLabel,
  normalizeRemediationClass,
} from '../../packages/ui-workflow/src/remediation/fixTypes';

function prop(partial: Partial<OperationProposal> & { id: string }): OperationProposal {
  return {
    rule_id: "L001",
    file: "playbook.yml",
    tier: 1,
    confidence: 0.9,
    ...partial,
  };
}

describe("splitRuleIds", () => {
  it("splits coupled rule ids", () => {
    expect(splitRuleIds("L026,M001")).toEqual(["L026", "M001"]);
  });

  it("trims empty segments", () => {
    expect(splitRuleIds(" L001 , , M002 ")).toEqual(["L001", "M002"]);
  });
});

describe("proposalNodeTitle", () => {
  it("prefers path", () => {
    expect(
      proposalNodeTitle(prop({ id: "t1-0", path: "playbook.yml::task[0]" })),
    ).toBe("playbook.yml::task[0]");
  });

  it("falls back to file:line", () => {
    expect(
      proposalNodeTitle(prop({ id: "t1-0", file: "a.yml", line_start: 12 })),
    ).toBe("a.yml:12");
  });
});

describe("isAiRemediationProposal", () => {
  it("classifies by source", () => {
    expect(isAiRemediationProposal(prop({ id: "t1-0", source: "deterministic" }))).toBe(
      false,
    );
    expect(isAiRemediationProposal(prop({ id: "ai-0", source: "ai", tier: 2 }))).toBe(
      true,
    );
  });

  it("classifies by tier when source absent", () => {
    expect(isAiRemediationProposal(prop({ id: "t1-0", tier: 1 }))).toBe(false);
    expect(isAiRemediationProposal(prop({ id: "ai-0", tier: 2 }))).toBe(true);
  });
});

describe("proposalHasVisibleDiff", () => {
  it("true for diff_hunk", () => {
    expect(
      proposalHasVisibleDiff(prop({ id: "t1-0", diff_hunk: "@@ -1 +1 @@\n-a\n+b" })),
    ).toBe(true);
  });

  it("true for before/after text change", () => {
    expect(
      proposalHasVisibleDiff(
        prop({ id: "t1-0", before_text: "a: 1\n", after_text: "a: 2\n" }),
      ),
    ).toBe(true);
  });

  it("false when no change", () => {
    expect(
      proposalHasVisibleDiff(
        prop({ id: "t1-0", before_text: "a: 1\n", after_text: "a: 1\n" }),
      ),
    ).toBe(false);
  });
});

describe("resolveProposalReviewGate", () => {
  it("uses tier1 when only rule-based proposals and no escalation", () => {
    const t1 = [prop({ id: "t1-0001", source: "deterministic", tier: 1 })];
    expect(resolveProposalReviewGate(t1)).toBe("tier1");
  });

  it("uses ai after escalation even when proposals are declined-only", () => {
    const declined = [
      prop({
        id: "ai-declined-0000",
        source: "ai",
        tier: 2,
        status: "declined",
        explanation: "AI could not generate a fix for this violation.",
      }),
    ];
    expect(
      resolveProposalReviewGate(declined, { enableAi: true, pastAiEscalation: true }),
    ).toBe("ai");
    expect(gateLabel(declined, "ai")).toContain("AI");
  });
});

describe("proposalsGateKey / gateLabel", () => {
  it("resets key when gate proposals change", () => {
    const t1 = [prop({ id: "t1-0001", source: "deterministic", tier: 1 })];
    const ai = [prop({ id: "ai-0001", source: "ai", tier: 2 })];
    expect(proposalsGateKey(t1)).toBe("t1:t1-0001");
    expect(proposalsGateKey(ai)).toBe("ai:ai-0001");
    expect(gateLabel(t1)).toContain("Rule-based fix");
    expect(gateLabel(ai)).toContain("AI");
  });
});

describe("resolveProposalReviewGate edge cases", () => {
  it("empty proposals without options returns tier1", () => {
    expect(resolveProposalReviewGate([])).toBe("tier1");
  });

  it("tier2 deterministic proposals classified as ai gate", () => {
    const t2det = [prop({ id: "t1-0001", source: "deterministic", tier: 2 })];
    expect(resolveProposalReviewGate(t2det)).toBe("ai");
  });

  it("ai-candidate source classified as ai gate", () => {
    const candidate = [prop({ id: "ai-cand-0", source: "ai-candidate", tier: 2 })];
    expect(resolveProposalReviewGate(candidate)).toBe("ai");
  });

  it("pastAiEscalation + enableAi promotes tier1 proposals to ai gate", () => {
    const t1 = [prop({ id: "t1-0001", source: "deterministic", tier: 1 })];
    expect(resolveProposalReviewGate(t1, { enableAi: true, pastAiEscalation: true })).toBe("ai");
  });

  it("pastAiEscalation without enableAi stays tier1", () => {
    const t1 = [prop({ id: "t1-0001", source: "deterministic", tier: 1 })];
    expect(resolveProposalReviewGate(t1, { enableAi: false, pastAiEscalation: true })).toBe("tier1");
  });

  it("gateLabel with explicit reviewGate overrides intrinsic detection", () => {
    const t1 = [prop({ id: "t1-0001", source: "deterministic", tier: 1 })];
    expect(gateLabel(t1, "ai")).toContain("AI");
    const ai = [prop({ id: "ai-0001", source: "ai", tier: 2 })];
    expect(gateLabel(ai, "tier1")).toContain("Rule-based fix");
  });
});

describe("fixTypes", () => {
  it("normalizes remediation class strings", () => {
    expect(normalizeRemediationClass("auto-fixable")).toBe(1);
    expect(normalizeRemediationClass("ai-candidate")).toBe(2);
    expect(normalizeRemediationClass("manual-review")).toBe(3);
  });

  it("rejects fractional and prefix-parsed remediation classes", () => {
    expect(normalizeRemediationClass(1.5)).toBe(3);
    expect(normalizeRemediationClass("2.9")).toBe(3);
    expect(normalizeRemediationClass("2foo")).toBe(3);
    expect(normalizeRemediationClass("2")).toBe(2);
  });

  it("maps effective fix type with AI toggle", () => {
    expect(effectiveFixType(2, true)).toBe("ai");
    expect(effectiveFixType(2, false)).toBe("manual");
    expect(fixMethodLabel("auto")).toBe("Rule-based fix");
  });
});
