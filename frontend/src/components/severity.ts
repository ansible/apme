/**
 * Shared severity utilities — single source of truth for mapping API severity
 * levels to CSS classes, display labels, and color variables.
 *
 * ADR-043 defines exactly 6 severity levels:
 *   Critical > Error > High > Medium > Low > Info
 *
 * Legacy strings (very_high, very_low, warning, warn, hint, none, fatal)
 * are mapped to the nearest ADR-043 level for backward compatibility.
 */

export const SEV_CSS_VAR: Record<string, string> = {
  critical: 'var(--apme-sev-critical)',
  error: 'var(--apme-sev-error)',
  high: 'var(--apme-sev-high)',
  medium: 'var(--apme-sev-medium)',
  low: 'var(--apme-sev-low)',
  info: 'var(--apme-sev-info)',
};

export const SEVERITY_ORDER = [
  'critical', 'error', 'high', 'medium', 'low', 'info',
] as const;

export const SEVERITY_LABELS: Record<string, string> = {
  critical: 'Critical',
  error: 'Error',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
  info: 'Info',
};

const SEVERITY_RANK: Record<string, number> = {
  critical: 0, error: 1, high: 2, medium: 3, low: 4, info: 5,
};

/**
 * Map an API-level severity string (and optional rule ID) to a CSS class slug.
 * SEC-prefixed rules always map to "critical".
 * Legacy strings are normalized per ADR-043 backward-compatible mapping.
 */
export function severityClass(level: string, ruleId?: string): string {
  if (ruleId?.startsWith('SEC')) return 'critical';
  const l = level.toLowerCase();
  if (l === 'fatal' || l === 'critical') return 'critical';
  if (l === 'error') return 'error';
  if (l === 'very_high' || l === 'high') return 'high';
  if (l === 'medium') return 'medium';
  if (['warning', 'warn'].includes(l)) return 'medium';
  if (l === 'low') return 'low';
  if (['very_low', 'info', 'none'].includes(l)) return 'info';
  return 'info';
}

/** Upper-case display label for the severity badge text. */
export function severityLabel(level: string, ruleId?: string): string {
  if (ruleId?.startsWith('SEC')) return 'CRITICAL';
  const cls = severityClass(level, ruleId);
  return SEVERITY_LABELS[cls]?.toUpperCase() ?? 'INFO';
}

/** Numeric sort weight — lower = more severe. */
export function severityOrder(cls: string): number {
  return SEVERITY_RANK[cls] ?? 6;
}

/**
 * Map a health score (0–100) to a CSS color string.
 * 0–24 red, 25–49 orange, 50–74 yellow/gold, 75–100 green.
 */
export function healthColor(score: number): string {
  if (score < 25) return 'var(--apme-sev-critical)';
  if (score < 50) return 'var(--apme-sev-high)';
  if (score < 75) return 'var(--apme-sev-medium)';
  return 'var(--apme-green)';
}

/**
 * Map a health score to a PF Label-compatible color name.
 * 0–24 red, 25–49 orange, 50–74 yellow, 75–100 green.
 */
export function healthLabelColor(score: number): 'red' | 'orange' | 'yellow' | 'green' {
  if (score < 25) return 'red';
  if (score < 50) return 'orange';
  if (score < 75) return 'yellow';
  return 'green';
}

/** Strip validator prefix from a rule ID (e.g. "native:L042" → "L042"). */
export function bareRuleId(ruleId: string): string {
  const idx = ruleId.indexOf(':');
  if (idx > 0 && idx < ruleId.length - 1) return ruleId.slice(idx + 1);
  return ruleId;
}

/** Extract the validator source from a rule ID (e.g. "native:L042" → "native"). */
export function ruleSource(ruleId: string): string | null {
  const idx = ruleId.indexOf(':');
  if (idx > 0) return ruleId.slice(0, idx);
  return null;
}
