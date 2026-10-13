import type { NodeDecision } from '../components/NodeReviewList';

/** Undecided rows that filters or collapse hide from the current list. */
export function hiddenUndecidedCount(
  totalPending: number,
  visiblePending: number,
): number {
  return Math.max(0, totalPending - visiblePending);
}

export function applyImplicitDeclinesToMap(
  decisions: Map<string, NodeDecision>,
  pendingKeys: Iterable<string>,
): Map<string, NodeDecision> {
  const next = new Map(decisions);
  for (const key of pendingKeys) {
    if ((next.get(key) ?? 'pending') === 'pending') {
      next.set(key, 'declined');
    }
  }
  return next;
}
