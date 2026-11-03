/*
 * Copyright Red Hat
 *
 * Shared findings chrome for Quality activity (US-007).
 * Acknowledge/suppress actions live on Dependencies (richer context).
 */

import { useEffect, useMemo, useState } from "react";
import { useApi } from "@backstage/core-plugin-api";
import { Flex } from "@patternfly/react-core";
import { AssessFindingsPanel } from "@apme/ui-workflow";
import type { Violation } from "../../../common/types";
import { apmeApiRef } from "../../api";
import { createQualitySettingsRuleHrefResolver } from "../../utils/qualitySettingsRuleHref";
import { normalizeRuleId } from "../../utils/violationAnalytics";
import { violationsToAssessFindings } from "../../utils/violationToAssessFinding";

export interface QualityFindingsSectionProps {
  violations: Violation[];
  /** Prefills AssessFindingsPanel Rule filter (fleet drill-down). */
  ruleFilter?: string;
  categoryFilter?: string;
  /** Override AssessFindingsPanel description (history vs latest). */
  description?: string;
}

/**
 * Read-only AssessFindingsPanel (SPA history pattern).
 * The shared panel owns severity/rule filters.
 */
export function QualityFindingsSection({
  violations,
  ruleFilter,
  categoryFilter,
  description,
}: Readonly<QualityFindingsSectionProps>) {
  const apmeApi = useApi(apmeApiRef);
  const [knownRuleIds, setKnownRuleIds] = useState<ReadonlySet<string> | null>(
    null,
  );

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const rules = await apmeApi.getRules();
        if (!cancelled) {
          setKnownRuleIds(
            new Set(rules.map((rule) => normalizeRuleId(rule.id))),
          );
        }
      } catch {
        if (!cancelled) {
          // Fail closed: plain rule text without broken links.
          setKnownRuleIds(new Set());
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [apmeApi]);

  const panelFilterOpts = useMemo(
    () => ({
      ruleId: ruleFilter,
      category: categoryFilter,
    }),
    [ruleFilter, categoryFilter],
  );

  const findings = useMemo(
    () => violationsToAssessFindings(violations, panelFilterOpts),
    [violations, panelFilterOpts],
  );

  // Catalog-known rules link to Quality settings; RuleId falls back to filter
  // chips for findings without a catalog match (e.g. secrets / gitleaks).
  const resolveRuleHref = useMemo(() => {
    if (knownRuleIds === null) {
      return undefined;
    }
    return createQualitySettingsRuleHrefResolver(knownRuleIds);
  }, [knownRuleIds]);

  if (violations.length === 0 && findings.length === 0) {
    return (
      <div style={{ opacity: 0.7, padding: "8px 0" }}>
        No open findings for this project yet. Run a scan to assess content
        quality.
      </div>
    );
  }

  return (
    <Flex direction={{ default: "column" }} gap={{ default: "gapMd" }}>
      {findings.length > 0 ? (
        <AssessFindingsPanel
          findings={findings}
          description={description}
          resolveRuleHref={resolveRuleHref}
          ruleHrefTarget="_blank"
        />
      ) : (
        <div style={{ opacity: 0.7 }}>
          No open findings match the current filters.
        </div>
      )}
    </Flex>
  );
}
