/**
 * OperationPanel — pure state-to-screen renderer for project operations (ADR-052).
 *
 * Renders the correct UI panel based on the current OperationState.status.
 * All 11 states map to a specific UI:
 *
 *   queued          → spinner + "Starting..."
 *   cloning         → progress bar + clone status
 *   scanning        → streaming progress log
 *   assessed        → findings panel + Remediate (ADR-064)
 *   awaiting_ai_triage → AI escalation Include/Skip panel
 *   awaiting_approval → proposal review panel
 *   applying        → progress log + "Applying fixes..."
 *   completed       → Commit step (if patches) or results
 *   submitting_pr   → Commit step (in progress)
 *   pr_submitted    → results + PR link
 *   failed          → error banner
 *   expired         → "Session expired" banner
 *   cancelled       → null (no panel)
 */

import { useCallback, useEffect, useState, type ReactNode } from 'react';
import {
  Button,
  Card,
  CardBody,
  Flex,
  FlexItem,
  Spinner,
} from '@patternfly/react-core';
import type {
  ProjectOperationState,
  ProjectOperationStatus,
} from '../hooks/useProjectOperationState';
import type { OperationProgress, OperationResult } from '../types/operation';
import { needsCommitStep } from '../remediation';
import { OperationProgressPanel } from './OperationProgressPanel';
import {
  ProposalReviewPanel,
  type ProposalDraftUpdate,
} from './ProposalReviewPanel';
import { OperationResultCard } from './OperationResultCard';
import { AssessFindingsPanel } from './AssessFindingsPanel';
import {
  AiEscalationPanel,
  type AiEscalateTarget,
} from './AiEscalationPanel';
import { OperationWorkflowStepper } from './OperationWorkflowStepper';
import {
  CommitChangesPanel,
  type CommitSubmitOptions,
  type CommitSubmitResult,
} from './CommitChangesPanel';

export interface OperationPanelProps {
  state: ProjectOperationState | null;
  onApprove: (ids: string[]) => Promise<unknown>;
  onBeginRemediate?: () => Promise<unknown>;
  /** ADR-062: leave awaiting_ai_triage (empty targets skips AI). */
  onEscalateAi?: (targets: AiEscalateTarget[]) => Promise<unknown>;
  onDraftUpdate?: (updates: ProposalDraftUpdate[]) => void;
  onCancel: () => Promise<unknown>;
  onCreatePR: (options?: CommitSubmitOptions) => Promise<CommitSubmitResult>;
  onDismiss: () => void;
  feedbackEnabled?: boolean;
  /** Activity "Enable AI" — drives AI steps on the workflow tracker. */
  enableAi?: boolean;
  /** Host navigation for "View details" (no react-router in workflow). */
  onViewDetails?: (scanId: string) => void;
}

const RUNNING_STATUSES = new Set<ProjectOperationStatus>([
  'queued',
  'cloning',
  'scanning',
  'applying',
]);

function mapStatus(s: ProjectOperationStatus): import('../types/operation').OperationStatus {
  const mapping: Partial<Record<ProjectOperationStatus, string>> = {
    queued: 'connecting',
    cloning: 'cloning',
    scanning: 'checking',
    applying: 'applying',
  };
  return (mapping[s] ?? s) as import('../types/operation').OperationStatus;
}

function withStepper(
  state: ProjectOperationState,
  enableAi: boolean,
  body: ReactNode,
  commitFinished = false,
): ReactNode {
  return (
    <div>
      <OperationWorkflowStepper
        state={state}
        enableAi={enableAi}
        commitFinished={commitFinished}
      />
      {body}
    </div>
  );
}

export function OperationPanel({
  state,
  onApprove,
  onBeginRemediate,
  onEscalateAi,
  onDraftUpdate,
  onCancel,
  onCreatePR,
  onDismiss,
  feedbackEnabled,
  enableAi = false,
  onViewDetails,
}: OperationPanelProps) {
  const [prCreating, setPrCreating] = useState(false);
  const [prError, setPrError] = useState<string | null>(null);
  const [beginning, setBeginning] = useState(false);
  const [beginError, setBeginError] = useState<string | null>(null);
  const [escalating, setEscalating] = useState(false);
  const [escalateError, setEscalateError] = useState<string | null>(null);
  const [commitFinished, setCommitFinished] = useState(false);
  const [localPrUrl, setLocalPrUrl] = useState<string | null>(null);

  useEffect(() => {
    setCommitFinished(false);
    setPrError(null);
    setBeginError(null);
    setEscalateError(null);
    setLocalPrUrl(null);
  }, [state?.operation_id]);

  useEffect(() => {
    if (state?.pr_url) {
      setCommitFinished(true);
      setLocalPrUrl(state.pr_url);
    }
  }, [state?.pr_url]);

  const handleCancel = useCallback(() => {
    onCancel().catch(() => {});
  }, [onCancel]);

  const handleApprove = useCallback(
    (ids: string[]) => {
      onApprove(ids).catch(() => {});
    },
    [onApprove],
  );

  const handleBeginRemediate = useCallback(async () => {
    if (!onBeginRemediate) return;
    setBeginning(true);
    setBeginError(null);
    try {
      await onBeginRemediate();
    } catch (err) {
      const message =
        err instanceof Error ? err.message : 'Failed to begin remediation';
      setBeginError(message);
      console.error('begin-remediate failed:', err);
    } finally {
      setBeginning(false);
    }
  }, [onBeginRemediate]);

  const handleEscalateAi = useCallback(
    async (targets: AiEscalateTarget[]) => {
      if (!onEscalateAi) return;
      setEscalating(true);
      setEscalateError(null);
      try {
        await onEscalateAi(targets);
      } catch (err) {
        const message =
          err instanceof Error ? err.message : 'Failed to escalate to AI';
        setEscalateError(message);
        console.error('escalate-ai failed:', err);
      } finally {
        setEscalating(false);
      }
    },
    [onEscalateAi],
  );

  const handleSubmit = useCallback(
    async (options?: CommitSubmitOptions) => {
      setPrCreating(true);
      setPrError(null);
      try {
        const result = await onCreatePR(options);
        if (result.pr_url) {
          setLocalPrUrl(result.pr_url);
          setCommitFinished(true);
        }
        return result;
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Failed to submit changes';
        setPrError(message);
        throw err instanceof Error ? err : new Error(message);
      } finally {
        setPrCreating(false);
      }
    },
    [onCreatePR],
  );

  if (!state || state.status === 'cancelled') {
    return null;
  }

  const status = state.status;

  if (RUNNING_STATUSES.has(status)) {
    if (status === 'queued') {
      return withStepper(
        state,
        enableAi,
        <Card style={{ marginBottom: 16 }}>
          <CardBody style={{ textAlign: 'center', padding: '32px 24px' }}>
            <Spinner size="lg" />
            <div style={{ marginTop: 12, fontSize: 16 }}>Starting operation...</div>
            <Button variant="link" onClick={handleCancel} style={{ marginTop: 8 }}>
              Cancel
            </Button>
          </CardBody>
        </Card>,
      );
    }

    const progressEntries: OperationProgress[] = state.progress.map((p) => ({
      phase: p.phase,
      message: p.message,
      timestamp: new Date(p.timestamp).getTime(),
      progress: p.progress ?? undefined,
      level: p.level ?? undefined,
    }));

    return withStepper(
      state,
      enableAi,
      <OperationProgressPanel
        status={mapStatus(status)}
        progress={progressEntries}
        onCancel={handleCancel}
      />,
    );
  }

  if (status === 'assessed') {
    return withStepper(
      state,
      enableAi,
      <AssessFindingsPanel
        findings={state.findings ?? []}
        enableAi={enableAi}
        error={beginError}
        onRemediate={() => {
          handleBeginRemediate().catch(() => {});
        }}
        onCancel={handleCancel}
        remediating={beginning}
      />,
    );
  }

  if (status === 'awaiting_ai_triage') {
    const candidatesRaw = state.ai_triage_candidates;
    const candidatesLoading = candidatesRaw === undefined;
    const candidates = candidatesRaw ?? [];
    return withStepper(
      state,
      enableAi,
      candidatesLoading ? (
        <Card style={{ marginBottom: 16 }}>
          <CardBody style={{ textAlign: 'center', padding: '32px 24px' }}>
            <Spinner size="lg" />
            <div style={{ marginTop: 12, fontSize: 16 }}>
              Loading AI-eligible findings…
            </div>
            <Button variant="link" onClick={handleCancel} style={{ marginTop: 8 }}>
              Cancel
            </Button>
          </CardBody>
        </Card>
      ) : (
        <AiEscalationPanel
          candidates={candidates}
          onEscalate={(targets) => {
            handleEscalateAi(targets).catch(() => {});
          }}
          onCancel={handleCancel}
          escalating={escalating}
          error={escalateError}
        />
      ),
    );
  }

  if (status === 'awaiting_approval') {
    // status_changed can arrive before the proposals payload.
    if (!state.proposals) {
      return withStepper(
        state,
        enableAi,
        <Card style={{ marginBottom: 16 }}>
          <CardBody style={{ textAlign: 'center', padding: '32px 24px' }}>
            <Spinner size="lg" />
            <div style={{ marginTop: 12, fontSize: 16 }}>Loading proposals...</div>
            <Button variant="link" onClick={handleCancel} style={{ marginTop: 8 }}>
              Cancel
            </Button>
          </CardBody>
        </Card>,
      );
    }
    const proposals = state.proposals.map((p) => ({
      id: p.id,
      rule_id: p.rule_id,
      file: p.file,
      tier: p.tier,
      confidence: p.confidence,
      explanation: p.explanation,
      diff_hunk: p.diff_hunk,
      status: p.status,
      suggestion: p.suggestion,
      line_start: p.line_start,
      path: p.path,
      node_type: p.node_type,
      source: p.source,
      before_text: p.before_text,
      after_text: p.after_text,
    }));
    return withStepper(
      state,
      enableAi,
      <ProposalReviewPanel
        proposals={proposals}
        onApprove={handleApprove}
        onDraftUpdate={onDraftUpdate}
        feedbackEnabled={feedbackEnabled ?? false}
        scanId={state.scan_id}
        onCancel={handleCancel}
      />,
    );
  }

  if (status === 'completed' || status === 'submitting_pr' || status === 'pr_submitted') {
    const resultData: OperationResult | null = state.result
      ? {
          total_violations: state.result.total_violations,
          fixable: state.result.fixable,
          ai_candidate: state.result.ai_proposed,
          ai_proposed: state.result.ai_proposed,
          ai_declined: state.result.ai_declined,
          ai_accepted: state.result.ai_accepted,
          manual_review: state.result.manual_review,
          remediated_count: state.result.remediated_count,
        }
      : null;

    const showCommit =
      (status === 'completed' || status === 'submitting_pr') &&
      !commitFinished &&
      needsCommitStep(state);

    if (showCommit && resultData) {
      return withStepper(
        state,
        enableAi,
        <CommitChangesPanel
          remediatedCount={resultData.remediated_count ?? 0}
          scanId={state.scan_id}
          onSubmit={handleSubmit}
          onFinish={() => setCommitFinished(true)}
          onDismiss={onDismiss}
          submitting={prCreating || status === 'submitting_pr'}
          error={prError}
          prUrl={state.pr_url || localPrUrl}
        />,
        false,
      );
    }

    if (!resultData) {
      return withStepper(
        state,
        enableAi,
        <Card style={{ marginBottom: 16, borderLeft: '4px solid var(--pf-t--global--color--status--success--default)' }}>
          <CardBody style={{ textAlign: 'center', padding: 32 }}>
            <div style={{ fontSize: 48, color: 'var(--pf-t--global--color--status--success--default)' }}>&#10003;</div>
            <h2>Operation Complete</h2>
            <Button variant="link" onClick={onDismiss} style={{ marginTop: 8 }}>
              Dismiss
            </Button>
          </CardBody>
        </Card>,
        true,
      );
    }

    const canStillCommit =
      needsCommitStep(state) && !(state.pr_url || localPrUrl);

    return withStepper(
      state,
      enableAi,
      <OperationResultCard
        result={resultData}
        isRemediate={state.scan_type === 'remediate' || (resultData.remediated_count ?? 0) > 0}
        onDismiss={onDismiss}
        onCreatePR={
          canStillCommit
            ? () => {
                handleSubmit({ create_pr: true }).catch(() => {});
              }
            : undefined
        }
        prCreating={prCreating || status === 'submitting_pr'}
        prUrl={state.pr_url || localPrUrl}
        prError={prError}
        scanId={state.scan_id}
        onViewDetails={onViewDetails}
      />,
      true,
    );
  }

  if (status === 'failed') {
    return withStepper(
      state,
      enableAi,
      <Card style={{ marginBottom: 16, borderLeft: '4px solid var(--pf-t--global--color--status--danger--default)' }}>
        <CardBody>
          <h3 style={{ color: 'var(--pf-t--global--color--status--danger--default)' }}>
            Operation Failed
          </h3>
          <p>{state.error || 'An unknown error occurred.'}</p>
          <Flex gap={{ default: 'gapSm' }} style={{ marginTop: 8 }}>
            <FlexItem>
              <Button variant="link" onClick={onDismiss}>Dismiss</Button>
            </FlexItem>
          </Flex>
        </CardBody>
      </Card>,
    );
  }

  if (status === 'expired') {
    return withStepper(
      state,
      enableAi,
      <Card style={{ marginBottom: 16, borderLeft: '4px solid var(--pf-t--global--color--status--warning--default)' }}>
        <CardBody>
          <h3>Session Expired</h3>
          <p>This operation session has expired. Please start a new one.</p>
          <Button variant="link" onClick={onDismiss}>Dismiss</Button>
        </CardBody>
      </Card>,
    );
  }

  // Unknown / future statuses must not blank the Session tab.
  return withStepper(
    state,
    enableAi,
    <Card style={{ marginBottom: 16 }}>
      <CardBody>
        <h3 style={{ marginTop: 0 }}>Session in progress</h3>
        <p style={{ opacity: 0.8 }}>
          Status: <code>{status}</code>
        </p>
        <Flex gap={{ default: 'gapSm' }} style={{ marginTop: 8 }}>
          <FlexItem>
            <Button variant="link" onClick={handleCancel}>Cancel</Button>
          </FlexItem>
          <FlexItem>
            <Button variant="link" onClick={onDismiss}>Dismiss</Button>
          </FlexItem>
        </Flex>
      </CardBody>
    </Card>,
  );
}
