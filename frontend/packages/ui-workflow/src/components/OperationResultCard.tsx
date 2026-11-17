import type { ReactNode } from 'react';
import { Button, ButtonLink, Alert, Card, CardBody, Flex } from '@backstage/ui';
import { RiExternalLinkLine } from '@remixicon/react';
import type { OperationResult } from '../types/operation';
import { toPrFilesDiffUrl } from '../utils/prDiffUrl';

export interface OperationResultCardProps {
  result: OperationResult;
  isRemediate?: boolean;
  onDismiss?: () => void;
  actions?: ReactNode;
  /** Host controls beside an existing pull request action. */
  hostShipActions?: ReactNode;
  prUrl?: string | null;
  prError?: string | null;
  scanId?: string;
  /** Host navigation for durable run details. */
  onViewDetails?: (scanId: string) => void;
}

function Metric({
  value,
  label,
  color,
}: {
  value: number;
  label: string;
  color?: string;
}) {
  return (
    <div>
      <div style={{ fontSize: 32, fontWeight: 700, color }}>{value}</div>
      <div style={{ opacity: 0.7 }}>{label}</div>
    </div>
  );
}

function SmallMetric({
  value,
  label,
  color,
}: {
  value: number;
  label: string;
  color?: string;
}) {
  if (!value) return null;
  return (
    <div>
      <div style={{ fontSize: 20, fontWeight: 700, color }}>{value}</div>
      <div style={{ opacity: 0.7, fontSize: 12 }}>{label}</div>
    </div>
  );
}

export function OperationResultCard({
  result,
  isRemediate,
  onDismiss,
  actions,
  hostShipActions,
  prUrl,
  prError,
  scanId,
  onViewDetails,
}: OperationResultCardProps) {
  const wasRemediate = isRemediate ?? result.remediated_count != null;
  const hasAi =
    (result.ai_proposed ?? 0) > 0 ||
    (result.ai_declined ?? 0) > 0 ||
    (result.ai_accepted ?? 0) > 0;

  return (
    <Card
      style={{
        marginBottom: 16,
        borderLeft: '4px solid var(--bui-fg-success)',
      }}
    >
      <CardBody style={{ textAlign: 'center', padding: 32 }}>
        <div style={{ fontSize: 48, color: 'var(--bui-fg-success)' }}>
          &#10003;
        </div>
        <h2>Operation Complete</h2>

        <Flex gap="4" style={{ justifyContent: 'center', margin: '16px 0' }}>
          <Metric value={result.total_violations} label="Violations" />
          {!wasRemediate && (
            <Metric
              value={result.fixable}
              label="Fixable"
              color="var(--bui-fg-success)"
            />
          )}
          {wasRemediate && (
            <Metric
              value={result.remediated_count ?? 0}
              label="Remediated"
              color="var(--bui-fg-success)"
            />
          )}
          <Metric
            value={result.manual_review}
            label="Manual review"
            color="var(--bui-fg-warning)"
          />
        </Flex>

        {hasAi && (
          <Flex
            gap="4"
            style={{ justifyContent: 'center', margin: '8px 0 16px' }}
          >
            <SmallMetric
              value={result.ai_proposed ?? 0}
              label="AI Proposed"
              color="var(--bui-fg-warning)"
            />
            <SmallMetric
              value={result.ai_accepted ?? 0}
              label="AI Accepted"
              color="var(--bui-fg-success)"
            />
            <SmallMetric
              value={result.ai_declined ?? 0}
              label="AI Declined"
              color="var(--bui-fg-danger)"
            />
          </Flex>
        )}

        {prError && (
          <Alert
            status="danger"
            title={`PR creation failed: ${prError}`}
            style={{ marginBottom: 12 }}
          />
        )}

        <Flex justify="center" gap="2">
          {actions}
          {prUrl && hostShipActions}
          {prUrl && (
            <div>
              <ButtonLink
                variant="secondary"
                href={toPrFilesDiffUrl(prUrl)}
                target="_blank"
                rel="noopener noreferrer"
                iconEnd={<RiExternalLinkLine />}
              >
                View Pull Request
              </ButtonLink>
            </div>
          )}
          {scanId && onViewDetails && (
            <div>
              <Button variant="secondary" onPress={() => onViewDetails(scanId)}>
                View details
              </Button>
            </div>
          )}
          {onDismiss && (
            <div>
              <Button variant="tertiary" onPress={onDismiss}>
                Dismiss
              </Button>
            </div>
          )}
        </Flex>
      </CardBody>
    </Card>
  );
}
