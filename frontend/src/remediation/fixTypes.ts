/**
 * Fix-type helpers adapted from ansible-backstage-plugins@prototype/apme
 * (plugins/backstage-apme-common/src/severity.ts) for the standalone SPA.
 */

export type FixType = 'auto' | 'ai' | 'manual';

const REMEDIATION_CLASS_TO_FIX_TYPE: Record<number, FixType> = {
  1: 'auto',
  2: 'ai',
  3: 'manual',
};

const REMEDIATION_CLASS_STRING_TO_NUMBER: Record<string, number> = {
  'auto-fixable': 1,
  'ai-candidate': 2,
  'manual-review': 3,
};

/** Normalizes gateway remediation class (proto int or engine string) to 1/2/3. */
export function normalizeRemediationClass(value: unknown): number {
  if (typeof value === 'number' && !Number.isNaN(value)) {
    if (value >= 1 && value <= 3) {
      return value;
    }
    return 3;
  }
  if (typeof value === 'string') {
    const trimmed = value.trim().toLowerCase();
    const mapped = REMEDIATION_CLASS_STRING_TO_NUMBER[trimmed];
    if (mapped !== undefined) {
      return mapped;
    }
    const parsed = Number.parseInt(trimmed, 10);
    if (!Number.isNaN(parsed) && parsed >= 1 && parsed <= 3) {
      return parsed;
    }
  }
  return 3;
}

export function remediationClassToFixType(remClass: number): FixType | undefined {
  return REMEDIATION_CLASS_TO_FIX_TYPE[normalizeRemediationClass(remClass)];
}

/** Maps engine remediation class to UI fix type, respecting AI toggle. */
export function effectiveFixType(
  remClass: number | unknown,
  enableAi: boolean,
): FixType | undefined {
  const base = remediationClassToFixType(normalizeRemediationClass(remClass));
  if (base === 'ai' && !enableAi) {
    return 'manual';
  }
  return base;
}

export function fixMethodLabel(fixType: FixType | undefined): string {
  if (fixType === 'auto') {
    return 'Quick-fix';
  }
  if (fixType === 'ai') {
    return 'AI eligible';
  }
  return 'Manual';
}
