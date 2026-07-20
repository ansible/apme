/**
 * Findings review panel — live Assess (ADR-064) and read-only Activity history.
 *
 * Domain mapping only; shared chrome lives in ReviewStepShell / NodeReviewList.
 */

import { useEffect, useMemo, useState } from 'react';
import { Label } from '@patternfly/react-core';
import { RuleId } from './RuleId';
import { CurrentYamlView, DiffView } from './DiffView';
import { type NodeReviewItem } from './NodeReviewList';
import { ReviewStepShell } from './ReviewStepShell';
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
} from './severity';
import {
  resolveSnippetHighlight,
  type AssessFinding,
} from '../hooks/useProjectOperationState';
import {
  effectiveFixType,
  fixMethodLabel,
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
      title: 'Not tied to a task node',
      findings: singletons,
      isSingleton: true,
    });
  }
  return groups;
}

function findingFixType(f: AssessFinding): FixType {
  return effectiveFixType(f.remediation_class ?? 3, true) ?? 'manual';
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

function nodesPhrase(n: number): string {
  return `${n} node${n !== 1 ? 's' : ''}`;
}

function FindingRow({
  f,
  snippetYaml,
  onHighlightLine,
}: {
  f: AssessFinding;
  snippetYaml: string;
  onHighlightLine?: (line: number | null) => void;
}) {
  const fix = findingFixType(f);
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

function AssessNodeDetail({ findings }: { findings: AssessFinding[] }) {
  const [highlightLine, setHighlightLine] = useState<number | null>(null);
  const beforeYaml =
    findings.find((f) => (f.original_yaml || '').trim())?.original_yaml ?? '';
  const afterYaml =
    findings.find((f) => (f.fixed_yaml || '').trim())?.fixed_yaml ?? '';
  const showDiff = Boolean(beforeYaml.trim() && afterYaml.trim());
  return (
    <>
      <div className="apme-assess-findings-detail">
        {findings.map((f, i) => (
          <FindingRow
            key={`${f.rule_id}-${i}`}
            f={f}
            snippetYaml={beforeYaml}
            onHighlightLine={
              beforeYaml.trim() ? setHighlightLine : undefined
            }
          />
        ))}
      </div>
      {showDiff ? (
        <div className="apme-proposal-diff">
          <DiffView
            mode="side-by-side"
            before={beforeYaml}
            after={afterYaml}
            highlightLine={highlightLine}
          />
        </div>
      ) : beforeYaml.trim() ? (
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
  opts?: { isSingleton?: boolean },
): NodeReviewItem {
  const ruleIds = [...new Set(findings.map((f) => f.rule_id))];
  const rulePreview = ruleIds.slice(0, 3);
  return {
    id,
    title,
    hasDetail: findings.length > 0,
    className: opts?.isSingleton ? 'apme-proposal-declined' : undefined,
    meta: (
      <>
        <Label isCompact variant="outline">
          {findings.length} finding{findings.length !== 1 ? 's' : ''}
        </Label>
        {rulePreview.map((rid) => (
          <RuleId key={rid} ruleId={rid} />
        ))}
        {ruleIds.length > rulePreview.length && (
          <span style={{ fontSize: 12, opacity: 0.6 }}>
            +{ruleIds.length - rulePreview.length}
          </span>
        )}
      </>
    ),
    detail: <AssessNodeDetail findings={findings} />,
  };
}

function presentSeverityOptions(findings: AssessFinding[]): string[] {
  const present = new Set(
    findings.map((f) => severityClass(f.severity || 'info', f.rule_id)),
  );
  return SEVERITY_ORDER.filter((s) => present.has(s));
}

function presentFixTypeOptions(findings: AssessFinding[]): FixType[] {
  const present = new Set(findings.map(findingFixType));
  return FIX_FILTER_ORDER.filter((t) => present.has(t));
}

export function AssessFindingsPanel({
  findings,
  onRemediate,
  onCancel,
  remediating,
  description: descriptionOverride,
}: AssessFindingsPanelProps) {
  const [view, setView] = useState<ViewMode>('grouped');
  const [sevFilters, setSevFilters] = useState<Set<string>>(
    () => new Set(presentSeverityOptions(findings)),
  );
  const [fixFilters, setFixFilters] = useState<Set<FixType>>(
    () => new Set(presentFixTypeOptions(findings)),
  );

  const presentSeverities = useMemo(
    () => presentSeverityOptions(findings),
    [findings],
  );
  const presentFixTypes = useMemo(
    () => presentFixTypeOptions(findings),
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
      const fix = findingFixType(f);
      counts.set(fix, (counts.get(fix) ?? 0) + 1);
    }
    return counts;
  }, [findings]);

  const presentSevKey = presentSeverities.join(',');
  const presentFixKey = presentFixTypes.join(',');
  useEffect(() => {
    setSevFilters(new Set(presentSeverities));
    setFixFilters(new Set(presentFixTypes));
  }, [presentSevKey, presentFixKey, presentSeverities, presentFixTypes]);

  const filteredFindings = useMemo(() => {
    return findings.filter((f) => {
      const sev = severityClass(f.severity || 'info', f.rule_id);
      if (!sevFilters.has(sev)) return false;
      const fix = findingFixType(f);
      if (!fixFilters.has(fix)) return false;
      return true;
    });
  }, [findings, sevFilters, fixFilters]);

  const groups = useMemo(() => groupFindings(filteredFindings), [filteredFindings]);

  const inventory = useMemo(() => {
    const auto = findings.filter((f) => findingFixType(f) === 'auto');
    const ai = findings.filter((f) => findingFixType(f) === 'ai');
    const manual = findings.filter((f) => findingFixType(f) === 'manual');
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
  }, [findings]);

  const hasNarrowedFilters =
    presentSeverities.some((s) => !sevFilters.has(s)) ||
    presentFixTypes.some((t) => !fixFilters.has(t));

  const nodeItems: NodeReviewItem[] = useMemo(() => {
    if (view === 'flat') {
      return filteredFindings.map((f, i) =>
        findingsToNodeItem(
          `flat-${f.rule_id}-${f.file}-${f.line ?? 0}-${i}`,
          findingCardTitle(f),
          [f],
          { isSingleton: !(f.path || '').trim() },
        ),
      );
    }
    return groups.map((g) =>
      findingsToNodeItem(g.key, g.title, g.findings, {
        isSingleton: g.isSingleton,
      }),
    );
  }, [view, filteredFindings, groups]);

  const filterGroups: ReviewFilterGroup[] = useMemo(
    () => [
      {
        label: 'View',
        ariaLabel: 'Findings view',
        options: [
          {
            id: 'grouped',
            label: 'Group by node',
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
          selected: fixFilters.has(fix),
          onToggle: () => setFixFilters((prev) => toggleInFilterSet(prev, fix)),
        })),
      },
    ],
    [
      view,
      presentSeverities,
      presentFixTypes,
      severityCounts,
      fixTypeCounts,
      sevFilters,
      fixFilters,
    ],
  );

  const title = hasNarrowedFilters
    ? `Showing ${findingsPhrase(filteredFindings.length)} of ${inventory.totalFindings}`
    : 'Findings';

  const inventoryDescription = (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <span>
        Total: {findingsPhrase(inventory.totalFindings)} across{' '}
        {nodesPhrase(inventory.totalNodes)}
      </span>
      <span>
        Quick-fix: {findingsPhrase(inventory.autoFindings)} across{' '}
        {nodesPhrase(inventory.autoNodes)}
      </span>
      <span>
        AI eligible: {findingsPhrase(inventory.aiFindings)} across{' '}
        {nodesPhrase(inventory.aiNodes)}
      </span>
      <span>
        Manual: {findingsPhrase(inventory.manualFindings)} across{' '}
        {nodesPhrase(inventory.manualNodes)}
      </span>
      {descriptionOverride ? (
        <span style={{ marginTop: 4 }}>{descriptionOverride}</span>
      ) : null}
    </div>
  );

  const nextSummary =
    inventory.autoFindings > 0
      ? `Move on to remediation — review quick-fix proposals for ${findingsPhrase(inventory.autoFindings)} (same session, no rescan).`
      : 'Move on to remediation — continue this session to review any available fixes (no rescan).';

  return (
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
      hasNarrowedFilters={hasNarrowedFilters}
      onSelectAllFilters={() => {
        setSevFilters(new Set(presentSeverities));
        setFixFilters(new Set(presentFixTypes));
      }}
      emptyMessage="No findings match the current filters."
      list={
        filteredFindings.length === 0
          ? undefined
          : {
              items: nodeItems,
              ariaLabel:
                view === 'flat' ? 'Findings (flat)' : 'Findings by node',
              defaultExpanded: true,
              showExpandControls: true,
              resetKey: `assess-${view}-${filteredFindings.length}-${groups.length}-${sevFilters.size}-${fixFilters.size}`,
            }
      }
    />
  );
}
