/*
 * Copyright Red Hat
 */

import {
  severityBreakdown,
  getViolationCategory,
  inferCategoryFromRuleId,
  categoryBreakdown,
  categorySeverityBreakdown,
  fixableViolationCount,
  dependencyViolations,
  violationsForCollection,
  violationsForPythonPackage,
  collectionViolationCount,
  pythonPackageViolationCount,
  normalizeRuleId,
} from "./violationAnalytics";
import type { Violation } from "../../common/types";
import { buildRulesById } from "./gatewayRules";

describe("violationAnalytics", () => {
  it("classifies dependency violations from validator source", () => {
    const v: Violation = {
      id: 1,
      rule_id: "CVE-1",
      level: "high",
      message: "cryptography issue",
      file: "requirements.txt",
      line: 1,
      remediation_class: 1,
      validator_source: "dep_audit",
    };
    expect(getViolationCategory(v)).toBe("dependencies");
  });

  it("classifies collection_health findings as dependencies even with R-prefixed rule ids", () => {
    const v: Violation = {
      id: 2,
      rule_id: "R108",
      level: "medium",
      message: "Privilege escalation",
      file: "roles/foo/tasks/main.yml",
      line: 3,
      path: "ansible.posix",
      remediation_class: 3,
      validator_source: "collection_health",
    };
    expect(getViolationCategory(v)).toBe("dependencies");
    expect(inferCategoryFromRuleId("R108")).toBe("risk");
  });

  it("classifies dep_audit R200 CVE findings as dependencies", () => {
    const v: Violation = {
      id: 3,
      rule_id: "R200",
      level: "medium",
      message:
        "cryptography==3.4.8 has known vulnerability CVE-2023-1234: example",
      file: "",
      line: 0,
      remediation_class: 3,
      validator_source: "dep_audit",
    };
    expect(getViolationCategory(v)).toBe("dependencies");
  });

  it("matches collection_health violations by path fqcn", () => {
    const violations: Violation[] = [
      {
        id: 1,
        rule_id: "R108",
        level: "medium",
        message: "become:true in dependency",
        file: "tasks/main.yml",
        line: 1,
        path: "community.general",
        remediation_class: 3,
        validator_source: "collection_health",
      },
    ];
    expect(
      violationsForCollection(violations, "community.general"),
    ).toHaveLength(1);
    expect(violationsForCollection(violations, "ansible.posix")).toHaveLength(
      0,
    );
  });

  it("infers modernize category from M-prefixed rule ids", () => {
    const v: Violation = {
      id: 1,
      rule_id: "M001",
      level: "high",
      message: "Use FQCN",
      file: "playbook.yml",
      line: 1,
      remediation_class: 1,
      validator_source: "native",
    };
    expect(getViolationCategory(v)).toBe("modernize");
    expect(inferCategoryFromRuleId("M001")).toBe("modernize");
    expect(inferCategoryFromRuleId("L020")).toBe("lint");
    expect(inferCategoryFromRuleId("DEP-100")).toBe("dependencies");
    expect(inferCategoryFromRuleId("CVE-2024-1234")).toBe("dependencies");
    expect(inferCategoryFromRuleId("POL001")).toBe("policy");
  });

  it("uses rule catalog category when available", () => {
    const rulesById = buildRulesById([
      {
        id: "custom-rule",
        name: "custom",
        description: "",
        severity: "high",
        category: "risk",
        remediationClass: 3,
        enabled: true,
      },
    ]);
    const v: Violation = {
      id: 1,
      rule_id: "custom-rule",
      level: "high",
      message: "risky",
      file: "playbook.yml",
      line: 1,
      remediation_class: 3,
      validator_source: "native",
    };
    expect(getViolationCategory(v, rulesById)).toBe("risk");
  });

  it("builds severity and category breakdown", () => {
    const violations: Violation[] = [
      {
        id: 1,
        rule_id: "L001",
        level: "critical",
        message: "a",
        file: "f",
        line: 1,
        remediation_class: 1,
        validator_source: "native",
      },
      {
        id: 2,
        rule_id: "L002",
        level: "high",
        message: "b",
        file: "f",
        line: 2,
        remediation_class: 1,
        validator_source: "native",
      },
    ];
    expect(severityBreakdown(violations)).toEqual({
      critical: 1,
      error: 0,
      high: 1,
      medium: 0,
      low: 0,
      info: 0,
    });
    expect(categoryBreakdown(violations)).toEqual({ lint: 2 });
  });

  it("normalizes native rule id prefixes", () => {
    expect(normalizeRuleId("native:L001")).toBe("L001");
  });

  it("builds category severity breakdown for a single category", () => {
    const violations: Violation[] = [
      {
        id: 1,
        rule_id: "L001",
        level: "high",
        message: "a",
        file: "f",
        line: 1,
        remediation_class: 1,
        validator_source: "native",
      },
      {
        id: 2,
        rule_id: "M001",
        level: "medium",
        message: "b",
        file: "f",
        line: 2,
        remediation_class: 1,
        validator_source: "native",
      },
    ];
    expect(categorySeverityBreakdown(violations, "lint")).toEqual({
      critical: 0,
      error: 0,
      high: 1,
      medium: 0,
      low: 0,
      info: 0,
    });
  });

  it("counts fixable violations with and without AI", () => {
    const violations: Violation[] = [
      {
        id: 1,
        rule_id: "L001",
        level: "high",
        message: "auto",
        file: "f",
        line: 1,
        remediation_class: 1,
        validator_source: "native",
      },
      {
        id: 2,
        rule_id: "R001",
        level: "high",
        message: "ai candidate",
        file: "f",
        line: 2,
        remediation_class: 2,
        validator_source: "native",
      },
    ];
    expect(fixableViolationCount(violations, false)).toBe(1);
    expect(fixableViolationCount(violations, true)).toBe(2);
  });

  it("filters dependency violations and python package matches", () => {
    const violations: Violation[] = [
      {
        id: 1,
        rule_id: "R200",
        level: "medium",
        message: "cryptography==3.4.8 CVE",
        file: "requirements.txt",
        line: 1,
        remediation_class: 3,
        validator_source: "dep_audit",
      },
      {
        id: 2,
        rule_id: "L001",
        level: "low",
        message: "lint",
        file: "playbook.yml",
        line: 1,
        remediation_class: 1,
        validator_source: "native",
      },
    ];
    expect(dependencyViolations(violations)).toHaveLength(1);
    expect(violationsForPythonPackage(violations, "cryptography")).toHaveLength(
      1,
    );
    expect(
      collectionViolationCount(violations, {
        fqcn: "community.general",
        version: "1.0.0",
        source: "specified",
      }),
    ).toBe(0);
    expect(pythonPackageViolationCount(violations, "cryptography")).toBe(1);
  });

  it("classifies gitleaks and opa validator sources", () => {
    expect(
      getViolationCategory({
        id: 1,
        rule_id: "X",
        level: "high",
        message: "secret",
        file: "f",
        line: 1,
        remediation_class: 3,
        validator_source: "gitleaks",
      }),
    ).toBe("secrets");
    expect(
      getViolationCategory({
        id: 2,
        rule_id: "X",
        level: "high",
        message: "policy",
        file: "f",
        line: 1,
        remediation_class: 3,
        validator_source: "opa",
      }),
    ).toBe("policy");
  });
});
