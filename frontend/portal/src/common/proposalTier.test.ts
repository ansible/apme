/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import {
  collectAiAssistedViolationIds,
  effectiveViolationFixType,
  findViolationForProposal,
  isAiRemediationProposal,
  isDeclinedProposal,
  normalizeGatewayProposal,
  normalizeProposals,
  proposalHasVisibleDiff,
  proposalNeedsUserReview,
  violationHadAiAttempt,
} from "./proposalTier";
import type { Violation } from "./types";

const violations: Violation[] = [
  {
    id: 10,
    rule_id: "RULE-A",
    file: "playbook.yml",
    line: 5,
    remediation_class: 1,
    original_yaml: "before",
    fixed_yaml: "after",
    level: "medium",
    message: "test",
    category: "lint",
    validator_source: "native",
  },
  {
    id: 20,
    rule_id: "RULE-B",
    file: "roles/foo/tasks/main.yml",
    line: 12,
    remediation_class: 2,
    original_yaml: "old",
    fixed_yaml: "new",
    level: "high",
    message: "ai candidate",
    category: "risk",
    ai_reason: "Use FQCN",
    validator_source: "native",
  },
];

describe("normalizeGatewayProposal", () => {
  it("maps gateway tier and explanation onto portal proposal fields", () => {
    const proposal = normalizeGatewayProposal(
      {
        id: "p1",
        rule_id: "RULE-B",
        file: "roles/foo/tasks/main.yml",
        line_start: 12,
        tier: 2,
        confidence: 0.91,
        explanation: "Use fully qualified collection name",
        suggestion: "ansible.builtin.debug:",
        status: "proposed",
      },
      violations,
    );

    expect(proposal.violation_id).toBe(20);
    expect(proposal.line).toBe(12);
    expect(proposal.tier).toBe(2);
    expect(proposal.ai_reason).toBe("Use fully qualified collection name");
    expect(proposal.fixed_yaml).toBe("ansible.builtin.debug:");
    expect(proposal.status).toBe("pending");
  });
});

describe("normalizeProposals", () => {
  it("normalizes a batch of gateway proposals", () => {
    const proposals = normalizeProposals(
      [
        {
          id: "p1",
          rule_id: "RULE-A",
          file: "playbook.yml",
          line_start: 5,
          tier: 1,
          suggestion: "fixed",
          status: "accepted",
        },
      ],
      violations,
    );

    expect(proposals).toHaveLength(1);
    expect(proposals[0].status).toBe("accepted");
    expect(proposals[0].violation_id).toBe(10);
  });
});

describe("proposalHasVisibleDiff", () => {
  it("returns true when diff_hunk is present", () => {
    expect(
      proposalHasVisibleDiff({
        id: "p1",
        violation_id: 1,
        rule_id: "L001",
        file: "a.yml",
        line: 1,
        original_yaml: "",
        fixed_yaml: "",
        status: "pending",
        diff_hunk: "--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
      }),
    ).toBe(true);
  });

  it("returns true when original and fixed yaml differ", () => {
    expect(
      proposalHasVisibleDiff({
        id: "p1",
        violation_id: 1,
        rule_id: "L001",
        file: "a.yml",
        line: 1,
        original_yaml: "before",
        fixed_yaml: "after",
        status: "pending",
      }),
    ).toBe(true);
  });

  it("returns false when before and after are identical", () => {
    expect(
      proposalHasVisibleDiff({
        id: "p1",
        violation_id: 1,
        rule_id: "L001",
        file: "a.yml",
        line: 1,
        original_yaml: "same",
        fixed_yaml: "same",
        status: "pending",
      }),
    ).toBe(false);
  });
});

describe("isDeclinedProposal", () => {
  it("detects declined status", () => {
    expect(
      isDeclinedProposal({
        id: "p1",
        violation_id: 1,
        rule_id: "L001",
        file: "a.yml",
        line: 1,
        original_yaml: "",
        fixed_yaml: "",
        status: "declined",
      }),
    ).toBe(true);
  });
});

describe("effectiveViolationFixType", () => {
  it("overrides auto-fixable class when violation had an AI proposal", () => {
    const ids = collectAiAssistedViolationIds(
      violations,
      [
        {
          rule_id: "RULE-A",
          file: "playbook.yml",
          line: 5,
          tier: 2,
          violation_id: 0,
        },
      ],
      true,
    );
    expect(effectiveViolationFixType(violations[0], true, ids)).toBe("ai");
  });

  it("marks AI-abstained violations as AI-assisted", () => {
    const violation: Violation = {
      ...violations[0],
      remediation_class: 3,
      remediation_resolution: 11,
    };
    expect(effectiveViolationFixType(violation, true)).toBe("ai");
  });
});

describe("violationHadAiAttempt", () => {
  it("detects AI remediation resolution codes", () => {
    expect(
      violationHadAiAttempt({
        ...violations[0],
        remediation_resolution: 11,
      }),
    ).toBe(true);
    expect(
      violationHadAiAttempt({
        ...violations[0],
        remediation_resolution: 1,
      }),
    ).toBe(false);
  });
});

describe("collectAiAssistedViolationIds", () => {
  it("returns empty set when AI is disabled", () => {
    expect(
      collectAiAssistedViolationIds(
        violations,
        [
          {
            rule_id: "RULE-A",
            file: "playbook.yml",
            line: 5,
            tier: 2,
            violation_id: 0,
          },
        ],
        false,
      ).size,
    ).toBe(0);
  });

  it("skips tier-1 proposals when collecting AI-assisted ids", () => {
    const ids = collectAiAssistedViolationIds(
      violations,
      [
        {
          rule_id: "RULE-A",
          file: "playbook.yml",
          line: 5,
          tier: 1,
          violation_id: 0,
        },
      ],
      true,
    );
    expect(ids.size).toBe(0);
  });
});

describe("isAiRemediationProposal", () => {
  it("classifies by gateway tier even when violation remediation_class is auto", () => {
    const proposal = normalizeGatewayProposal(
      {
        id: "p1",
        rule_id: "RULE-A",
        file: "playbook.yml",
        line_start: 5,
        tier: 2,
        explanation: "AI rewrite",
        suggestion: "fixed",
      },
      violations,
    );

    expect(isAiRemediationProposal(proposal, violations, true)).toBe(true);
  });

  it("treats tier 1 as deterministic rule-based fix", () => {
    const proposal = normalizeGatewayProposal(
      {
        id: "p2",
        rule_id: "RULE-A",
        file: "playbook.yml",
        tier: 1,
        suggestion: "fixed",
      },
      violations,
    );

    expect(isAiRemediationProposal(proposal, violations, true)).toBe(false);
  });

  it("falls back to explanation text when tier is absent", () => {
    const proposal = normalizeGatewayProposal(
      {
        id: "p3",
        rule_id: "RULE-A",
        file: "playbook.yml",
        explanation: "AI rewrite",
      },
      violations,
    );

    expect(isAiRemediationProposal(proposal, violations, true)).toBe(true);
  });
});

describe("proposalNeedsUserReview", () => {
  it("requires checkbox inclusion for tier 2 proposals", () => {
    const proposal = normalizeGatewayProposal(
      {
        id: "p1",
        rule_id: "RULE-A",
        file: "playbook.yml",
        tier: 2,
        explanation: "AI",
      },
      violations,
    );

    expect(proposalNeedsUserReview(proposal, violations, true)).toBe(true);
  });

  it("requires checkbox inclusion for tier 1 rule-based fix proposals", () => {
    const proposal = normalizeGatewayProposal(
      {
        id: "p2",
        rule_id: "RULE-A",
        file: "playbook.yml",
        tier: 1,
        suggestion: "fixed",
      },
      violations,
    );

    expect(proposalNeedsUserReview(proposal, violations, true)).toBe(true);
  });

  it("requires checkbox inclusion when proposal has no matching violation", () => {
    const proposal = normalizeGatewayProposal(
      {
        id: "p3",
        rule_id: "UNKNOWN",
        file: "other.yml",
        explanation: "Needs human review",
      },
      violations,
    );

    expect(proposalNeedsUserReview(proposal, violations, true)).toBe(true);
  });
});

describe("findViolationForProposal", () => {
  it("matches by rule_id and file when violation_id is missing", () => {
    const match = findViolationForProposal(
      {
        violation_id: 0,
        rule_id: "RULE-B",
        file: "roles/foo/tasks/main.yml",
        line: 12,
      },
      violations,
    );

    expect(match?.id).toBe(20);
  });

  it("prefers violation_id when present", () => {
    expect(
      findViolationForProposal(
        {
          violation_id: 10,
          rule_id: "RULE-B",
          file: "roles/foo/tasks/main.yml",
          line: 12,
        },
        violations,
      )?.id,
    ).toBe(10);
  });

  it("disambiguates duplicate rule/file by line", () => {
    const dupes: Violation[] = [
      { ...violations[0], id: 1, line: 5 },
      { ...violations[0], id: 2, line: 8 },
    ];
    expect(
      findViolationForProposal(
        {
          violation_id: 0,
          rule_id: "RULE-A",
          file: "playbook.yml",
          line: 8,
        },
        dupes,
      )?.id,
    ).toBe(2);
  });

  it("returns undefined when no match exists", () => {
    expect(
      findViolationForProposal(
        {
          violation_id: 0,
          rule_id: "MISSING",
          file: "nowhere.yml",
          line: 1,
        },
        violations,
      ),
    ).toBeUndefined();
  });
});
