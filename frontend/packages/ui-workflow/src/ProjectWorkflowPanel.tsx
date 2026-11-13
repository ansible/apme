/**
 * Session-tab body for the shared scan/remediate workflow.
 * Host supplies chrome (tabs, Overview); this renders OperationPanel or session loading states.
 */

import { useEffect, useRef, useState, type ReactNode } from 'react';
import { Alert, Button, Card, CardBody } from '@backstage/ui';
import { LoadingIndicator } from './components/WorkflowProgress';
import { OperationPanel } from './components/OperationPanel';
import type { ProjectWorkflowController } from './useProjectWorkflow';

export interface ProjectWorkflowPanelProps {
  workflow: ProjectWorkflowController;
  enableAi: boolean;
  feedbackEnabled: boolean;
  /** Host navigation for "View details" on completed ops. */
  onViewDetails?: (scanId: string) => void;
  /** Host controls shown after a successful push, or beside a completed PR. */
  hostShipActions?: ReactNode;
}

export function ProjectWorkflowPanel({
  workflow,
  enableAi,
  feedbackEnabled,
  onViewDetails,
  hostShipActions,
}: ProjectWorkflowPanelProps) {
  const {
    opState,
    isCancelling,
    approve,
    beginRemediate,
    escalateAi,
    patchProposals,
    cancel,
    createPR,
    dismiss,
  } = workflow;
  const [draftError, setDraftError] = useState<string | null>(null);
  /** Ignore late patchProposals rejections after the operation changes. */
  const draftGenRef = useRef(0);
  const operationId = opState?.operation_id;
  const opStatus = opState?.status;

  useEffect(() => {
    draftGenRef.current += 1;
    setDraftError(null);
  }, [operationId, opStatus]);

  // Cancel clears the op snapshot before dismiss — show stopping, not starting.
  if (isCancelling) {
    return (
      <Card>
        <CardBody style={{ textAlign: 'center', padding: '48px 24px' }}>
          <LoadingIndicator />
          <div style={{ marginTop: 12, fontSize: 16 }}>Stopping session…</div>
          <Button
            variant="tertiary"
            onPress={dismiss}
            style={{ marginTop: 16 }}
          >
            Dismiss
          </Button>
        </CardBody>
      </Card>
    );
  }

  // Only show the pre-op spinner while waiting for the first snapshot.
  if (!opState) {
    return (
      <Card>
        <CardBody style={{ textAlign: 'center', padding: '48px 24px' }}>
          <LoadingIndicator />
          <div style={{ marginTop: 12, fontSize: 16 }}>Starting scan…</div>
          <Button
            variant="tertiary"
            onPress={dismiss}
            style={{ marginTop: 16 }}
          >
            Dismiss
          </Button>
        </CardBody>
      </Card>
    );
  }

  return (
    <>
      {draftError ? (
        <Alert
          status="danger"
          title="Could not save draft proposals"
          description={draftError}
          style={{ marginBottom: 12 }}
          customActions={
            <Button variant="tertiary" onPress={() => setDraftError(null)}>
              Dismiss error
            </Button>
          }
        />
      ) : null}
      <OperationPanel
        state={opState}
        onApprove={approve}
        onBeginRemediate={beginRemediate}
        onEscalateAi={escalateAi}
        onDraftUpdate={(updates) => {
          const gen = draftGenRef.current;
          setDraftError(null);
          patchProposals(updates).catch((err: unknown) => {
            if (gen !== draftGenRef.current) return;
            console.error('Failed to patch proposals:', err);
            setDraftError(
              err instanceof Error ? err.message : 'Draft update failed.',
            );
          });
        }}
        onCancel={cancel}
        onCreatePR={createPR}
        onDismiss={dismiss}
        feedbackEnabled={feedbackEnabled}
        enableAi={enableAi}
        hostShipActions={hostShipActions}
        onViewDetails={onViewDetails}
      />
    </>
  );
}
