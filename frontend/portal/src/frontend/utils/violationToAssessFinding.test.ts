/*
 * Copyright Red Hat
 */

import type { Violation } from "../../common/types";
import {
  filterViolationsForAssessPanel,
  violationToAssessFinding,
  violationsToAssessFindings,
} from "./violationToAssessFinding";

function v(
  partial: Partial<Violation> & Pick<Violation, "id" | "rule_id">,
): Violation {
  return {
    level: "medium",
    message: "msg",
    file: "play.yml",
    line: 1,
    remediation_class: 1,
    validator_source: "native",
    ...partial,
  };
}

describe("violationToAssessFinding", () => {
  it("maps gateway fields to AssessFinding", () => {
    expect(
      violationToAssessFinding(
        v({
          id: 1,
          rule_id: "L001",
          level: "high",
          message: "use FQCN",
          path: "play.yml/tasks/0",
          node_type: "task",
          original_yaml: "debug:",
        }),
      ),
    ).toEqual({
      rule_id: "L001",
      severity: "high",
      message: "use FQCN",
      file: "play.yml",
      line: 1,
      path: "play.yml/tasks/0",
      node_type: "task",
      remediation_class: 1,
      source: "native",
      original_yaml: "debug:",
      fixed_yaml: undefined,
      co_fixes: undefined,
      node_line_start: undefined,
    });
  });
});

describe("filterViolationsForAssessPanel", () => {
  const rows = [
    v({ id: 1, rule_id: "L001", category: "lint" }),
    v({ id: 2, rule_id: "M001", category: "modernize", suppressed: true }),
    v({
      id: 3,
      rule_id: "CVE-1",
      validator_source: "dep_audit",
      category: "dependencies",
    }),
    v({ id: 4, rule_id: "R001", category: "risk" }),
  ];

  it("drops suppressed and dep-health by default", () => {
    expect(filterViolationsForAssessPanel(rows).map((x) => x.id)).toEqual([
      1, 4,
    ]);
  });

  it("filters by rule and category", () => {
    expect(
      filterViolationsForAssessPanel(rows, { ruleId: "native:L001" }).map(
        (x) => x.id,
      ),
    ).toEqual([1]);
    expect(
      filterViolationsForAssessPanel(rows, { category: "risk" }).map(
        (x) => x.id,
      ),
    ).toEqual([4]);
  });

  it("honors acknowledgedIds", () => {
    expect(
      filterViolationsForAssessPanel(rows, {
        acknowledgedIds: new Set([1]),
      }).map((x) => x.id),
    ).toEqual([4]);
  });
});

describe("violationsToAssessFindings", () => {
  it("maps filtered rows", () => {
    const findings = violationsToAssessFindings([
      v({ id: 1, rule_id: "L001" }),
      v({ id: 2, rule_id: "L002", suppressed: true }),
    ]);
    expect(findings).toHaveLength(1);
    expect(findings[0].rule_id).toBe("L001");
  });
});
