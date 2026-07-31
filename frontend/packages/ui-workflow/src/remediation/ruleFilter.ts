/**
 * Rule-ID filter helpers for Assess / Gate / AI review panels.
 *
 * Semantics: selected rules pick **nodes**. If any finding/proposal on a node
 * matches, every item on that node stays in view (sibling violations included).
 * Other filters (severity, kind, …) decide which nodes qualify, not which
 * sibling rows survive once a node is in.
 */

import { bareRuleId } from '../shared/severity';
import { splitRuleIds } from './proposalTier';

export interface RuleIdCarrier {
  rule_id?: string;
}

export interface NodeKeyed {
  path?: string;
  file?: string;
}

/** Stable node key for grouping (graph path, else file, else singleton bucket). */
export function reviewNodeKey(item: NodeKeyed): string {
  const path = (item.path || '').trim();
  if (path) return path;
  const file = (item.file || '').trim();
  if (file) return file;
  return '__singleton__';
}

/** Bare rule IDs present in the scan (sorted) — autocomplete source. */
export function presentRuleIds(items: RuleIdCarrier[]): string[] {
  const ids = new Set<string>();
  for (const item of items) {
    for (const part of splitRuleIds(item.rule_id || '')) {
      const bare = bareRuleId(part);
      if (bare) ids.add(bare);
    }
  }
  return [...ids].sort((a, b) => a.localeCompare(b));
}

/**
 * Normalize host-provided ``initialRuleFilters`` to bare IDs present in the
 * scan (fleet ``?rule=`` seed). Unknown / absent rules are dropped.
 */
export function normalizeInitialRuleFilters(
  initial: readonly string[] | undefined,
  presentRules: readonly string[],
): string[] {
  if (!initial?.length || presentRules.length === 0) return [];
  const present = new Set(presentRules);
  return initial.map((r) => bareRuleId(r)).filter((r) => present.has(r));
}

/** True when no rules selected, or the item carries at least one selected bare ID. */
export function matchesRuleFilters(
  item: RuleIdCarrier,
  selected: ReadonlySet<string>,
): boolean {
  if (selected.size === 0) return true;
  return splitRuleIds(item.rule_id || '').some((r) =>
    selected.has(bareRuleId(r)),
  );
}

/**
 * Filter review items with node-inclusion for rule IDs.
 *
 * - No rules selected → ``itemPassesOtherFilters`` only (row-level).
 * - Rules selected → keep **all** items on nodes that both (a) carry a
 *   selected rule and (b) have at least one item passing other filters.
 */
export function filterByRuleKeepingNodeContext<
  T extends RuleIdCarrier & NodeKeyed,
>(
  items: readonly T[],
  selectedRules: ReadonlySet<string>,
  itemPassesOtherFilters: (item: T) => boolean = () => true,
): T[] {
  if (selectedRules.size === 0) {
    return items.filter(itemPassesOtherFilters);
  }

  const ruleNodes = new Set<string>();
  const qualifyNodes = new Set<string>();
  for (const item of items) {
    const key = reviewNodeKey(item);
    if (matchesRuleFilters(item, selectedRules)) ruleNodes.add(key);
    if (itemPassesOtherFilters(item)) qualifyNodes.add(key);
  }

  return items.filter((item) => {
    const key = reviewNodeKey(item);
    return ruleNodes.has(key) && qualifyNodes.has(key);
  });
}
