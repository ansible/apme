/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 */

import type { Rule } from "./types";
import {
  coerceRuleResponse,
  isPortalRuleRow,
  normalizeGatewayRule,
  normalizeGatewayRules,
} from "./gatewayRules";

describe("isPortalRuleRow", () => {
  it("detects catalog-proxied Rule JSON", () => {
    const rule: Rule = {
      id: "M009",
      name: "M009",
      description: "Use loop instead of with_items",
      severity: "low",
      defaultSeverity: "high",
      category: "lint",
      remediationClass: 1,
      enabled: true,
      hasOverride: true,
    };
    expect(isPortalRuleRow(rule)).toBe(true);
  });

  it("detects catalog-proxied Rule JSON without requiring name/description", () => {
    expect(
      isPortalRuleRow({
        id: "M009",
        severity: "low",
        default_severity: 4,
      }),
    ).toBe(true);
  });

  it("rejects raw gateway rows", () => {
    expect(
      isPortalRuleRow({
        rule_id: "M009",
        resolved_severity_label: "low",
        name: "M009",
      }),
    ).toBe(false);
  });
});

describe("normalizeGatewayRule", () => {
  it("maps full gateway row with resolved severity label", () => {
    const rule = normalizeGatewayRule({
      rule_id: "M009",
      name: "M009",
      description: "Use loop instead of with_items",
      category: "lint",
      default_severity_label: "high",
      resolved_severity_label: "low",
      resolved_enabled: true,
      remediation_class: 1,
      override: { severity_override: 2 },
    });

    expect(rule.id).toBe("M009");
    expect(rule.severity).toBe("low");
    expect(rule.defaultSeverity).toBe("high");
    expect(rule.hasOverride).toBe(true);
  });

  it("passes through already-normalized Rule without changing severity", () => {
    const portalRule: Rule = {
      id: "M009",
      name: "M009",
      description: "Use loop instead of with_items",
      severity: "low",
      defaultSeverity: "high",
      category: "lint",
      remediationClass: 1,
      enabled: true,
      enforced: false,
      hasOverride: true,
    };

    const rule = normalizeGatewayRule(portalRule);
    expect(rule).toBe(portalRule);
    expect(rule.severity).toBe("low");
  });

  it("maps proto severity ints via ADR-043 labels", () => {
    expect(
      normalizeGatewayRule({
        rule_id: "R1",
        resolved_severity: 1,
      }).severity,
    ).toBe("info");
    expect(
      normalizeGatewayRule({
        rule_id: "R2",
        resolved_severity: 2,
      }).severity,
    ).toBe("low");
    expect(
      normalizeGatewayRule({
        rule_id: "R4",
        resolved_severity: 4,
      }).severity,
    ).toBe("high");
    expect(
      normalizeGatewayRule({
        rule_id: "R5",
        resolved_severity: 5,
      }).severity,
    ).toBe("critical");
    expect(
      normalizeGatewayRule({
        rule_id: "R6",
        resolved_severity: 6,
      }).severity,
    ).toBe("critical");
  });

  it("maps error label to catalog critical severity", () => {
    expect(
      normalizeGatewayRule({
        rule_id: "R5",
        resolved_severity_label: "error",
      }).severity,
    ).toBe("critical");
  });

  it("coerceRuleResponse preserves portal severity when proto ints are present", () => {
    const portalRule: Rule = {
      id: "M009",
      name: "M009",
      description: "desc",
      severity: "low",
      category: "lint",
      remediationClass: 1,
      enabled: true,
      hasOverride: true,
    };
    const payload = {
      ...portalRule,
      default_severity: 4,
      resolved_severity: 4,
    };
    const coerced = coerceRuleResponse(payload);
    expect(coerced).toBe(payload);
    expect(coerced.severity).toBe("low");
  });
});

describe("normalizeGatewayRules", () => {
  it("filters empty ids and preserves portal rules in a mixed list", () => {
    const portalRule: Rule = {
      id: "M009",
      name: "M009",
      description: "desc",
      severity: "low",
      category: "lint",
      remediationClass: 1,
      enabled: true,
    };
    const rules = normalizeGatewayRules([
      portalRule,
      { rule_id: "M010", resolved_severity_label: "medium", name: "M010" },
      { rule_id: "", name: "empty" },
    ]);

    expect(rules).toHaveLength(2);
    expect(rules[0]).toBe(portalRule);
    expect(rules[1]?.id).toBe("M010");
    expect(rules[1]?.severity).toBe("medium");
  });
});
