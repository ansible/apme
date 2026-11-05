/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

import type { RemediationClass, Rule, Severity } from "./types";
import {
  severityLevelToCatalogSeverity,
  severityProtoToLabel,
} from "./severity";

/** Raw rule row from the APME gateway ``/rules`` API. */
export interface GatewayRuleRow {
  rule_id?: string;
  id?: string;
  name?: string;
  description?: string;
  category?: string;
  source?: string;
  default_severity_label?: string;
  resolved_severity_label?: string;
  default_severity?: number;
  resolved_severity?: number;
  enabled?: boolean;
  resolved_enabled?: boolean;
  remediation_class?: number;
  remediationClass?: number;
  severity?: Severity;
  defaultSeverity?: Severity;
  enforced?: boolean;
  hasOverride?: boolean;
  override?: {
    severity_override?: number | null;
    enabled_override?: boolean | null;
    enforced?: boolean;
  };
}

/** True when JSON is already a portal ``Rule`` from catalog proxy (not raw gateway). */
export function isPortalRuleRow(row: GatewayRuleRow): row is Rule {
  if (row.rule_id) {
    return false;
  }
  if (
    row.resolved_severity_label !== null &&
    row.resolved_severity_label !== undefined
  ) {
    return false;
  }
  if (
    row.default_severity_label !== null &&
    row.default_severity_label !== undefined
  ) {
    return false;
  }
  // Portal rules use ``id`` + catalog ``severity``; gateway rows use ``rule_id``.
  return (
    typeof row.id === "string" && row.id.length > 0 && Boolean(row.severity)
  );
}

/** Normalize a single rules API payload to portal ``Rule`` (idempotent for Rule JSON). */
export function coerceRuleResponse(row: GatewayRuleRow | Rule): Rule {
  if (isPortalRuleRow(row)) {
    return row;
  }
  return normalizeGatewayRule(row);
}

function severityFromRow(row: GatewayRuleRow): Severity {
  if (isPortalRuleRow(row)) {
    return row.severity;
  }

  const label = row.resolved_severity_label ?? row.default_severity_label;
  if (label) {
    const normalized = label.toLowerCase();
    if (
      normalized === "critical" ||
      normalized === "high" ||
      normalized === "medium" ||
      normalized === "low" ||
      normalized === "info" ||
      normalized === "error" ||
      normalized === "blocker"
    ) {
      return normalized === "error" || normalized === "blocker"
        ? "critical"
        : (normalized as Severity);
    }
  }
  const proto = row.resolved_severity ?? row.default_severity ?? 3;
  return severityLevelToCatalogSeverity(severityProtoToLabel(proto));
}

function defaultSeverityFromRow(row: GatewayRuleRow): Severity | undefined {
  if (isPortalRuleRow(row)) {
    return row.defaultSeverity;
  }

  if (row.default_severity_label) {
    return severityFromRow({
      default_severity_label: row.default_severity_label,
      default_severity: row.default_severity,
    });
  }
  if (row.default_severity !== undefined && row.default_severity !== null) {
    return severityLevelToCatalogSeverity(
      severityProtoToLabel(row.default_severity),
    );
  }
  return undefined;
}

/** Maps gateway category values to portal violation category keys. */
export function normalizeApmeCategory(category: string): string {
  if (category === "aap") return "modernize";
  return category;
}

export function normalizeGatewayRule(row: GatewayRuleRow): Rule {
  if (isPortalRuleRow(row)) {
    return row;
  }

  const id = row.rule_id ?? row.id ?? "";
  const override = row.override;
  const hasOverride =
    !!override &&
    ((override.severity_override !== undefined &&
      override.severity_override !== null) ||
      (override.enabled_override !== undefined &&
        override.enabled_override !== null) ||
      override.enforced === true);
  const defaultSeverity = defaultSeverityFromRow(row);
  return {
    id,
    name: row.name ?? id,
    description: row.description ?? "",
    severity: severityFromRow(row),
    defaultSeverity,
    category: normalizeApmeCategory(row.category ?? "lint"),
    remediationClass: (row.remediation_class ??
      row.remediationClass ??
      3) as RemediationClass,
    enabled: row.resolved_enabled ?? row.enabled ?? true,
    source: row.source,
    enforced: override?.enforced ?? false,
    hasOverride,
  };
}

export function normalizeGatewayRules(rows: GatewayRuleRow[]): Rule[] {
  return rows.map(normalizeGatewayRule).filter((rule) => rule.id.length > 0);
}

export function buildRulesById(rules: Rule[]): Map<string, Rule> {
  const map = new Map<string, Rule>();
  for (const rule of rules) {
    map.set(rule.id, rule);
  }
  return map;
}
