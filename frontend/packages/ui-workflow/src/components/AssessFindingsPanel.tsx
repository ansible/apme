/**
 * Findings review panel — live Assess (ADR-064) and read-only Activity history.
 *
 * Domain mapping only; shared chrome lives in ReviewStepShell / NodeReviewList.
 */

import { useEffect, useMemo, useState } from 'react';
import { Alert, Label } from '@patternfly/react-core';
import { RuleId } from '../shared/RuleId';
import { CurrentYamlView } from './DiffView';
import { type NodeReviewItem } from './NodeReviewList';
import { ReviewStepShell } from './ReviewStepShell';
import { ReviewInventoryRow } from './ReviewInventoryRow';
import {
  toggleInFilterSet,
  type ReviewFilterGroup,
} from './ReviewFilterBar';
import {
  SEVERITY_LABELS,
  SEVERITY_ORDER,
  severityClass,
  severityDisplayLabel,
  severityLabelColor,
} from '../shared/severity';
import {
  resolveSnippetHighlight,
  type AssessFinding,
} from '../hooks/useProjectOperationState';
import {
  effectiveFixType,
  fixMethodLabel,
  fixTypeLabelColor,
  nodeTypeLabel,
  nodeTypeLabelColor,
  normalizeFindingNodeType,
  orderPresentNodeTypes,
  type FindingNodeType,
  type FixType,
} from '../remediation';

export interface AssessFindingsPanelProps {
  findings: AssessFinding[];
  /**
   * Live Assess CTA. When omitted, the panel is read-only (Activity history).
   */
  onRemediate?: () => void;
  /** Cancel the in-flight operation (not a soft dismiss). */
  onCancel?: () => void;
  remediating?: boolean;
  /** Override header description (defaults differ for live vs history). */
  description?: string;
  /**
   * When false, ai-candidate findings label/filter as manual (matches
   * ``effectiveFixType``). Defaults true for Activity history.
   */
  enableAi?: boolean;
  /** Begin-remediate / session error to show above the findings list. */
  error?: string | null;
}

function formatReviewStatus(status: string): string {
  return status
    .split('_')
    .filter(Boolean)
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(' ');
}

type ViewMode = 'grouped' | 'flat';

const FIX_FILTER_ORDER: FixType[] = ['auto', 'ai', 'manual'];

interface NodeGroup {
  key: string;
  title: string;
  findings: AssessFinding[];
  isSingleton: boolean;
}

function groupFindings(findings: AssessFinding[]): NodeGroup[] {
  const byPath = new Map<string, AssessFinding[]>();
  const singletons: AssessFinding[] = [];
  for (const f of findings) {
    const path = (f.path || '').trim();
    if (!path) {
      singletons.push(f);
    } else {
      const list = byPath.get(path) ?? [];
      list.push(f);
      byPath.set(path, list);
    }
  }
  const groups: NodeGroup[] = [...byPath.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([path, items]) => ({
      key: path,
      title: path,
      findings: items,
      isSingleton: false,
    }));
  if (singletons.length > 0) {
    groups.push({
      key: '__singleton__',
      title: 'Not tied to a location',
      findings: singletons,
      isSingleton: true,
    });
  }
  return groups;
}

function findingFixType(f: AssessFinding, enableAi: boolean): FixType {
  return effectiveFixType(f.remediation_class ?? 3, enableAi) ?? 'manual';
}

/** Unique graph nodes (path), plus one bucket for path-less findings. */
function uniqueNodeCount(items: AssessFinding[]): number {
  const paths = new Set<string>();
  let hasSingleton = false;
  for (const f of items) {
    const path = (f.path || '').trim();
    if (path) paths.add(path);
    else hasSingleton = true;
  }
  return paths.size + (hasSingleton ? 1 : 0);
}

function findingsPhrase(n: number): string {
  return `${n} finding${n !== 1 ? 's' : ''}`;
}

function FindingRow({
  f,
  snippetYaml,
  enableAi,
  onHighlightLine,
}: {
  f: AssessFinding;
  snippetYaml: string;
  enableAi: boolean;
  onHighlightLine?: (line: number | null) => void;
}) {
  const fix = findingFixType(f, enableAi);
  const sev = f.severity || 'info';
  const review = (f.review_status || '').trim();
  const canHighlight = onHighlightLine != null && !!snippetYaml.trim();
  return (
    <div className="apme-assess-finding-row">
      <RuleId
        ruleId={f.rule_id}
        onHoverChange={
          canHighlight
            ? (hovering) =>
                onHighlightLine(
                  hovering
                    ? resolveSnippetHighlight({
                        fileLine: f.line,
                        nodeLineStart: f.node_line_start,
                        snippet: snippetYaml,
                        message: f.message,
                      })
                    : null,
                )
            : undefined
        }
      />
      <Label isCompact color={severityLabelColor(sev, f.rule_id)}>
        {severityDisplayLabel(sev, f.rule_id)}
      </Label>
      <Label isCompact>{fixMethodLabel(fix)}</Label>
      {review ? (
        <Label isCompact color="grey">
          {formatReviewStatus(review)}
        </Label>
      ) : null}
      <span className="apme-assess-finding-msg">{f.message}</span>
      {f.file && (
        <span className="apme-assess-finding-loc">
          {f.file}
          {f.line != null && f.line > 0 ? `:${f.line}` : ''}
        </span>
      )}
    </div>
  );
}

function findingCardTitle(f: AssessFinding): string {
  const path = (f.path || '').trim();
  if (path) return path;
  if (f.file) {
    return f.line != null && f.line > 0 ? `${f.file}:${f.line}` : f.file;
  }
  return f.rule_id || 'Finding';
}

export function AssessNodeDetail({
  findings,
  enableAi,
}: {
  findings: AssessFinding[];
  enableAi: boolean;
}) {
  const [highlightLine, setHighlightLine] = useState<number | null>(null);
  // Assess/history findings are read-only: current YAML only. Proposed diffs
  // belong in ProposalReviewPanel during remediation gates (ADR-062/064).
  const beforeYaml =
    findings.find((f) => (f.original_yaml || '').trim())?.original_yaml ?? '';
  return (
    <>
      <div className="apme-assess-findings-detail">
        {findings.map((f, i) => (
          <FindingRow
            key={`${f.rule_id}-${i}`}
            f={f}
            snippetYaml={beforeYaml}
            enableAi={enableAi}
            onHighlightLine={
              beforeYaml.trim() ? setHighlightLine : undefined
            }
          />
        ))}
      </div>
      {beforeYaml.trim() ? (
        <div className="apme-proposal-diff">
          <CurrentYamlView text={beforeYaml} highlightLine={highlightLine} />
        </div>
      ) : null}
    </>
  );
}

function findingsToNodeItem(
  id: string,
  title: string,
  findings: AssessFinding[],
  opts: { isSingleton?: boolean; enableAi: boolean },
): NodeReviewItem {
  const nodeType = normalizeFindingNodeType(
    findings.find((f) => (f.node_type || '').trim())?.node_type,
  );
  return {
    id,
    title,
    hasDetail: findings.length > 0,
    className: opts.isSingleton ? 'apme-proposal-declined' : undefined,
    meta: (
      <>
        <Label isCompact variant="outline">
          {findings.length} finding{findings.length !== 1 ? 's' : ''}
        </Label>
        <Label isCompact color={nodeTypeLabelColor(nodeType)}>
          {nodeTypeLabel(nodeType)}
        </Label>
      </>
    ),
    detail: <AssessNodeDetail findings={findings} enableAi={opts.enableAi} />,
  };
}

function presentSeverityOptions(findings: AssessFinding[]): string[] {
  const present = new Set(
    findings.map((f) => severityClass(f.severity || 'info', f.rule_id)),
  );
  return SEVERITY_ORDER.filter((s) => present.has(s));
}

function presentFixTypeOptions(
  findings: AssessFinding[],
  enableAi: boolean,
): FixType[] {
  const present = new Set(findings.map((f) => findingFixType(f, enableAi)));
  return FIX_FILTER_ORDER.filter((t) => present.has(t));
}

function presentNodeTypeOptions(findings: AssessFinding[]): FindingNodeType[] {
  return orderPresentNodeTypes(
    findings.map((f) => normalizeFindingNodeType(f.node_type)),
  );
}

export function AssessFindingsPanel({
  findings,
  onRemediate,
  onCancel,
  remediating,
  description: descriptionOverride,
  enableAi = true,
  error = null,
}: AssessFindingsPanelProps) {
  const [view, setView] = useState<ViewMode>('grouped');
  const [sevFilters, setSevFilters] = useState<Set<string>>(
    () => new Set(presentSeverityOptions(findings)),
  );
  const [fixFilters, setFixFilters] = useState<Set<FixType>>(
    () => new Set(presentFixTypeOptions(findings, enableAi)),
  );
  const [nodeTypeFilters, setNodeTypeFilters] = useState<Set<FindingNodeType>>(
    () => new Set(presentNodeTypeOptions(findings)),
  );

  const presentSeverities = useMemo(
    () => presentSeverityOptions(findings),
    [findings],
  );
  const presentFixTypes = useMemo(
    () => presentFixTypeOptions(findings, enableAi),
    [findings, enableAi],
  );
  const presentNodeTypes = useMemo(
    () => presentNodeTypeOptions(findings),
    [findings],
  );

  const severityCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const f of findings) {
      const sev = severityClass(f.severity || 'info', f.rule_id);
      counts.set(sev, (counts.get(sev) ?? 0) + 1);
    }
    return counts;
  }, [findings]);

  const fixTypeCounts = useMemo(() => {
    const counts = new Map<FixType, number>();
    for (const f of findings) {
      const fix = findingFixType(f, enableAi);
      counts.set(fix, (counts.get(fix) ?? 0) + 1);
    }
    return counts;
  }, [findings, enableAi]);

  const nodeTypeCounts = useMemo(() => {
    const counts = new Map<FindingNodeType, number>();
    for (const f of findings) {
      const nt = normalizeFindingNodeType(f.node_type);
      counts.set(nt, (counts.get(nt) ?? 0) + 1);
    }
    return counts;
  }, [findings]);

  const presentSevKey = presentSeverities.join(',');
  const presentFixKey = presentFixTypes.join(',');
  const presentNodeKey = presentNodeTypes.join(',');
  useEffect(() => {
    setSevFilters(new Set(presentSeverities));
    setFixFilters(new Set(presentFixTypes));
    setNodeTypeFilters(new Set(presentNodeTypes));
  }, [
    presentSevKey,
    presentFixKey,
    presentNodeKey,
    presentSeverities,
    presentFixTypes,
    presentNodeTypes,
  ]);

  const filteredFindings = useMemo(() => {
    return findings.filter((f) => {
      const sev = severityClass(f.severity || 'info', f.rule_id);
      if (!sevFilters.has(sev)) return false;
      const fix = findingFixType(f, enableAi);
      if (!fixFilters.has(fix)) return false;
      const nt = normalizeFindingNodeType(f.node_type);
      if (!nodeTypeFilters.has(nt)) return false;
      return true;
    });
  }, [findings, sevFilters, fixFilters, nodeTypeFilters, enableAi]);

  const groups = useMemo(() => groupFindings(filteredFindings), [filteredFindings]);

  const inventory = useMemo(() => {
    const auto = findings.filter((f) => findingFixType(f, enableAi) === 'auto');
    const ai = findings.filter((f) => findingFixType(f, enableAi) === 'ai');
    const manual = findings.filter((f) => findingFixType(f, enableAi) === 'manual');
    return {
      totalFindings: findings.length,
      totalNodes: uniqueNodeCount(findings),
      autoFindings: auto.length,
      autoNodes: uniqueNodeCount(auto),
      aiFindings: ai.length,
      aiNodes: uniqueNodeCount(ai),
      manualFindings: manual.length,
      manualNodes: uniqueNodeCount(manual),
    };
  }, [findings, enableAi]);

  const hasNarrowedFilters =
    presentSeverities.some((s) => !sevFilters.has(s)) ||
    presentFixTypes.some((t) => !fixFilters.has(t)) ||
    presentNodeTypes.some((t) => !nodeTypeFilters.has(t));

  const nodeItems: NodeReviewItem[] = useMemo(() => {
    if (view === 'flat') {
      return filteredFindings.map((f, i) =>
        findingsToNodeItem(
          `flat-${f.rule_id}-${f.file}-${f.line ?? 0}-${i}`,
          findingCardTitle(f),
          [f],
          { isSingleton: !(f.path || '').trim(), enableAi },
        ),
      );
    }
    return groups.map((g) =>
      findingsToNodeItem(g.key, g.title, g.findings, {
        isSingleton: g.isSingleton,
        enableAi,
      }),
    );
  }, [view, filteredFindings, groups, enableAi]);

  const filterGroups: ReviewFilterGroup[] = useMemo(
    () => [
      {
        label: 'View',
        ariaLabel: 'Findings view',
        options: [
          {
            id: 'grouped',
            label: 'Group by location',
            selected: view === 'grouped',
            onToggle: () => setView('grouped'),
          },
          {
            id: 'flat',
            label: 'Flat list',
            selected: view === 'flat',
            onToggle: () => setView('flat'),
          },
        ],
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
        label: 'Fix type',
        ariaLabel: 'Filter by fix type',
        options: presentFixTypes.map((fix) => ({
          id: fix,
          label: fixMethodLabel(fix),
          count: fixTypeCounts.get(fix) ?? 0,
          color: fixTypeLabelColor(fix),
          selected: fixFilters.has(fix),
          onToggle: () => setFixFilters((prev) => toggleInFilterSet(prev, fix)),
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
      view,
      presentSeverities,
      presentFixTypes,
      presentNodeTypes,
      severityCounts,
      fixTypeCounts,
      nodeTypeCounts,
      sevFilters,
      fixFilters,
      nodeTypeFilters,
    ],
  );

  const title = hasNarrowedFilters
    ? `Showing ${findingsPhrase(filteredFindings.length)} of ${inventory.totalFindings}`
    : undefined;

  const inventoryDescription = (
    <div>
      <ReviewInventoryRow
        ariaLabel="Findings inventory"
        boxes={[
          {
            key: 'total',
            label: 'Total',
            primary: inventory.totalFindings,
            secondary: inventory.totalNodes,
            tone: 'total',
          },
          {
            key: 'auto',
            label: 'Quick-fix',
            primary: inventory.autoFindings,
            secondary: inventory.autoNodes,
            tone: 'auto',
          },
          {
            key: 'ai',
            label: 'AI eligible',
            primary: inventory.aiFindings,
            secondary: inventory.aiNodes,
            tone: 'ai',
          },
          {
            key: 'manual',
            label: 'Manual',
            primary: inventory.manualFindings,
            secondary: inventory.manualNodes,
            tone: 'manual',
          },
        ]}
      />
      {descriptionOverride ? (
        <div style={{ marginTop: 8, fontSize: 13, opacity: 0.7 }}>
          {descriptionOverride}
        </div>
      ) : null}
    </div>
  );

  const nextSummary =
    inventory.autoFindings > 0
      ? `Move on to remediation — review quick-fix proposals for ${findingsPhrase(inventory.autoFindings)} (same session, no rescan).`
      : 'Move on to remediation — continue this session to review any available fixes (no rescan).';

  return (
    <>
      {error ? (
        <Alert
          variant="danger"
          title="Could not continue to remediation"
          style={{ marginBottom: 12 }}
        >
          {error}
        </Alert>
      ) : null}
      <ReviewStepShell
        title={title}
        description={inventoryDescription}
        onCancel={onCancel}
        next={
          onRemediate
            ? {
                label: 'Next',
                summary: nextSummary,
                onNext: onRemediate,
                isLoading: remediating,
                isDisabled: remediating,
              }
            : undefined
        }
        filterGroups={filterGroups}
        emptyMessage="No findings match the current filters."
        list={
          filteredFindings.length === 0
            ? undefined
            : {
                items: nodeItems,
                ariaLabel:
                  view === 'flat' ? 'Findings (flat)' : 'Findings by location',
                defaultExpanded: true,
                showExpandControls: true,
                resetKey: `assess-${view}-${filteredFindings.length}-${groups.length}-${sevFilters.size}-${fixFilters.size}-${nodeTypeFilters.size}`,
              }
        }
      />
    </>
  );
}
