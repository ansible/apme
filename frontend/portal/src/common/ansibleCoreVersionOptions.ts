/*
 * Copyright Red Hat
 */

/** Supported ansible-core minor versions for portal scan target selection. */
export const ANSIBLE_CORE_VERSION_OPTIONS = [
  "2.14",
  "2.15",
  "2.16",
  "2.17",
  "2.18",
  "2.19",
  "2.20",
] as const;

export type AnsibleCoreMinor = (typeof ANSIBLE_CORE_VERSION_OPTIONS)[number];

export interface AnsibleCoreVersionOption {
  value: string;
  label: string;
}

/** Dropdown options labelled `ansible-core X.YY` (no AAP branding). */
export function ansibleCoreVersionOptions(): AnsibleCoreVersionOption[] {
  return ANSIBLE_CORE_VERSION_OPTIONS.map((value) => ({
    value,
    label: `ansible-core ${value}`,
  }));
}

/** Normalize `2.17.0` → `2.17`; pass through already-minor strings. */
export function normalizeAnsibleCoreVersion(
  version: string | undefined | null,
): string | null {
  const trimmed = version?.trim();
  if (!trimmed) {
    return null;
  }
  const match = /^(\d+\.\d+)/.exec(trimmed);
  return match ? match[1] : trimmed;
}

/** True when version is a supported ansible-core minor for portal settings. */
export function isAllowedAnsibleCoreVersion(version: string): boolean {
  const normalized = normalizeAnsibleCoreVersion(version);
  if (!normalized) {
    return false;
  }
  return (ANSIBLE_CORE_VERSION_OPTIONS as readonly string[]).includes(
    normalized,
  );
}
