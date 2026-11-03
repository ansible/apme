/*
 * Copyright Red Hat
 *
 * Map gateway Violation → @apme/ui-workflow AssessFinding (SPA ActivityDetailPage).
 */

import type { Violation } from "../../common/types";
import type { AssessFinding } from "@apme/ui-workflow";
import {
  getViolationCategory,
  isDependencyHealthViolation,
  normalizeRuleId,
} from "./violationAnalytics";

/** SPA `violationToFinding` — durable activity / project violation → panel row. */
export function violationToAssessFinding(v: Violation): AssessFinding {
  return {
    rule_id: v.rule_id,
    severity: v.level,
    message: v.message,
    file: v.file,
    line: v.line,
    path: v.path,
    node_type: v.node_type,
    remediation_class: v.remediation_class,
    source: v.validator_source,
    original_yaml: v.original_yaml,
    fixed_yaml: v.fixed_yaml,
    co_fixes: v.co_fixes,
    node_line_start: v.node_line_start,
  };
}

export type ViolationFilterOptions = {
  ruleId?: string;
  category?: string;
  /** Violation ids already acknowledged in this session. */
  acknowledgedIds?: ReadonlySet<number>;
  /** When true (default), omit dep_audit / collection_health (SPA panel filter). */
  excludeDepHealth?: boolean;
};

/**
 * Open content findings for AssessFindingsPanel: not suppressed, not dep-health
 * (unless requested), optional rule/category drill-down.
 */
export function filterViolationsForAssessPanel(
  violations: Violation[],
  options: ViolationFilterOptions = {},
): Violation[] {
  const {
    ruleId,
    category,
    acknowledgedIds,
    excludeDepHealth = true,
  } = options;
  const ruleNorm = ruleId ? normalizeRuleId(ruleId) : undefined;
  const categoryNorm = category?.trim().toLowerCase();

  return violations.filter((v) => {
    if (v.suppressed) return false;
    if (acknowledgedIds?.has(v.id)) return false;
    if (excludeDepHealth && isDependencyHealthViolation(v)) return false;
    if (ruleNorm && normalizeRuleId(v.rule_id) !== ruleNorm) return false;
    if (categoryNorm && getViolationCategory(v) !== categoryNorm) return false;
    return true;
  });
}

export function violationsToAssessFindings(
  violations: Violation[],
  options?: ViolationFilterOptions,
): AssessFinding[] {
  return filterViolationsForAssessPanel(violations, options).map(
    violationToAssessFinding,
  );
}
