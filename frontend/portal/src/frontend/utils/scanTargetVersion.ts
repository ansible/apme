/*
 * Copyright Red Hat
 */

/** Human-readable ansible-core label for scan target display. */
export function formatAnsibleCoreVersionLabel(
  version: string | undefined | null,
): string | null {
  const trimmed = version?.trim();
  if (!trimmed) {
    return null;
  }
  if (/^ansible-core\b/i.test(trimmed)) {
    return trimmed;
  }
  return `ansible-core ${trimmed}`;
}
