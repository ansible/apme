/**
 * Shared node card list for Assessment (group-by-node) and Gate 1/2 proposals.
 *
 * - Chevron + row click expands/collapses detail
 * - Optional Accept/Decline decision toggles; decided rows auto-collapse
 * - Expand all / Collapse all when requested
 */

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Button, Flex, ToggleGroup, ToggleGroupItem } from '@patternfly/react-core';
import { AngleDownIcon, AngleRightIcon } from '@patternfly/react-icons';

/** Per-node gate decision. ``ignored`` reserved for Quick-fix noqa. */
export type NodeDecision = 'accepted' | 'declined' | 'pending' | 'ignored';

export interface NodeReviewItem {
  id: string;
  title: string;
  /** Rule chips, badges, etc. shown after the title. */
  meta?: ReactNode;
  /** 0–1 confidence bar (AI proposals). */
  confidence?: number;
  /** Whether expand chevron / detail are available. */
  hasDetail?: boolean;
  detail?: ReactNode;
  className?: string;
}

export interface NodeReviewListProps {
  items: NodeReviewItem[];
  ariaLabel: string;
  /**
   * When true, show Accept/Decline toggles per row.
   * Ignore can be enabled later via ``allowIgnore``.
   */
  decisionMode?: boolean;
  /** Show Ignore option (Quick-fix noqa) — off until wired. */
  allowIgnore?: boolean;
  decisions?: Map<string, NodeDecision>;
  onDecisionChange?: (id: string, decision: NodeDecision) => void;
  /** Reset expand state when this key changes (e.g. gate key). */
  resetKey?: string;
  /** When true (default), expandable rows start open. */
  defaultExpanded?: boolean;
  /** Show Expand all / Collapse all above the list. */
  showExpandControls?: boolean;
  /**
   * Bulk-accept undecided rows only (link style, right of expand controls).
   * Does not change rows already Accept / Decline / Ignore.
   */
  onAcceptRemaining?: () => void;
  /**
   * Bulk-decline undecided rows only (link style, right of expand controls).
   * Does not change rows already Accept / Decline / Ignore.
   */
  onDeclineRemaining?: () => void;
  /** Count of undecided rows — disables remaining actions when 0. */
  pendingCount?: number;
  /**
   * Clear all Accept / Decline / Ignore choices and re-expand rows.
   * Shown after Accept remaining / Decline remaining when any row is decided.
   */
  onClearDecisions?: () => void;
  /** Override Accept toggle label (e.g. Include for AI escalation). */
  acceptLabel?: string;
  /** Override Decline toggle label (e.g. Skip for AI escalation). */
  declineLabel?: string;
  /** Override bulk-accept control label. */
  acceptRemainingLabel?: string;
  /** Override bulk-decline control label. */
  declineRemainingLabel?: string;
}

function decisionClass(decision: NodeDecision | undefined): string {
  if (decision === 'accepted') return 'apme-proposal-decision-accepted';
  if (decision === 'declined') return 'apme-proposal-decision-declined';
  if (decision === 'ignored') return 'apme-proposal-decision-ignored';
  return '';
}

function isDecided(d: NodeDecision | undefined): boolean {
  return d === 'accepted' || d === 'declined' || d === 'ignored';
}

function expandableIds(items: NodeReviewItem[]): string[] {
  return items
    .filter((i) => i.hasDetail !== false && i.detail != null)
    .map((i) => i.id);
}

export function NodeReviewList({
  items,
  ariaLabel,
  decisionMode = false,
  allowIgnore = false,
  decisions,
  onDecisionChange,
  resetKey = '',
  defaultExpanded = true,
  showExpandControls = true,
  onAcceptRemaining,
  onDeclineRemaining,
  pendingCount,
  onClearDecisions,
  acceptLabel = 'Accept',
  declineLabel = 'Decline',
  acceptRemainingLabel = 'Accept remaining',
  declineRemainingLabel = 'Decline remaining',
}: NodeReviewListProps) {
  const expandables = useMemo(() => expandableIds(items), [items]);
  const itemKey = useMemo(() => items.map((i) => i.id).join('\0'), [items]);
  const [expanded, setExpanded] = useState<Set<string>>(() =>
    defaultExpanded ? new Set(expandableIds(items)) : new Set(),
  );
  const prevReset = useRef(resetKey);
  const prevItemKey = useRef(itemKey);

  // Reset / sync expand set when the gate or item set changes.
  useEffect(() => {
    const resetChanged = resetKey !== prevReset.current;
    const itemsChanged = itemKey !== prevItemKey.current;
    prevReset.current = resetKey;
    prevItemKey.current = itemKey;

    if (!resetChanged && !itemsChanged) return;

    setExpanded((prev) => {
      if (resetChanged) {
        return defaultExpanded ? new Set(expandables) : new Set();
      }
      // Item set changed (e.g. filters): keep decided collapsed; open the rest.
      const next = new Set<string>();
      for (const id of expandables) {
        if (decisionMode && isDecided(decisions?.get(id))) continue;
        if (defaultExpanded || prev.has(id)) next.add(id);
      }
      return next;
    });
  }, [
    resetKey,
    itemKey,
    expandables,
    defaultExpanded,
    decisionMode,
    decisions,
  ]);

  // Auto-collapse when Accept / Decline / Ignore is chosen.
  useEffect(() => {
    if (!decisionMode || !decisions) return;
    setExpanded((prev) => {
      let changed = false;
      const next = new Set(prev);
      for (const [id, d] of decisions) {
        if (isDecided(d) && next.has(id)) {
          next.delete(id);
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [decisions, decisionMode]);

  const toggleExpand = useCallback((id: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const expandAll = useCallback(() => {
    setExpanded(new Set(expandables));
  }, [expandables]);

  const collapseAll = useCallback(() => {
    setExpanded(new Set());
  }, []);

  const allExpanded =
    expandables.length > 0 && expandables.every((id) => expanded.has(id));

  const decidedCount = useMemo(() => {
    if (!decisions) return 0;
    let n = 0;
    for (const item of items) {
      if (isDecided(decisions.get(item.id))) n += 1;
    }
    return n;
  }, [decisions, items]);

  const showDecisionBulk =
    decisionMode &&
    (onAcceptRemaining != null ||
      onDeclineRemaining != null ||
      onClearDecisions != null) &&
    items.length > 0;
  const showToolbar =
    (showExpandControls && expandables.length > 0) || showDecisionBulk;
  const remainingDisabled =
    pendingCount != null ? pendingCount === 0 : false;
  const remainingLabel =
    pendingCount != null && pendingCount > 0 ? ` (${pendingCount})` : '';

  const handleClear = useCallback(() => {
    onClearDecisions?.();
    setExpanded(new Set(expandables));
  }, [onClearDecisions, expandables]);

  return (
    <div>
      {showToolbar && (
        <Flex
          justifyContent={{ default: 'justifyContentSpaceBetween' }}
          alignItems={{ default: 'alignItemsCenter' }}
          style={{ marginBottom: 8 }}
          gap={{ default: 'gapMd' }}
        >
          <Flex
            gap={{ default: 'gapSm' }}
            alignItems={{ default: 'alignItemsCenter' }}
          >
            {showExpandControls && expandables.length > 0 && (
              <>
                <Button
                  variant="link"
                  isInline
                  onClick={expandAll}
                  isDisabled={allExpanded}
                >
                  Expand all
                </Button>
                <span style={{ opacity: 0.35 }}>|</span>
                <Button
                  variant="link"
                  isInline
                  onClick={collapseAll}
                  isDisabled={expanded.size === 0}
                >
                  Collapse all
                </Button>
              </>
            )}
          </Flex>
          {showDecisionBulk && (
            <Flex
              gap={{ default: 'gapSm' }}
              alignItems={{ default: 'alignItemsCenter' }}
            >
              {onAcceptRemaining && (
                <Button
                  variant="link"
                  isInline
                  isDisabled={remainingDisabled}
                  onClick={onAcceptRemaining}
                >
                  {acceptRemainingLabel}{remainingLabel}
                </Button>
              )}
              {onAcceptRemaining && onDeclineRemaining && (
                <span style={{ opacity: 0.35 }}>|</span>
              )}
              {onDeclineRemaining && (
                <Button
                  variant="link"
                  isInline
                  isDisabled={remainingDisabled}
                  onClick={onDeclineRemaining}
                >
                  {declineRemainingLabel}{remainingLabel}
                </Button>
              )}
              {onClearDecisions &&
                (onAcceptRemaining != null || onDeclineRemaining != null) && (
                  <span style={{ opacity: 0.35 }}>|</span>
                )}
              {onClearDecisions && (
                <Button
                  variant="link"
                  isInline
                  isDisabled={decidedCount === 0}
                  onClick={handleClear}
                >
                  Clear
                </Button>
              )}
            </Flex>
          )}
        </Flex>
      )}

      <div className="apme-proposals-list" role="group" aria-label={ariaLabel}>
        {items.map((item) => {
          const isExpanded = expanded.has(item.id);
          const decision = decisions?.get(item.id) ?? 'pending';
          const canExpand = item.hasDetail !== false && item.detail != null;

          return (
            <div
              key={item.id}
              className={[
                'apme-proposal-card',
                decisionMode ? decisionClass(decision) : '',
                item.className ?? '',
              ]
                .filter(Boolean)
                .join(' ')}
            >
              <div
                className="apme-proposal-header"
                role={canExpand ? 'button' : undefined}
                tabIndex={canExpand ? 0 : undefined}
                aria-expanded={canExpand ? isExpanded : undefined}
                onClick={() => {
                  if (canExpand) toggleExpand(item.id);
                }}
                onKeyDown={(e) => {
                  if (!canExpand) return;
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    toggleExpand(item.id);
                  }
                }}
              >
                <span
                  className="apme-proposal-chevron"
                  aria-hidden={!canExpand}
                >
                  {canExpand ? (
                    isExpanded ? (
                      <AngleDownIcon />
                    ) : (
                      <AngleRightIcon />
                    )
                  ) : (
                    <span className="apme-proposal-chevron-spacer" />
                  )}
                </span>

                <div className="apme-proposal-meta">
                  <span className="apme-proposal-file" title={item.title}>
                    {item.title}
                  </span>
                  {item.meta}
                </div>

                {item.confidence != null && (
                  <div className="apme-proposal-confidence">
                    <div className="apme-confidence-bar">
                      <div
                        className="apme-confidence-fill"
                        style={{
                          width: `${Math.round(item.confidence * 100)}%`,
                        }}
                      />
                    </div>
                    <span className="apme-confidence-label">
                      {Math.round(item.confidence * 100)}%
                    </span>
                  </div>
                )}

                {decisionMode && (
                  <div
                    className="apme-proposal-decision"
                    onClick={(e) => e.stopPropagation()}
                    onKeyDown={(e) => e.stopPropagation()}
                  >
                    <ToggleGroup
                      isCompact
                      className="apme-decision-toggle"
                      aria-label={`Decision for ${item.title}`}
                    >
                      <ToggleGroupItem
                        text={acceptLabel}
                        className="apme-decision-accept"
                        isSelected={decision === 'accepted'}
                        onChange={() => onDecisionChange?.(item.id, 'accepted')}
                      />
                      <ToggleGroupItem
                        text={declineLabel}
                        className="apme-decision-decline"
                        isSelected={decision === 'declined'}
                        onChange={() => onDecisionChange?.(item.id, 'declined')}
                      />
                      {allowIgnore && (
                        <ToggleGroupItem
                          text="Ignore"
                          isSelected={decision === 'ignored'}
                          onChange={() =>
                            onDecisionChange?.(item.id, 'ignored')
                          }
                        />
                      )}
                    </ToggleGroup>
                  </div>
                )}
              </div>

              {isExpanded && item.detail != null && (
                <div className="apme-proposal-detail">{item.detail}</div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
