/*
 * Copyright Red Hat
 *
 * Deep links from Quality activity findings to the fleet Rules catalog.
 */

import { normalizeRuleId } from "./violationAnalytics";

/** Git Repositories → Quality settings tab (Rules catalog lives here). */
export const QUALITY_SETTINGS_RULES_PATH =
  "/self-service/repositories/quality-settings";

export function buildQualitySettingsRuleHref(bareRuleId: string): string {
  return `${QUALITY_SETTINGS_RULES_PATH}?rule=${encodeURIComponent(bareRuleId)}`;
}

/** Returns a same-tab href only when the rule exists in the catalog. */
export function createQualitySettingsRuleHrefResolver(
  knownRuleIds: ReadonlySet<string>,
): (bareRuleId: string) => string | undefined {
  return (bareRuleId: string) => {
    const normalized = normalizeRuleId(bareRuleId);
    if (!normalized || !knownRuleIds.has(normalized)) {
      return undefined;
    }
    return buildQualitySettingsRuleHref(normalized);
  };
}
