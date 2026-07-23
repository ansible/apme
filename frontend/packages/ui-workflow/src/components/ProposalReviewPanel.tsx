import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Button, Label } from '@patternfly/react-core';
import { RuleId } from '../shared/RuleId';
import type { OperationProposal } from '../types/operation';
import { DiffView } from './DiffView';
import { FeedbackModal, type FeedbackPayload } from '../shared/FeedbackModal';
import {
  NodeReviewList,
  type NodeDecision,
  type NodeReviewItem,
} from './NodeReviewList';
import {
  toggleInFilterSet,
  type ReviewFilterGroup,
} from './ReviewFilterBar';
import { ReviewStepShell } from './ReviewStepShell';
import { ReviewInventoryRow } from './ReviewInventoryRow';
import { type WorkflowNextConfig } from './WorkflowNextBar';
import {
  SEVERITY_LABELS,
  SEVERITY_ORDER,
  severityClass,
  severityDisplayLabel,
  severityLabelColor,
  severityOrder,
} from '../shared/severity';
import {
  descendantProposalIds,
  fixTypeLabelColor,
  isAiRemediationProposal,
  nodeTypeLabel,
  nodeTypeLabelColor,
  normalizeFindingNodeType,
  orderPresentNodeTypes,
  proposalHasVisibleDiff,
  proposalNodeTitle,
  proposalsGateKey,
  type FindingNodeType,
} from '../remediation';

type DecisionFilter = 'pending' | 'accepted' | 'declined';
const DECISION_FILTER_ORDER: DecisionFilter[] = [
  'pending',
  'accepted',
  'declined',
];
const DECISION_FILTER_LABELS: Record<DecisionFilter, string> = {
  pending: 'Undecided',
  accepted: 'Accepted',
  declined: 'Declined',
};

/** Highest severity present on a proposal (from explanation lines or rule id). */
function proposalSeverity(p: OperationProposal): string {
  const found: string[] = [];
  if (p.explanation) {
    for (const line of p.explanation.split('\n').filter(Boolean)) {
      const { ruleId, severity } = parseViolationLine(line);
      found.push(severityClass(severity || 'info', ruleId || undefined));
    }
  }
  if (found.length === 0) {
    for (const rid of p.rule_id.split(',').map((s) => s.trim()).filter(Boolean)) {
      found.push(severityClass('info', rid));
    }
  }
  if (found.length === 0) return 'info';
  return found.sort((a, b) => severityOrder(a) - severityOrder(b))[0]!;
}

function effectiveDecision(
  id: string,
  decisions: Map<string, NodeDecision>,
): DecisionFilter {
  const d = decisions.get(id);
  if (d === 'accepted') return 'accepted';
  if (d === 'declined' || d === 'ignored') return 'declined';
  return 'pending';
}

/** Count bundled findings on a node proposal (comma rules, else explanation lines). */
function proposalFindingCount(p: OperationProposal): number {
  const rules = p.rule_id
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean);
  if (rules.length > 0) return rules.length;
  const lines = (p.explanation || '').split('\n').filter(Boolean);
  return Math.max(lines.length, 1);
}

/** Parse ``[rule|severity]: message`` (or ``[rule]: message``) explanation lines. */
function parseViolationLine(line: string): {
  ruleId: string;
  severity: string;
  message: string;
} {
  const m = line.match(/^\[([^\]|]+)(?:\|([^\]]+))?\]:\s*(.*)$/);
  if (!m) {
    return { ruleId: '', severity: '', message: line };
  }
  return {
    ruleId: m[1] ?? '',
    severity: (m[2] ?? '').trim(),
    message: m[3] ?? '',
  };
}

export interface ProposalDraftUpdate {
  proposal_id: string;
  status: 'approved' | 'declined' | 'pending';
}

export interface ProposalReviewPanelProps {
  proposals: OperationProposal[];
  onApprove: (ids: string[]) => void;
  /** Optional optimistic draft decision sync (PATCH /operation/proposals). */
  onDraftUpdate?: (updates: ProposalDraftUpdate[]) => void;
  feedbackEnabled?: boolean;
  scanId?: string;
  /** Cancel the in-flight operation (not a soft dismiss). */
  onCancel?: () => void;
}

/**
 * Rows the user can Accept / Decline. Draft PATCH sets status to approved/declined
 * optimistically — those must stay in the list (collapsed + green/red border).
 * Only engine "could not fix" rows (declined/rejected with no applyable diff) leave
 * the queue for the separate Declined-by-AI section.
 */
function isInReviewQueue(p: OperationProposal): boolean {
  if (proposalHasVisibleDiff(p)) {
    return true;
  }
  return p.status !== 'declined' && p.status !== 'rejected';
}

function isAiCouldNotFix(p: OperationProposal): boolean {
  return (
    (p.status === 'declined' || p.status === 'rejected') &&
    !proposalHasVisibleDiff(p)
  );
}

function decisionToDraft(d: NodeDecision): ProposalDraftUpdate['status'] {
  if (d === 'accepted') return 'approved';
  if (d === 'declined' || d === 'ignored') return 'declined';
  return 'pending';
}

export function ProposalReviewPanel({
  proposals,
  onApprove,
  onDraftUpdate,
  feedbackEnabled,
  scanId,
  onCancel,
}: ProposalReviewPanelProps) {
  const proposed = useMemo(() => proposals.filter(isInReviewQueue), [proposals]);
  const declined = useMemo(
    () => proposals.filter(isAiCouldNotFix),
    [proposals],
  );
  const actionable = useMemo(
    () => proposed.filter(proposalHasVisibleDiff),
    [proposed],
  );
  const explanationOnly = useMemo(
    () => proposed.filter((p) => !proposalHasVisibleDiff(p)),
    [proposed],
  );

  const gateKey = proposalsGateKey(proposals);
  const isAiGate = proposed.length > 0 && isAiRemediationProposal(proposed[0]!);

  const [decisions, setDecisions] = useState<Map<string, NodeDecision>>(
    () => new Map(),
  );
  const [decisionFilters, setDecisionFilters] = useState<Set<DecisionFilter>>(
    () => new Set(DECISION_FILTER_ORDER),
  );
  const [sevFilters, setSevFilters] = useState<Set<string>>(() => {
    const present = new Set(actionable.map(proposalSeverity));
    return new Set(SEVERITY_ORDER.filter((s) => present.has(s)));
  });
  const [nodeTypeFilters, setNodeTypeFilters] = useState<Set<FindingNodeType>>(
    () =>
      new Set(
        orderPresentNodeTypes(
          actionable.map((p) => normalizeFindingNodeType(p.node_type)),
        ),
      ),
  );
  const [showDeclined, setShowDeclined] = useState(false);
  const [feedbackTarget, setFeedbackTarget] = useState<OperationProposal | null>(null);
  const draftTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const prevGateKey = useRef<string>('');

  const presentSeverities = useMemo(() => {
    const present = new Set(actionable.map(proposalSeverity));
    return SEVERITY_ORDER.filter((s) => present.has(s));
  }, [actionable]);

  const presentNodeTypes = useMemo(
    () =>
      orderPresentNodeTypes(
        actionable.map((p) => normalizeFindingNodeType(p.node_type)),
      ),
    [actionable],
  );

  const severityCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const p of actionable) {
      const sev = proposalSeverity(p);
      counts.set(sev, (counts.get(sev) ?? 0) + 1);
    }
    return counts;
  }, [actionable]);

  const nodeTypeCounts = useMemo(() => {
    const counts = new Map<FindingNodeType, number>();
    for (const p of actionable) {
      const nt = normalizeFindingNodeType(p.node_type);
      counts.set(nt, (counts.get(nt) ?? 0) + 1);
    }
    return counts;
  }, [actionable]);

  const decisionCounts = useMemo(() => {
    const counts: Record<DecisionFilter, number> = {
      pending: 0,
      accepted: 0,
      declined: 0,
    };
    for (const p of actionable) {
      counts[effectiveDecision(p.id, decisions)] += 1;
    }
    return counts;
  }, [actionable, decisions]);

  const inventory = useMemo(() => {
    const byDecision: Record<
      DecisionFilter,
      { findings: number; locations: number }
    > = {
      pending: { findings: 0, locations: 0 },
      accepted: { findings: 0, locations: 0 },
      declined: { findings: 0, locations: 0 },
    };
    let totalFindings = 0;
    for (const p of actionable) {
      const findings = proposalFindingCount(p);
      totalFindings += findings;
      const d = effectiveDecision(p.id, decisions);
      byDecision[d].findings += findings;
      byDecision[d].locations += 1;
    }
    return {
      totalFindings,
      totalLocations: actionable.length,
      ...byDecision,
    };
  }, [actionable, decisions]);

  useEffect(() => {
    if (gateKey && gateKey !== prevGateKey.current) {
      prevGateKey.current = gateKey;
      setDecisions(new Map());
      setDecisionFilters(new Set(DECISION_FILTER_ORDER));
    }
  }, [gateKey]);

  const presentSevKey = presentSeverities.join(',');
  const presentNodeKey = presentNodeTypes.join(',');
  useEffect(() => {
    setSevFilters(new Set(presentSeverities));
    setNodeTypeFilters(new Set(presentNodeTypes));
  }, [presentSevKey, presentNodeKey, presentSeverities, presentNodeTypes]);

  // Mirror draft PATCH status onto toggles (e.g. after SSE / remount) without
  // overwriting an in-progress local choice.
  useEffect(() => {
    setDecisions((prev) => {
      let changed = false;
      const next = new Map(prev);
      for (const p of actionable) {
        if (next.has(p.id)) continue;
        if (p.status === 'approved') {
          next.set(p.id, 'accepted');
          changed = true;
        } else if (p.status === 'declined') {
          next.set(p.id, 'declined');
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [actionable]);

  const flushDraft = useCallback(
    (next: Map<string, NodeDecision>) => {
      if (!onDraftUpdate) return;
      if (draftTimer.current) clearTimeout(draftTimer.current);
      draftTimer.current = setTimeout(() => {
        const updates: ProposalDraftUpdate[] = actionable.map((p) => ({
          proposal_id: p.id,
          status: decisionToDraft(next.get(p.id) ?? 'pending'),
        }));
        onDraftUpdate(updates);
      }, 300);
    },
    [onDraftUpdate, actionable],
  );

  useEffect(
    () => () => {
      if (draftTimer.current) clearTimeout(draftTimer.current);
    },
    [],
  );

  const setDecisionForIds = useCallback(
    (ids: string[], decision: NodeDecision) => {
      setDecisions((prev) => {
        const next = new Map(prev);
        for (const id of ids) next.set(id, decision);
        flushDraft(next);
        return next;
      });
    },
    [flushDraft],
  );

  const handleDecisionChange = useCallback(
    (id: string, decision: NodeDecision) => {
      const parent = actionable.find((p) => p.id === id);
      const kids = parent ? descendantProposalIds(parent, actionable) : [];
      setDecisionForIds([id, ...kids], decision);
    },
    [actionable, setDecisionForIds],
  );

  const clearDecisions = useCallback(() => {
    setDecisions(() => {
      const next = new Map<string, NodeDecision>();
      for (const p of actionable) {
        next.set(p.id, 'pending');
      }
      flushDraft(next);
      return next;
    });
  }, [actionable, flushDraft]);

  const acceptedIds = useMemo(() => {
    const ids: string[] = [];
    for (const p of actionable) {
      if (decisions.get(p.id) === 'accepted') ids.push(p.id);
    }
    return ids;
  }, [actionable, decisions]);

  const declinedCount = useMemo(() => {
    let n = 0;
    for (const p of actionable) {
      if (decisions.get(p.id) === 'declined') n += 1;
    }
    return n;
  }, [actionable, decisions]);

  /** All undecided locations (gates Next regardless of filters). */
  const pendingIds = useMemo(() => {
    const ids: string[] = [];
    for (const p of actionable) {
      const d = decisions.get(p.id);
      if (d !== 'accepted' && d !== 'declined' && d !== 'ignored') {
        ids.push(p.id);
      }
    }
    return ids;
  }, [actionable, decisions]);

  const pendingCount = pendingIds.length;

  const workflowNext = useMemo((): WorkflowNextConfig => {
    // No silent default: every location must be Accept or Decline before Next.
    if (pendingCount > 0) {
      return {
        label: 'Next',
        summary: `${pendingCount} location${
          pendingCount !== 1 ? 's' : ''
        } still undecided. Accept or Decline each one, then Next unlocks. Accept remaining / Decline remaining only decide currently visible rows — widen filters to reach the rest.`,
        onNext: () => onApprove(acceptedIds),
        isDisabled: true,
      };
    }
    const declinedNote =
      declinedCount > 0
        ? ` (${declinedCount} declined)`
        : '';
    let summary: string;
    if (isAiGate) {
      summary =
        acceptedIds.length > 0
          ? `Apply ${acceptedIds.length} accepted AI fix${acceptedIds.length !== 1 ? 'es' : ''}${declinedNote}, then finish remediation.`
          : 'Continue with no AI fixes applied, then finish remediation.';
    } else {
      summary =
        acceptedIds.length > 0
          ? `Apply ${acceptedIds.length} accepted quick-fix${acceptedIds.length !== 1 ? 'es' : ''}${declinedNote}, then continue to AI assessment if enabled.`
          : 'Continue with no quick-fixes applied, then AI assessment if enabled (or finish if AI is off).';
    }
    return {
      label: 'Next',
      summary,
      onNext: () => onApprove(acceptedIds),
      isDisabled: actionable.length === 0,
    };
  }, [
    acceptedIds,
    actionable.length,
    declinedCount,
    isAiGate,
    onApprove,
    pendingCount,
  ]);

  const actionableItems: NodeReviewItem[] = useMemo(
    () =>
      actionable.map((p) => {
        const hasDetail = !!(
          p.explanation ||
          p.diff_hunk ||
          p.before_text ||
          p.after_text ||
          (feedbackEnabled && isAiGate)
        );
        return {
          id: p.id,
          title: proposalNodeTitle(p),
          confidence: isAiGate ? p.confidence : undefined,
          hasDetail,
          meta: (
            <>
              <RuleId ruleId={p.rule_id} />
              <Label
                isCompact
                color={fixTypeLabelColor(isAiGate ? 'ai' : 'auto')}
              >
                {isAiGate ? 'AI' : 'Quick-fix'}
              </Label>
              <Label
                isCompact
                color={nodeTypeLabelColor(
                  normalizeFindingNodeType(p.node_type),
                )}
              >
                {nodeTypeLabel(normalizeFindingNodeType(p.node_type))}
              </Label>
            </>
          ),
          detail: (
            <>
              {p.explanation && (
                <div className="apme-assess-findings-detail">
                  {p.explanation.split('\n').filter(Boolean).map((line, i) => {
                    const { ruleId, severity, message } = parseViolationLine(line);
                    const sev = severity || 'info';
                    return (
                      <div
                        key={`${p.id}-desc-${i}`}
                        className="apme-assess-finding-row"
                      >
                        {ruleId ? <RuleId ruleId={ruleId} /> : null}
                        {(ruleId || severity) && (
                          <Label
                            isCompact
                            color={severityLabelColor(sev, ruleId)}
                          >
                            {severityDisplayLabel(sev, ruleId)}
                          </Label>
                        )}
                        {!isAiGate && <Label isCompact>Quick-fix</Label>}
                        <span className="apme-assess-finding-msg">{message}</span>
                      </div>
                    );
                  })}
                </div>
              )}
              {(p.diff_hunk || p.before_text || p.after_text) && (
                <div className="apme-proposal-diff">
                  <DiffView
                    mode="side-by-side"
                    diff={p.diff_hunk}
                    before={p.before_text}
                    after={p.after_text}
                  />
                </div>
              )}
              {feedbackEnabled && isAiGate && (
                <div style={{ padding: '4px 12px 8px' }}>
                  <Button
                    variant="link"
                    size="sm"
                    onClick={() => setFeedbackTarget(p)}
                  >
                    Report Issue
                  </Button>
                </div>
              )}
            </>
          ),
        };
      }),
    [actionable, isAiGate, feedbackEnabled],
  );

  const hasNarrowedFilters =
    decisionFilters.size < DECISION_FILTER_ORDER.length ||
    (presentSeverities.length > 0 &&
      sevFilters.size < presentSeverities.length) ||
    (presentNodeTypes.length > 0 &&
      presentNodeTypes.some((t) => !nodeTypeFilters.has(t)));

  const filteredActionableItems = useMemo(() => {
    const byId = new Map(actionable.map((p) => [p.id, p]));
    return actionableItems.filter((item) => {
      const decision = effectiveDecision(item.id, decisions);
      if (!decisionFilters.has(decision)) return false;
      const proposal = byId.get(item.id);
      if (!proposal) return false;
      if (presentSeverities.length > 0 && sevFilters.size > 0) {
        const sev = proposalSeverity(proposal);
        if (!sevFilters.has(sev)) return false;
      }
      if (presentNodeTypes.length > 0 && nodeTypeFilters.size > 0) {
        const nt = normalizeFindingNodeType(proposal.node_type);
        if (!nodeTypeFilters.has(nt)) return false;
      }
      return true;
    });
  }, [
    actionableItems,
    actionable,
    decisions,
    decisionFilters,
    sevFilters,
    nodeTypeFilters,
    presentSeverities.length,
    presentNodeTypes.length,
  ]);

  /** Bulk Accept/Decline only the currently visible undecided rows. */
  const visiblePendingIds = useMemo(() => {
    const visible = new Set(filteredActionableItems.map((i) => i.id));
    return pendingIds.filter((id) => visible.has(id));
  }, [filteredActionableItems, pendingIds]);

  const acceptRemaining = useCallback(() => {
    setDecisionForIds(visiblePendingIds, 'accepted');
  }, [visiblePendingIds, setDecisionForIds]);

  const declineRemaining = useCallback(() => {
    setDecisionForIds(visiblePendingIds, 'declined');
  }, [visiblePendingIds, setDecisionForIds]);

  const explanationItems: NodeReviewItem[] = useMemo(
    () =>
      explanationOnly.map((p) => ({
        id: p.id,
        title: proposalNodeTitle(p),
        hasDetail: !!p.explanation,
        className: 'apme-proposal-declined',
        meta: (
          <>
            <RuleId ruleId={p.rule_id} />
            <Label color="grey" isCompact>
              No visible diff
            </Label>
          </>
        ),
        detail: p.explanation ? (
          <div className="apme-proposal-explanation">{p.explanation}</div>
        ) : undefined,
      })),
    [explanationOnly],
  );

  const declinedItems: NodeReviewItem[] = useMemo(
    () =>
      declined.map((p) => ({
        id: p.id,
        title: proposalNodeTitle(p),
        hasDetail: !!(p.explanation || p.suggestion),
        className: 'apme-proposal-declined',
        meta: <RuleId ruleId={p.rule_id} />,
        detail: (
          <div className="apme-proposal-explanation">
            {p.explanation && (
              <div style={{ marginBottom: p.suggestion ? 8 : 0 }}>
                <strong>Reason:</strong> {p.explanation}
              </div>
            )}
            {p.suggestion && (
              <div>
                <strong>Suggestion:</strong> {p.suggestion}
              </div>
            )}
          </div>
        ),
      })),
    [declined],
  );

  const filterGroups: ReviewFilterGroup[] = useMemo(
    () => [
      {
        label: 'Decision',
        ariaLabel: 'Filter by decision',
        options: DECISION_FILTER_ORDER.map((d) => ({
          id: d,
          label: DECISION_FILTER_LABELS[d],
          count: decisionCounts[d],
          selected: decisionFilters.has(d),
          onToggle: () =>
            setDecisionFilters((prev) => toggleInFilterSet(prev, d)),
        })),
      },
      {
        label: 'Severity',
        ariaLabel: 'Filter by severity',
        options: presentSeverities.map((sev) => ({
          id: sev,
          label: SEVERITY_LABELS[sev] ?? sev,
          count: severityCounts.get(sev) ?? 0,
          color: severityLabelColor(sev),
          selected: sevFilters.has(sev),
          onToggle: () => setSevFilters((prev) => toggleInFilterSet(prev, sev)),
        })),
      },
      {
        label: 'Kind',
        ariaLabel: 'Filter by kind',
        options: presentNodeTypes.map((nt) => ({
          id: nt,
          label: nodeTypeLabel(nt),
          count: nodeTypeCounts.get(nt) ?? 0,
          color: nodeTypeLabelColor(nt),
          selected: nodeTypeFilters.has(nt),
          onToggle: () =>
            setNodeTypeFilters((prev) => toggleInFilterSet(prev, nt)),
        })),
      },
    ],
    [
      decisionCounts,
      decisionFilters,
      presentSeverities,
      presentNodeTypes,
      severityCounts,
      nodeTypeCounts,
      sevFilters,
      nodeTypeFilters,
    ],
  );

  const titleProposals = useMemo(() => {
    const ids = new Set(filteredActionableItems.map((i) => i.id));
    return actionable.filter((p) => ids.has(p.id));
  }, [actionable, filteredActionableItems]);

  const visibleFindingsCount = useMemo(
    () => titleProposals.reduce((n, p) => n + proposalFindingCount(p), 0),
    [titleProposals],
  );

  const title = hasNarrowedFilters
    ? `Showing ${visibleFindingsCount} finding${
        visibleFindingsCount !== 1 ? 's' : ''
      } of ${inventory.totalFindings}`
    : undefined;

  const inventoryDescription = (
    <div>
      <ReviewInventoryRow
        ariaLabel="Proposal inventory"
        boxes={[
          {
            key: 'total',
            label: 'Total',
            primary: inventory.totalFindings,
            secondary: inventory.totalLocations,
            tone: 'total',
          },
          {
            key: 'pending',
            label: 'Undecided',
            primary: inventory.pending.findings,
            secondary: inventory.pending.locations,
            tone: 'pending',
          },
          {
            key: 'accepted',
            label: 'Accepted',
            primary: inventory.accepted.findings,
            secondary: inventory.accepted.locations,
            tone: 'accepted',
          },
          {
            key: 'declined',
            label: 'Declined',
            primary: inventory.declined.findings,
            secondary: inventory.declined.locations,
            tone: 'declined',
          },
        ]}
      />
      <div style={{ marginTop: 8, fontSize: 13, opacity: 0.7 }}>
        Every location starts undecided. Next stays disabled until you Accept or
        Decline each one. Decided rows collapse. Accept remaining / Decline
        remaining only apply to currently visible rows — widen filters to decide
        the rest, or Clear to reset and expand again.
        {explanationOnly.length > 0
          ? ` ${explanationOnly.length} explanation-only proposal${
              explanationOnly.length !== 1 ? 's' : ''
            } listed below.`
          : ''}
      </div>
    </div>
  );

  return (
    <ReviewStepShell
      title={title}
      description={inventoryDescription}
      onCancel={onCancel}
      next={workflowNext}
      filterGroups={filterGroups}
      emptyMessage="No proposals match the current filters."
      list={
        filteredActionableItems.length === 0
          ? undefined
          : {
              items: filteredActionableItems,
              ariaLabel: isAiGate ? 'AI proposals' : 'Quick-fix proposals',
              decisionMode: true,
              decisions,
              onDecisionChange: handleDecisionChange,
              resetKey: gateKey,
              defaultExpanded: true,
              showExpandControls: true,
              onAcceptRemaining: acceptRemaining,
              onDeclineRemaining: declineRemaining,
              pendingCount: visiblePendingIds.length,
              onClearDecisions: clearDecisions,
            }
      }
      afterBody={
        feedbackTarget ? (
          <FeedbackModal
            isOpen={!!feedbackTarget}
            onClose={() => setFeedbackTarget(null)}
            prefill={{
              type: 'bad_ai_suggestion',
              rule_id: feedbackTarget.rule_id,
              file: feedbackTarget.file,
              scan_id: scanId ?? '',
              context: {
                violation_message: '',
                ai_proposal_diff: feedbackTarget.diff_hunk ?? '',
                ai_explanation: feedbackTarget.explanation ?? '',
                source_snippet: '',
              },
            } satisfies Partial<FeedbackPayload>}
          />
        ) : undefined
      }
    >
      {explanationItems.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <NodeReviewList
            items={explanationItems}
            ariaLabel="Explanation-only proposals"
            resetKey={`${gateKey}-explain`}
            defaultExpanded={false}
            showExpandControls={false}
          />
        </div>
      )}

      {declined.length > 0 && (
        <div style={{ marginTop: 20 }}>
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              cursor: 'pointer',
              marginBottom: 8,
            }}
            onClick={() => setShowDeclined((v) => !v)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                setShowDeclined((v) => !v);
              }
            }}
            role="button"
            tabIndex={0}
          >
            <span style={{ opacity: 0.5, fontSize: 12 }}>
              {showDeclined ? '\u25BC' : '\u25B6'}
            </span>
            <Label color="orange" isCompact>
              Declined by AI
            </Label>
            <span style={{ fontSize: 13, opacity: 0.7 }}>
              {declined.length} violation{declined.length !== 1 ? 's' : ''} the AI
              could not fix
            </span>
          </div>
          {showDeclined && (
            <NodeReviewList
              items={declinedItems}
              ariaLabel="Declined AI proposals"
              resetKey={`${gateKey}-declined`}
              defaultExpanded={false}
              showExpandControls={false}
            />
          )}
        </div>
      )}
    </ReviewStepShell>
  );
}
