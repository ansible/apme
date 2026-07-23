/**
 * AI escalation triage — Include / Skip locations before Gate 2 (ADR-062).
 *
 * Same review chrome as Assess / Gate panels: inventory, Decision/Severity/Kind
 * filters, YAML expand detail. Grain is per location (path). Defaults:
 * undecided. Next requires every location decided; none Included skips AI.
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { Alert, Label } from '@patternfly/react-core';
import type { AssessFinding } from '../hooks/useProjectOperationState';
import {
  normalizeFindingNodeType,
  nodeTypeLabel,
  nodeTypeLabelColor,
  orderPresentNodeTypes,
  type FindingNodeType,
} from '../remediation/nodeType';
import { AssessNodeDetail } from './AssessFindingsPanel';
import {
  toggleInFilterSet,
  type ReviewFilterGroup,
} from './ReviewFilterBar';
import { ReviewInventoryRow } from './ReviewInventoryRow';
import { ReviewStepShell } from './ReviewStepShell';
import type { NodeDecision, NodeReviewItem } from './NodeReviewList';
import {
  SEVERITY_LABELS,
  SEVERITY_ORDER,
  severityClass,
  severityLabelColor,
} from '../shared/severity';

export interface AiEscalateTarget {
  path: string;
  rule_ids?: string[];
}

export interface AiEscalationPanelProps {
  candidates: AssessFinding[];
  onEscalate: (targets: AiEscalateTarget[]) => void | Promise<void>;
  onCancel?: () => void;
  escalating?: boolean;
  error?: string | null;
}

type DecisionFilter = 'pending' | 'accepted' | 'declined';

const DECISION_FILTER_ORDER: DecisionFilter[] = [
  'pending',
  'accepted',
  'declined',
];
const DECISION_FILTER_LABELS: Record<DecisionFilter, string> = {
  pending: 'Undecided',
  accepted: 'Include',
  declined: 'Skip',
};

interface LocationGroup {
  path: string;
  title: string;
  findings: AssessFinding[];
}

function groupByLocation(candidates: AssessFinding[]): LocationGroup[] {
  const byPath = new Map<string, AssessFinding[]>();
  for (const c of candidates) {
    const path = (c.path || '').trim() || (c.file || '').trim() || '(unknown)';
    const list = byPath.get(path) ?? [];
    list.push(c);
    byPath.set(path, list);
  }
  return [...byPath.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([path, findings]) => ({
      path,
      title: path,
      findings,
    }));
}

function locationPath(f: AssessFinding): string {
  return (f.path || '').trim() || (f.file || '').trim() || '(unknown)';
}

function presentSeverityOptions(findings: AssessFinding[]): string[] {
  const present = new Set(
    findings.map((f) => severityClass(f.severity || 'info', f.rule_id)),
  );
  return SEVERITY_ORDER.filter((s) => present.has(s));
}

function presentNodeTypeOptions(findings: AssessFinding[]): FindingNodeType[] {
  return orderPresentNodeTypes(
    findings.map((f) => normalizeFindingNodeType(f.node_type)),
  );
}

function effectiveDecision(
  path: string,
  decisions: Map<string, NodeDecision>,
): DecisionFilter {
  const d = decisions.get(path);
  if (d === 'accepted') return 'accepted';
  if (d === 'declined' || d === 'ignored') return 'declined';
  return 'pending';
}

function findingsPhrase(n: number): string {
  return `${n} finding${n !== 1 ? 's' : ''}`;
}

export function AiEscalationPanel({
  candidates,
  onEscalate,
  onCancel,
  escalating = false,
  error = null,
}: AiEscalationPanelProps) {
  const allGroups = useMemo(() => groupByLocation(candidates), [candidates]);
  const pathKey = useMemo(
    () => allGroups.map((g) => g.path).join('\0'),
    [allGroups],
  );

  const [decisions, setDecisions] = useState<Map<string, NodeDecision>>(
    () => new Map(allGroups.map((g) => [g.path, 'pending' as NodeDecision])),
  );
  const [decisionFilters, setDecisionFilters] = useState<Set<DecisionFilter>>(
    () => new Set(DECISION_FILTER_ORDER),
  );
  const [sevFilters, setSevFilters] = useState<Set<string>>(
    () => new Set(presentSeverityOptions(candidates)),
  );
  const [nodeTypeFilters, setNodeTypeFilters] = useState<Set<FindingNodeType>>(
    () => new Set(presentNodeTypeOptions(candidates)),
  );

  useEffect(() => {
    const paths = pathKey ? pathKey.split('\0') : [];
    setDecisions(
      new Map(paths.map((p) => [p, 'pending' as NodeDecision])),
    );
  }, [pathKey]);

  const presentSeverities = useMemo(
    () => presentSeverityOptions(candidates),
    [candidates],
  );
  const presentNodeTypes = useMemo(
    () => presentNodeTypeOptions(candidates),
    [candidates],
  );

  const severityCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const f of candidates) {
      const sev = severityClass(f.severity || 'info', f.rule_id);
      counts.set(sev, (counts.get(sev) ?? 0) + 1);
    }
    return counts;
  }, [candidates]);

  const nodeTypeCounts = useMemo(() => {
    const counts = new Map<FindingNodeType, number>();
    for (const f of candidates) {
      const nt = normalizeFindingNodeType(f.node_type);
      counts.set(nt, (counts.get(nt) ?? 0) + 1);
    }
    return counts;
  }, [candidates]);

  const decisionCounts = useMemo(() => {
    const counts: Record<DecisionFilter, number> = {
      pending: 0,
      accepted: 0,
      declined: 0,
    };
    for (const g of allGroups) {
      counts[effectiveDecision(g.path, decisions)] += 1;
    }
    return counts;
  }, [allGroups, decisions]);

  const presentSevKey = presentSeverities.join(',');
  const presentNodeKey = presentNodeTypes.join(',');
  useEffect(() => {
    setSevFilters(new Set(presentSeverities));
    setNodeTypeFilters(new Set(presentNodeTypes));
  }, [presentSevKey, presentNodeKey, presentSeverities, presentNodeTypes]);

  const handleDecisionChange = useCallback(
    (id: string, decision: NodeDecision) => {
      setDecisions((prev) => {
        const next = new Map(prev);
        next.set(id, decision);
        return next;
      });
    },
    [],
  );

  const includeCount = decisionCounts.accepted;
  const skipCount = decisionCounts.declined;
  const pendingCount = decisionCounts.pending;

  const includeFindings = useMemo(() => {
    let n = 0;
    for (const g of allGroups) {
      if (decisions.get(g.path) === 'accepted') n += g.findings.length;
    }
    return n;
  }, [allGroups, decisions]);

  const skipFindings = useMemo(() => {
    let n = 0;
    for (const g of allGroups) {
      if (decisions.get(g.path) === 'declined') n += g.findings.length;
    }
    return n;
  }, [allGroups, decisions]);

  const pendingFindings = useMemo(() => {
    let n = 0;
    for (const g of allGroups) {
      if ((decisions.get(g.path) ?? 'pending') === 'pending') {
        n += g.findings.length;
      }
    }
    return n;
  }, [allGroups, decisions]);

  const filteredFindings = useMemo(() => {
    return candidates.filter((f) => {
      const sev = severityClass(f.severity || 'info', f.rule_id);
      if (!sevFilters.has(sev)) return false;
      const nt = normalizeFindingNodeType(f.node_type);
      if (!nodeTypeFilters.has(nt)) return false;
      const path = locationPath(f);
      if (!decisionFilters.has(effectiveDecision(path, decisions))) return false;
      return true;
    });
  }, [candidates, sevFilters, nodeTypeFilters, decisionFilters, decisions]);

  const filteredGroups = useMemo(
    () => groupByLocation(filteredFindings),
    [filteredFindings],
  );

  const hasNarrowedFilters =
    decisionFilters.size < DECISION_FILTER_ORDER.length ||
    presentSeverities.some((s) => !sevFilters.has(s)) ||
    presentNodeTypes.some((t) => !nodeTypeFilters.has(t));

  const listItems: NodeReviewItem[] = useMemo(
    () =>
      filteredGroups.map((g) => {
        const nt = normalizeFindingNodeType(
          g.findings.find((f) => (f.node_type || '').trim())?.node_type,
        );
        return {
          id: g.path,
          title: g.title,
          meta: (
            <>
              <Label isCompact variant="outline">
                {g.findings.length} finding
                {g.findings.length !== 1 ? 's' : ''}
              </Label>
              <Label isCompact color={nodeTypeLabelColor(nt)}>
                {nodeTypeLabel(nt)}
              </Label>
            </>
          ),
          hasDetail: g.findings.length > 0,
          detail: <AssessNodeDetail findings={g.findings} enableAi />,
        };
      }),
    [filteredGroups],
  );

  /** Bulk Include/Skip only currently visible undecided locations. */
  const visiblePendingPaths = useMemo(
    () =>
      filteredGroups
        .map((g) => g.path)
        .filter((p) => (decisions.get(p) ?? 'pending') === 'pending'),
    [filteredGroups, decisions],
  );

  const includeRemaining = useCallback(() => {
    setDecisions((prev) => {
      const next = new Map(prev);
      for (const path of visiblePendingPaths) {
        next.set(path, 'accepted');
      }
      return next;
    });
  }, [visiblePendingPaths]);

  const skipRemaining = useCallback(() => {
    setDecisions((prev) => {
      const next = new Map(prev);
      for (const path of visiblePendingPaths) {
        next.set(path, 'declined');
      }
      return next;
    });
  }, [visiblePendingPaths]);

  const clearDecisions = useCallback(() => {
    setDecisions(
      new Map(allGroups.map((g) => [g.path, 'pending' as NodeDecision])),
    );
  }, [allGroups]);

  const handleNext = useCallback(() => {
    const targets: AiEscalateTarget[] = allGroups
      .filter((g) => decisions.get(g.path) === 'accepted')
      .map((g) => ({ path: g.path, rule_ids: [] }));
    void onEscalate(targets);
  }, [allGroups, decisions, onEscalate]);

  const nextSummary =
    pendingCount > 0
      ? `Decide Include or Skip for ${pendingCount} remaining location${pendingCount !== 1 ? 's' : ''}.`
      : includeCount === 0
        ? 'Skip AI escalation and finish remediation (no AI proposals).'
        : `Run AI on ${includeCount} location${includeCount !== 1 ? 's' : ''} (${includeFindings} finding${includeFindings !== 1 ? 's' : ''})${skipCount > 0 ? `; skip ${skipCount}` : ''}.`;

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

  const title = hasNarrowedFilters
    ? `Showing ${findingsPhrase(filteredFindings.length)} of ${candidates.length}`
    : undefined;

  const inventoryDescription = (
    <div>
      <ReviewInventoryRow
        ariaLabel="AI escalation inventory"
        boxes={[
          {
            key: 'total',
            label: 'Total',
            primary: candidates.length,
            secondary: allGroups.length,
            tone: 'total',
          },
          {
            key: 'pending',
            label: 'Undecided',
            primary: pendingFindings,
            secondary: pendingCount,
            tone: 'pending',
          },
          {
            key: 'include',
            label: 'Include',
            primary: includeFindings,
            secondary: includeCount,
            tone: 'accepted',
          },
          {
            key: 'skip',
            label: 'Skip',
            primary: skipFindings,
            secondary: skipCount,
            tone: 'declined',
          },
        ]}
      />
      <div style={{ marginTop: 8, fontSize: 13, opacity: 0.7 }}>
        Every location starts undecided. Next stays disabled until you Include
        or Skip each one. Include remaining / Skip remaining only apply to
        currently visible rows — widen filters to decide the rest, or Clear to
        reset. Next with none Included skips AI entirely.
      </div>
    </div>
  );

  return (
    <>
      {error ? (
        <Alert
          variant="danger"
          title="Could not start AI escalation"
          style={{ marginBottom: 12 }}
        >
          {error}
        </Alert>
      ) : null}
      <ReviewStepShell
        title={title}
        description={inventoryDescription}
        onCancel={onCancel}
        next={{
          label: 'Next',
          summary: nextSummary,
          onNext: handleNext,
          isLoading: escalating,
          isDisabled: escalating || pendingCount > 0,
        }}
        filterGroups={filterGroups}
        emptyMessage="No locations match the current filters."
        list={
          listItems.length === 0
            ? undefined
            : {
                items: listItems,
                ariaLabel: 'AI escalation by location',
                decisionMode: true,
                decisions,
                onDecisionChange: handleDecisionChange,
                defaultExpanded: false,
                showExpandControls: true,
                resetKey: `ai-esc-${pathKey}-${filteredFindings.length}-${sevFilters.size}-${nodeTypeFilters.size}-${decisionFilters.size}`,
                onAcceptRemaining: includeRemaining,
                onDeclineRemaining: skipRemaining,
                pendingCount: visiblePendingPaths.length,
                onClearDecisions: clearDecisions,
                acceptLabel: 'Include',
                declineLabel: 'Skip',
                acceptRemainingLabel: 'Include remaining',
                declineRemainingLabel: 'Skip remaining',
              }
        }
      />
    </>
  );
}
