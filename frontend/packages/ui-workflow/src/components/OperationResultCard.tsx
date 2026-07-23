import type { ReactNode } from 'react';
import {
  Button,
  Card,
  CardBody,
  Flex,
  FlexItem,
  Split,
  SplitItem,
} from '@patternfly/react-core';
import { ExternalLinkAltIcon } from '@patternfly/react-icons';
import type { OperationResult } from '../types/operation';

export interface OperationResultCardProps {
  result: OperationResult;
  isRemediate?: boolean;
  onDismiss?: () => void;
  actions?: ReactNode;
  onCreatePR?: () => void;
  prCreating?: boolean;
  prUrl?: string | null;
  prError?: string | null;
  scanId?: string;
  /** Host navigation (no react-router in this package). */
  onViewDetails?: (scanId: string) => void;
}

function Metric({ value, label, color }: { value: number; label: string; color?: string }) {
  return (
    <SplitItem>
      <div style={{ fontSize: 32, fontWeight: 700, color }}>{value}</div>
      <div style={{ opacity: 0.7 }}>{label}</div>
    </SplitItem>
  );
}

function SmallMetric({ value, label, color }: { value: number; label: string; color?: string }) {
  if (!value) return null;
  return (
    <SplitItem>
      <div style={{ fontSize: 20, fontWeight: 700, color }}>{value}</div>
      <div style={{ opacity: 0.7, fontSize: 12 }}>{label}</div>
    </SplitItem>
  );
}

export function OperationResultCard({
  result,
  isRemediate,
  onDismiss,
  actions,
  onCreatePR,
  prCreating,
  prUrl,
  prError,
  scanId,
  onViewDetails,
}: OperationResultCardProps) {
  const wasRemediate = isRemediate ?? result.remediated_count != null;
  const hasAi = (result.ai_proposed ?? 0) > 0 || (result.ai_declined ?? 0) > 0 || (result.ai_accepted ?? 0) > 0;
  // Caller decides eligibility (e.g. patches or remediated_count via needsCommitStep).
  const showCreatePR = Boolean(onCreatePR) && !prUrl;

  return (
    <Card style={{ marginBottom: 16, borderLeft: '4px solid var(--pf-t--global--color--status--success--default)' }}>
      <CardBody style={{ textAlign: 'center', padding: 32 }}>
        <div style={{ fontSize: 48, color: 'var(--pf-t--global--color--status--success--default)' }}>&#10003;</div>
        <h2>Operation Complete</h2>

        <Split hasGutter style={{ justifyContent: 'center', margin: '16px 0' }}>
          <Metric value={result.total_violations} label="Violations" />
          {!wasRemediate && (
            <Metric value={result.fixable} label="Fixable" color="var(--pf-t--global--color--status--success--default)" />
          )}
          {wasRemediate && (
            <Metric value={result.remediated_count ?? 0} label="Remediated" color="var(--pf-t--global--color--status--success--default)" />
          )}
          <Metric value={result.manual_review} label="Manual" color="#9e8700" />
        </Split>

        {hasAi && (
          <Split hasGutter style={{ justifyContent: 'center', margin: '8px 0 16px' }}>
            <SmallMetric
              value={result.ai_proposed ?? 0}
              label="AI Proposed"
              color="var(--pf-t--global--color--status--warning--default)"
            />
            <SmallMetric
              value={result.ai_accepted ?? 0}
              label="AI Accepted"
              color="var(--pf-t--global--color--status--success--default)"
            />
            <SmallMetric
              value={result.ai_declined ?? 0}
              label="AI Declined"
              color="var(--pf-t--global--color--status--danger--default)"
            />
          </Split>
        )}

        {prError && (
          <div style={{ margin: '0 0 12px', padding: '8px 16px', borderRadius: 6, background: 'var(--pf-t--global--color--status--danger--default)', color: '#fff', fontSize: 13 }}>
            PR creation failed: {prError}
          </div>
        )}

        <Flex justifyContent={{ default: 'justifyContentCenter' }} gap={{ default: 'gapSm' }}>
          {actions}
          {prUrl && (
            <FlexItem>
              <Button
                variant="secondary"
                component="a"
                href={prUrl}
                target="_blank"
                rel="noopener noreferrer"
                icon={<ExternalLinkAltIcon />}
                iconPosition="end"
              >
                View Pull Request
              </Button>
            </FlexItem>
          )}
          {showCreatePR && (
            <FlexItem>
              <Button
                variant="secondary"
                onClick={onCreatePR}
                isLoading={prCreating}
                isDisabled={prCreating}
              >
                {prCreating ? 'Creating PR...' : 'Create PR'}
              </Button>
            </FlexItem>
          )}
          {scanId && onViewDetails && (
            <FlexItem>
              <Button variant="secondary" onClick={() => onViewDetails(scanId)}>
                View details
              </Button>
            </FlexItem>
          )}
          {onDismiss && (
            <FlexItem>
              <Button variant="link" onClick={onDismiss}>Dismiss</Button>
            </FlexItem>
          )}
        </Flex>
      </CardBody>
    </Card>
  );
}
