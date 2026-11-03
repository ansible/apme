/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

import type { CollectionRef, Rule, Violation } from "../../common/types";
import { normalizeApmeCategory } from "./gatewayRules";
import {
  normalizeSeverity,
  SEVERITY_ORDER,
  effectiveFixType,
  type SeverityLevel,
} from "../../common/severity";

export const VIOLATION_CATEGORIES = [
  "lint",
  "modernize",
  "risk",
  "secrets",
  "policy",
  "dependencies",
] as const;

export type ViolationCategory = (typeof VIOLATION_CATEGORIES)[number];

export type RulesById = ReadonlyMap<string, Pick<Rule, "category">>;

export function normalizeRuleId(ruleId: string): string {
  return ruleId.replace(/^native:/, "");
}

export function inferCategoryFromRuleId(ruleId: string): string | undefined {
  const id = normalizeRuleId(ruleId).toUpperCase();
  if (/^M\d/.test(id)) return "modernize";
  if (/^L\d/.test(id)) return "lint";
  if (/^R\d/.test(id)) return "risk";
  if (/^SEC|^SECRET/.test(id)) return "secrets";
  if (id.startsWith("CVE") || id.startsWith("DEP") || id.startsWith("CVE-"))
    return "dependencies";
  if (id.startsWith("POL")) return "policy";
  const lower = normalizeRuleId(ruleId).toLowerCase();
  if (lower.includes("secret") || lower.includes("gitleaks")) return "secrets";
  if (lower.includes("risk") || lower.includes("permission")) return "risk";
  if (lower.includes("deprecated") || lower.includes("fqcn"))
    return "modernize";
  return undefined;
}

export function isDependencyHealthViolation(v: Violation): boolean {
  return (
    v.validator_source === "dep_audit" ||
    v.validator_source === "collection_health"
  );
}

export function getViolationCategory(
  v: Violation,
  rulesById?: RulesById,
): string {
  if (v.category) return normalizeApmeCategory(v.category);
  // Dependency-health findings reuse lint/risk rule IDs (R108, R200, M005, …) but
  // must classify as dependencies — match standalone APME UI (DependencyHealthOutput).
  if (isDependencyHealthViolation(v)) return "dependencies";
  const ruleId = normalizeRuleId(v.rule_id);
  const fromRule = rulesById?.get(ruleId) ?? rulesById?.get(v.rule_id);
  if (fromRule?.category) return normalizeApmeCategory(fromRule.category);
  const inferred = inferCategoryFromRuleId(v.rule_id);
  if (inferred) return inferred;
  if (v.validator_source === "gitleaks") return "secrets";
  if (v.validator_source === "opa") return "policy";
  return "lint";
}

export function severityBreakdown(
  violations: Violation[],
): Record<SeverityLevel, number> {
  const counts = SEVERITY_ORDER.reduce(
    (acc, sev) => {
      acc[sev] = 0;
      return acc;
    },
    {} as Record<SeverityLevel, number>,
  );
  for (const v of violations) {
    const sev = normalizeSeverity(v.level);
    counts[sev] += 1;
  }
  return counts;
}

export function categoryBreakdown(
  violations: Violation[],
  rulesById?: RulesById,
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const v of violations) {
    const cat = getViolationCategory(v, rulesById);
    counts[cat] = (counts[cat] ?? 0) + 1;
  }
  return counts;
}

export function categorySeverityBreakdown(
  violations: Violation[],
  category: string,
  rulesById?: RulesById,
): Record<SeverityLevel, number> {
  return severityBreakdown(
    violations.filter((v) => getViolationCategory(v, rulesById) === category),
  );
}

export function fixableViolationCount(
  violations: Violation[],
  enableAi: boolean,
): number {
  return violations.filter(
    (v) => effectiveFixType(v.remediation_class, enableAi) !== "manual",
  ).length;
}

export function dependencyViolations(
  violations: Violation[],
  rulesById?: RulesById,
): Violation[] {
  return violations.filter(
    (v) =>
      isDependencyHealthViolation(v) ||
      getViolationCategory(v, rulesById) === "dependencies",
  );
}

export function violationsForCollection(
  violations: Violation[],
  fqcn: string,
  rulesById?: RulesById,
): Violation[] {
  return violations.filter((v) => {
    if (v.validator_source === "collection_health") {
      return (v.path ?? "") === fqcn || (v.path?.includes(fqcn) ?? false);
    }
    if (!isDependencyHealthViolation(v)) {
      if (getViolationCategory(v, rulesById) !== "dependencies") return false;
    }
    return (
      v.message.includes(fqcn) ||
      v.file.includes(fqcn) ||
      (v.path?.includes(fqcn) ?? false)
    );
  });
}

export function violationsForPythonPackage(
  violations: Violation[],
  packageName: string,
  _rulesById?: RulesById,
): Violation[] {
  const needle = packageName.toLowerCase();
  return violations.filter((v) => {
    if (v.validator_source !== "dep_audit") return false;
    const msg = v.message.toLowerCase();
    return (
      msg.startsWith(`${needle}==`) ||
      msg.includes(needle) ||
      v.file.toLowerCase().includes(needle)
    );
  });
}

export function collectionViolationCount(
  violations: Violation[],
  collection: CollectionRef,
  rulesById?: RulesById,
): number {
  return violationsForCollection(violations, collection.fqcn, rulesById).length;
}

export function pythonPackageViolationCount(
  violations: Violation[],
  packageName: string,
  rulesById?: RulesById,
): number {
  return violationsForPythonPackage(violations, packageName, rulesById).length;
}
