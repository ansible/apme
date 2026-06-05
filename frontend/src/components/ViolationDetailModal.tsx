import { useMemo, useState } from 'react';
import {
  Button,
  Modal,
  ModalBody,
  ModalFooter,
  ModalHeader,
  Tab,
  Tabs,
  TabTitleText,
} from '@patternfly/react-core';
import { PageDetails, PageDetail } from '@ansible/ansible-ui-framework';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { DiffView } from './DiffView';
import { FeedbackModal, type FeedbackPayload } from './FeedbackModal';
import { severityClass, severityLabel, ruleSource, scopeLabel } from './severity';
import { RuleId } from './RuleId';
import { getRuleDescription as globalGetRuleDescription } from '../data/ruleDescriptions';

function makeUnifiedDiff(original: string, fixed: string, filename: string): string {
  const a = original.split('\n');
  const b = fixed.split('\n');
  const lines: string[] = [`--- a/${filename}`, `+++ b/${filename}`, `@@ -1,${a.length} +1,${b.length} @@`];
  const max = Math.max(a.length, b.length);
  let i = 0;
  let j = 0;
  while (i < a.length || j < b.length) {
    if (i < a.length && j < b.length && a[i] === b[j]) {
      lines.push(` ${a[i]}`);
      i++;
      j++;
    } else {
      const scan = Math.min(max - Math.max(i, j), 20);
      let syncA = -1;
      let syncB = -1;
      for (let d = 0; d < scan; d++) {
        if (syncA < 0 && i + d < a.length && j < b.length && a[i + d] === b[j]) syncA = d;
        if (syncB < 0 && j + d < b.length && i < a.length && a[i] === b[j + d]) syncB = d;
        if (syncA >= 0 || syncB >= 0) break;
      }
      if (syncA >= 0 && (syncB < 0 || syncA <= syncB)) {
        for (let d = 0; d < syncA; d++) { lines.push(`-${a[i++]}`); }
      } else if (syncB >= 0) {
        for (let d = 0; d < syncB; d++) { lines.push(`+${b[j++]}`); }
      } else {
        if (i < a.length) lines.push(`-${a[i++]}`);
        if (j < b.length) lines.push(`+${b[j++]}`);
      }
    }
  }
  return lines.join('\n');
}

import { RESOLUTION_AI_ABSTAINED } from '../types/constants';

function tierLabel(rc: number, isRemediate: boolean, resolution?: number): string {
  if (resolution === RESOLUTION_AI_ABSTAINED) return 'AI Tried';
  if (rc === 1) return isRemediate ? 'Fixed' : 'Fixable';
  if (rc === 2) return 'AI';
  if (rc === 3) return 'Manual';
  return 'Unknown';
}

export interface ViolationRecord {
  id: number;
  rule_id: string;
  level: string;
  message: string;
  file: string;
  line: number | null;
  path: string;
  remediation_class: number;
  remediation_resolution?: number;
  scope?: number;
  validator_source?: string;
  original_yaml?: string;
  fixed_yaml?: string;
  co_fixes?: string[];
  node_line_start?: number;
  ai_reason?: string;
  ai_suggestion?: string;
}

interface ViolationDetailModalProps {
  isOpen: boolean;
  onClose: () => void;
  violation: ViolationRecord;
  diff?: string;
  scanType?: string;
  scanId?: string;
  feedbackEnabled?: boolean;
  onAcknowledge?: () => void;
}

export function ViolationDetailModal({ isOpen, onClose, violation, diff, scanType, scanId, feedbackEnabled, onAcknowledge }: ViolationDetailModalProps) {
  const [activeTab, setActiveTab] = useState(0);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const ruleDesc = globalGetRuleDescription(violation.rule_id);
  const cls = severityClass(violation.level, violation.rule_id);
  const source = ruleSource(violation.rule_id);
  const isRemediate = scanType === 'fix' || scanType === 'remediate';

  const hasSource = !!violation.original_yaml;
  const startLine = violation.node_line_start || 1;

  const violationDiff = useMemo(() => {
    if (violation.original_yaml && violation.fixed_yaml) {
      return makeUnifiedDiff(violation.original_yaml, violation.fixed_yaml, violation.file || 'task.yml');
    }
    return diff ?? null;
  }, [violation.original_yaml, violation.fixed_yaml, violation.file, diff]);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      aria-label="Violation details"
      width="75%"
    >
      <ModalHeader title="Violation Details" />
      <ModalBody>
        <Tabs
          aria-label="Violation detail tabs"
          activeKey={activeTab}
          onSelect={(_e, key) => setActiveTab(key as number)}
        >
          <Tab eventKey={0} title={<TabTitleText>Details</TabTitleText>} aria-label="Details tab">
              <PageDetails>
                <PageDetail label="Rule">
                  <RuleId ruleId={violation.rule_id} />
                </PageDetail>
                {source && (
                  <PageDetail label="Source">
                    {source}
                  </PageDetail>
                )}
                <PageDetail label="Severity">
                  <span className={`apme-severity ${cls}`}>
                    {severityLabel(violation.level, violation.rule_id)}
                  </span>
                </PageDetail>
                {violation.scope != null && scopeLabel(violation.scope) && (
                  <PageDetail label="Scope">
                    {scopeLabel(violation.scope)}
                  </PageDetail>
                )}
                <PageDetail label="File">
                  {violation.file || '(unknown)'}
                </PageDetail>
                <PageDetail isEmpty={violation.line == null} label="Line">
                  {violation.line}
                </PageDetail>
                <PageDetail label="Remediation">
                  {tierLabel(violation.remediation_class, isRemediate, violation.remediation_resolution)}
                </PageDetail>
                <PageDetail label="Message">
                  {violation.message}
                </PageDetail>
                {violation.path && (
                  <PageDetail label="YAML Path">
                    <code style={{ fontSize: 12 }}>{violation.path}</code>
                  </PageDetail>
                )}
                {ruleDesc && (
                  <PageDetail label="Rule Description">
                    {ruleDesc}
                  </PageDetail>
                )}
                {violation.ai_reason ? (
                  <PageDetail label="AI Reason">
                    {violation.ai_reason}
                  </PageDetail>
                ) : null}
                {violation.ai_suggestion ? (
                  <PageDetail label="Suggestion">
                    {violation.ai_suggestion}
                  </PageDetail>
                ) : null}
              </PageDetails>
          </Tab>
          {hasSource ? (
            <Tab eventKey={1} title={<TabTitleText>Source</TabTitleText>} aria-label="Source tab">
              <div className="apme-modal-diff">
                <SyntaxHighlighter
                  language="yaml"
                  style={oneDark}
                  showLineNumbers
                  startingLineNumber={startLine}
                  wrapLines
                  lineProps={(lineNo: number) => {
                    const style: React.CSSProperties = { display: 'block' };
                    if (violation.line != null && lineNo === violation.line) {
                      style.backgroundColor = 'rgba(255, 215, 0, 0.18)';
                    }
                    return { style };
                  }}
                  customStyle={{ margin: 0, fontSize: '0.85em', borderRadius: 4 }}
                >
                  {violation.original_yaml!}
                </SyntaxHighlighter>
              </div>
            </Tab>
          ) : null}
          {violationDiff ? (
            <Tab eventKey={hasSource ? 2 : 1} title={<TabTitleText>Diff</TabTitleText>} aria-label="Diff tab">
              <div className="apme-modal-diff">
                {violation.co_fixes && violation.co_fixes.length > 0 && (
                  <p style={{ fontSize: 12, opacity: 0.7, margin: '8px 0' }}>
                    This diff also includes fixes for: {violation.co_fixes.join(', ')}
                  </p>
                )}
                <DiffView diff={violationDiff} />
              </div>
            </Tab>
          ) : null}
          <Tab eventKey={(hasSource ? 1 : 0) + (violationDiff ? 1 : 0) + 1} title={<TabTitleText>Data</TabTitleText>} aria-label="Data tab">
            <div className="apme-modal-diff">
              <pre>{JSON.stringify(violation, null, 2)}</pre>
            </div>
          </Tab>
        </Tabs>
      </ModalBody>
      {(feedbackEnabled || onAcknowledge) && (
        <ModalFooter>
          {onAcknowledge && (
            <Button variant="secondary" onClick={onAcknowledge}>
              Acknowledge
            </Button>
          )}
          {feedbackEnabled && (
            <Button variant="link" onClick={() => setFeedbackOpen(true)}>Report Issue</Button>
          )}
        </ModalFooter>
      )}
      <FeedbackModal
        isOpen={feedbackOpen}
        onClose={() => setFeedbackOpen(false)}
        prefill={{
          type: 'false_positive',
          rule_id: violation.rule_id,
          source: violation.validator_source || ruleSource(violation.rule_id) || '',
          file: violation.file,
          scan_id: scanId ?? '',
          context: {
            violation_message: violation.message,
            ai_proposal_diff: violationDiff ?? diff ?? '',
            ai_explanation: '',
            source_snippet: violation.original_yaml ?? '',
          },
        } satisfies Partial<FeedbackPayload>}
      />
    </Modal>
  );
}
