/**
 * Session-tab body for the shared scan/remediate workflow.
 * Host supplies chrome (tabs, Overview); this renders OperationPanel or starting spinner.
 */

import { Card, CardBody, Spinner } from '@patternfly/react-core';
import { OperationPanel } from './components/OperationPanel';
import type { ProjectWorkflowController } from './useProjectWorkflow';

export interface ProjectWorkflowPanelProps {
  workflow: ProjectWorkflowController;
  enableAi: boolean;
  feedbackEnabled: boolean;
  /** Host navigation for "View details" on completed ops. */
  onViewDetails?: (scanId: string) => void;
}

export function ProjectWorkflowPanel({
  workflow,
  enableAi,
  feedbackEnabled,
  onViewDetails,
}: ProjectWorkflowPanelProps) {
  const {
    operationActive,
    opState,
    approve,
    beginRemediate,
    escalateAi,
    patchProposals,
    cancel,
    createPR,
    dismiss,
  } = workflow;

  if (!operationActive || !opState) {
    return (
      <Card>
        <CardBody style={{ textAlign: 'center', padding: '48px 24px' }}>
          <Spinner size="lg" />
          <div style={{ marginTop: 12, fontSize: 16 }}>Starting scan…</div>
        </CardBody>
      </Card>
    );
  }

  return (
    <OperationPanel
      state={opState}
      onApprove={approve}
      onBeginRemediate={beginRemediate}
      onEscalateAi={escalateAi}
      onDraftUpdate={(updates) => {
        patchProposals(updates).catch(() => {});
      }}
      onCancel={cancel}
      onCreatePR={createPR}
      onDismiss={dismiss}
      feedbackEnabled={feedbackEnabled}
      enableAi={enableAi}
      onViewDetails={onViewDetails}
    />
  );
}
