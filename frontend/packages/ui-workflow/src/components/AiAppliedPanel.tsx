/**
 * Gate 2 completion — user acknowledges AI fixes before Create branch.
 */

import { Button, Card, CardBody } from '@patternfly/react-core';
import { WorkflowNextBar } from './WorkflowNextBar';

export interface AiAppliedPanelProps {
  aiAccepted: number;
  remediatedCount: number;
  onContinue: () => void;
  onCancel?: () => void;
}

export function AiAppliedPanel({
  aiAccepted,
  remediatedCount,
  onContinue,
  onCancel,
}: AiAppliedPanelProps) {
  const appliedLabel =
    aiAccepted > 0
      ? `${aiAccepted} AI fix${aiAccepted !== 1 ? 'es' : ''} applied`
      : 'No AI fixes applied';
  const totalLabel =
    remediatedCount > 0
      ? `${remediatedCount} total change${remediatedCount !== 1 ? 's' : ''} ready to commit`
      : 'Remediation complete';

  return (
    <Card style={{ marginBottom: 16 }}>
      <CardBody>
        <div className="apme-review-step-header" style={{ marginBottom: 8 }}>
          <div className="apme-review-step-header__text">
            <h3 style={{ marginTop: 0 }}>AI fixes applied</h3>
            <span style={{ fontSize: 13, opacity: 0.7 }}>
              {appliedLabel}. {totalLabel}. Next to create a branch and push.
            </span>
          </div>
          <div className="apme-review-step-header__actions">
            <WorkflowNextBar
              placement="header"
              label="Next"
              summary="Continue to create branch and push your remediated changes."
              onNext={onContinue}
              secondary={
                onCancel ? (
                  <Button variant="link" onClick={onCancel}>
                    Cancel
                  </Button>
                ) : undefined
              }
            />
          </div>
        </div>
      </CardBody>
    </Card>
  );
}
