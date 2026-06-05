import { useMemo, useRef, useState } from 'react';
import { Button } from '@patternfly/react-core';
import {
  AngleDownIcon,
  AngleRightIcon,
  AngleDoubleUpIcon,
  AngleDoubleDownIcon,
} from '@patternfly/react-icons';
import { ViolationDetailModal } from './ViolationDetailModal';
import { severityClass, severityLabel, severityOrder, SEVERITY_ORDER } from './severity';
import { RuleId } from './RuleId';
import type { ViolationDetail } from '../types/api';

const DEP_HEALTH_SOURCES = new Set(['collection_health', 'dep_audit']);

export function isDepHealthViolation(v: ViolationDetail): boolean {
  return DEP_HEALTH_SOURCES.has(v.validator_source ?? '');
}

interface DependencyHealthOutputProps {
  violations: ViolationDetail[];
  scanType?: string;
  scanId?: string;
  feedbackEnabled?: boolean;
  onAcknowledge?: (violation: ViolationDetail) => void;
  acknowledgedIds?: Set<number>;
}

interface DepGroup {
  key: string;
  label: string;
  kind: 'collection' | 'python';
  items: ViolationDetail[];
}

function groupDepViolations(violations: ViolationDetail[]): DepGroup[] {
  const collMap = new Map<string, ViolationDetail[]>();
  const cveList: ViolationDetail[] = [];

  for (const v of violations) {
    if (!isDepHealthViolation(v)) continue;
    if (v.validator_source === 'dep_audit') {
      cveList.push(v);
    } else if (v.validator_source === 'collection_health') {
      const fqcn = v.path || 'unknown';
      const arr = collMap.get(fqcn) ?? [];
      arr.push(v);
      collMap.set(fqcn, arr);
    }
  }

  const groups: DepGroup[] = [];

  const collEntries = Array.from(collMap.entries()).sort(
    (a, b) => b[1].length - a[1].length,
  );
  for (const [fqcn, items] of collEntries) {
    groups.push({ key: `coll:${fqcn}`, label: fqcn, kind: 'collection', items });
  }

  if (cveList.length > 0) {
    groups.push({ key: 'python-cves', label: 'Python CVEs', kind: 'python', items: cveList });
  }

  return groups;
}

function sevSummaryText(items: ViolationDetail[]): string {
  const counts = new Map<string, number>();
  for (const v of items) {
    const cls = severityClass(v.level, v.rule_id);
    counts.set(cls, (counts.get(cls) ?? 0) + 1);
  }
  const parts: string[] = [];
  for (const sev of SEVERITY_ORDER as readonly string[]) {
    const c = counts.get(sev);
    if (c) parts.push(`${c} ${sev}`);
  }
  return parts.join(', ');
}

export function DependencyHealthOutput({ violations, scanType, scanId, feedbackEnabled, onAcknowledge, acknowledgedIds }: DependencyHealthOutputProps) {
  const depViolations = useMemo(
    () => violations.filter(
      (v) => isDepHealthViolation(v) && !v.suppressed && !acknowledgedIds?.has(v.id),
    ),
    [violations, acknowledgedIds],
  );

  const suppressedCount = useMemo(
    () => violations.filter(
      (v) => isDepHealthViolation(v) && (v.suppressed || acknowledgedIds?.has(v.id)),
    ).length,
    [violations, acknowledgedIds],
  );

  const groups = useMemo(() => groupDepViolations(depViolations), [depViolations]);

  const [sectionOpen, setSectionOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [allCollapsed, setAllCollapsed] = useState(false);
  const [selectedViolation, setSelectedViolation] = useState<ViolationDetail | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const toggleGroup = (key: string) => {
    setCollapsed(prev => ({ ...prev, [key]: !prev[key] }));
  };

  const expandAll = () => {
    const next: Record<string, boolean> = {};
    for (const g of groups) next[g.key] = false;
    setCollapsed(next);
    setAllCollapsed(false);
  };

  const collapseAll = () => {
    const next: Record<string, boolean> = {};
    for (const g of groups) next[g.key] = true;
    setCollapsed(next);
    setAllCollapsed(true);
  };

  const toggleAll = () => {
    if (allCollapsed) expandAll(); else collapseAll();
  };

  const isCollapsed = (key: string) => collapsed[key] === true;

  if (depViolations.length === 0 && suppressedCount === 0) return null;

  const collCount = depViolations.filter(v => v.validator_source === 'collection_health').length;
  const cveCount = depViolations.filter(v => v.validator_source === 'dep_audit').length;

  const summaryParts: string[] = [];
  if (collCount > 0) summaryParts.push(`${collCount} collection`);
  if (cveCount > 0) summaryParts.push(`${cveCount} CVE`);
  if (suppressedCount > 0) summaryParts.push(`${suppressedCount} acknowledged`);

  return (
    <div className={`apme-output-panel ${sectionOpen ? 'apme-panel-open' : 'apme-panel-closed'}`}>
      <div className="apme-output-controls">
        <div className="apme-output-controls-left">
          <Button
            variant="plain"
            onClick={() => setSectionOpen(prev => !prev)}
            aria-label={sectionOpen ? 'Hide dependency health' : 'Show dependency health'}
            icon={sectionOpen ? <AngleDownIcon /> : <AngleRightIcon />}
            size="sm"
          />
          <span
            style={{ fontSize: 13, fontWeight: 600, paddingLeft: 4, cursor: 'pointer' }}
            role="button"
            tabIndex={0}
            onClick={() => setSectionOpen(prev => !prev)}
            onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') setSectionOpen(prev => !prev); }}
          >
            Dependencies ({depViolations.length})
            <span style={{ fontWeight: 400, opacity: 0.7, marginLeft: 8 }}>
              {summaryParts.join(' · ')}
            </span>
          </span>
        </div>
        {sectionOpen && (
          <div className="apme-output-controls-right">
            <Button
              variant="plain"
              onClick={toggleAll}
              aria-label={allCollapsed ? 'Expand all groups' : 'Collapse all groups'}
              icon={allCollapsed ? <AngleRightIcon /> : <AngleDownIcon />}
              size="sm"
            />
            <Button
              variant="plain"
              onClick={() => scrollRef.current?.scrollTo({ top: 0, behavior: 'smooth' })}
              icon={<AngleDoubleUpIcon />}
              aria-label="Scroll to top"
              size="sm"
            />
            <Button
              variant="plain"
              onClick={() => {
                const el = scrollRef.current;
                if (el) el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
              }}
              icon={<AngleDoubleDownIcon />}
              aria-label="Scroll to bottom"
              size="sm"
            />
          </div>
        )}
      </div>

      {sectionOpen && <div className="apme-output-scroll" ref={scrollRef}>
        <div className="apme-output-grid">
          {groups.map((group) => {
            const sorted = [...group.items].sort(
              (a, b) => severityOrder(severityClass(a.level, a.rule_id)) - severityOrder(severityClass(b.level, b.rule_id)),
            );

            return (
              <div className="apme-output-file-group" key={group.key}>
                <div
                  className="apme-output-row apme-output-row-header"
                  role="button"
                  tabIndex={0}
                  onClick={() => toggleGroup(group.key)}
                  onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') toggleGroup(group.key); }}
                >
                  <span className="apme-output-gutter">
                    {isCollapsed(group.key) ? <AngleRightIcon /> : <AngleDownIcon />}
                  </span>
                  <span className="apme-output-content apme-output-file-line">
                    <span className="apme-output-file-path">
                      {group.kind === 'collection' ? `📦 ${group.label}` : `🐍 ${group.label}`}
                    </span>
                    <span className="apme-output-file-meta">
                      {group.items.length} finding{group.items.length !== 1 ? 's' : ''}
                      <span style={{ marginLeft: 8, opacity: 0.7, fontSize: 11 }}>
                        {sevSummaryText(group.items)}
                      </span>
                    </span>
                  </span>
                </div>

                {isCollapsed(group.key) && (
                  <div className="apme-output-row apme-output-row-ellipsis">
                    <span className="apme-output-gutter" />
                    <span className="apme-output-content">...</span>
                  </div>
                )}

                {!isCollapsed(group.key) && sorted.map((v) => {
                  const cveMatch = v.validator_source === 'dep_audit'
                    ? v.message.match(/CVE-\d{4}-\d+/)
                    : null;
                  return (
                    <div
                      className="apme-output-row apme-output-row-item"
                      key={v.id}
                      role="button"
                      tabIndex={0}
                      aria-label={`View details for ${v.rule_id}: ${v.message}`}
                      onClick={() => setSelectedViolation(v)}
                      onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setSelectedViolation(v); } }}
                    >
                      <span className="apme-output-gutter apme-output-line-num">
                        {v.line != null && v.line > 0 ? v.line : ''}
                      </span>
                      <span className="apme-output-content apme-output-violation-line">
                        <span className={`apme-severity ${severityClass(v.level, v.rule_id)}`}>
                          {severityLabel(v.level, v.rule_id)}
                        </span>
                        {cveMatch
                          ? <span className="apme-rule-id">{cveMatch[0]}</span>
                          : <RuleId ruleId={v.rule_id} />
                        }
                        <span className="apme-output-violation-msg">
                          {v.message}
                        </span>
                        {v.file && v.validator_source !== 'dep_audit' && (
                          <span style={{ opacity: 0.5, fontSize: 11, marginLeft: 8 }}>
                            {v.file}
                          </span>
                        )}
                      </span>
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>}

      {selectedViolation && (
        <ViolationDetailModal
          isOpen={!!selectedViolation}
          onClose={() => setSelectedViolation(null)}
          violation={selectedViolation}
          scanType={scanType}
          scanId={scanId}
          feedbackEnabled={feedbackEnabled}
          onAcknowledge={onAcknowledge ? () => {
            const v = selectedViolation;
            setSelectedViolation(null);
            if (v) onAcknowledge(v);
          } : undefined}
        />
      )}
    </div>
  );
}
