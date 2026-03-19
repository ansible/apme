/** Shared utility functions for the APME UI. */

import type { ViolationLevel, ScanStatus } from '../api-client/models';

// --- Severity / Status Colors (PatternFly 6 dark theme) ---

export const SEVERITY_COLORS: Record<ViolationLevel, string> = {
  error: '#c9190b',
  warning: '#f0ab00',
  hint: '#73bcf7',
};

export const STATUS_COLORS: Record<ScanStatus, string> = {
  completed: '#5ba352',
  failed: '#c9190b',
  running: '#73bcf7',
};

export const SEVERITY_LABELS: Record<ViolationLevel, string> = {
  error: 'Error',
  warning: 'Warning',
  hint: 'Hint',
};

export const STATUS_LABELS: Record<ScanStatus, string> = {
  completed: 'Passed',
  failed: 'Failed',
  running: 'Running',
};

// --- Formatters ---

export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)}ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`;
  const minutes = Math.floor(ms / 60_000);
  const seconds = Math.round((ms % 60_000) / 1000);
  return `${minutes}m ${seconds}s`;
}

export function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString('en-US', {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

export function formatRelativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const seconds = Math.floor(diff / 1000);
  if (seconds < 60) return 'just now';
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

/** Map a rule_id prefix to a human-readable category. */
export function ruleCategory(ruleId: string): string {
  if (ruleId.startsWith('L')) return 'Lint';
  if (ruleId.startsWith('M')) return 'Modernize';
  if (ruleId.startsWith('R')) return 'Risk';
  if (ruleId.startsWith('P')) return 'Policy';
  if (ruleId.startsWith('SEC')) return 'Secrets';
  return 'Other';
}

/** Group violations by file path. */
export function groupByFile<V extends { file: string }>(
  violations: V[],
): Map<string, V[]> {
  const groups = new Map<string, V[]>();
  for (const v of violations) {
    const existing = groups.get(v.file);
    if (existing) {
      existing.push(v);
    } else {
      groups.set(v.file, [v]);
    }
  }
  return groups;
}
