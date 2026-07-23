/**
 * Derive Scan → Remediate workflow stepper position (ADR-064 + ADR-062 Option C).
 *
 * Steps (AI pair omitted when AI is off):
 *   Scan → Review findings → Quick-fix proposals → Quick-fix applied
 *     → AI escalation → AI proposals → AI applied → Commit → Complete
 */

import type { ProjectOperationState } from '../hooks/useProjectOperationState';
import { isAiRemediationProposal } from './proposalTier';

export type WorkflowStepId =
  | 'scan'
  | 'findings'
  | 'tier1_proposals'
  | 'tier1_applied'
  | 'ai_escalation'
  | 'ai_proposals'
  | 'ai_applied'
  | 'commit'
  | 'complete';

export interface WorkflowStepDef {
  id: WorkflowStepId;
  label: string;
}

/** Latched milestones so brief status gaps still advance the tracker. */
export interface WorkflowLatch {
  pastFindings: boolean;
  pastTier1Review: boolean;
  pastTier1Applied: boolean;
  pastAiEscalation: boolean;
  pastAiReview: boolean;
  pastAiApplied: boolean;
}

export function emptyWorkflowLatch(): WorkflowLatch {
  return {
    pastFindings: false,
    pastTier1Review: false,
    pastTier1Applied: false,
    pastAiEscalation: false,
    pastAiReview: false,
    pastAiApplied: false,
  };
}

export function workflowStepDefs(includeAi: boolean): WorkflowStepDef[] {
  const steps: WorkflowStepDef[] = [
    { id: 'scan', label: 'Scan' },
    { id: 'findings', label: 'Review findings' },
    { id: 'tier1_proposals', label: 'Quick-fix proposals' },
    { id: 'tier1_applied', label: 'Quick-fix applied' },
  ];
  if (includeAi) {
    steps.push(
      { id: 'ai_escalation', label: 'AI escalation' },
      { id: 'ai_proposals', label: 'AI proposals' },
      { id: 'ai_applied', label: 'AI applied' },
    );
  }
  steps.push({ id: 'commit', label: 'Commit' }, { id: 'complete', label: 'Complete' });
  return steps;
}

/** Whether the tracker should show AI steps for this operation. */
export function shouldIncludeAiSteps(
  enableAi: boolean,
  state: ProjectOperationState | null,
): boolean {
  if (enableAi) return true;
  if (!state) return false;
  if ((state.result?.ai_proposed ?? 0) > 0) return true;
  if ((state.result?.ai_accepted ?? 0) > 0) return true;
  if ((state.ai_triage_candidates?.length ?? 0) > 0) return true;
  const proposals = state.proposals ?? [];
  return proposals.length > 0 && isAiRemediationProposal(proposals[0]!);
}

/** True when remediation produced patches that still need a commit/PR decision. */
export function needsCommitStep(state: ProjectOperationState): boolean {
  if (state.pr_url) return false;
  const remediated = state.result?.remediated_count ?? 0;
  const patches = state.result?.patches?.length ?? 0;
  return remediated > 0 || patches > 0;
}

function isTerminalSuccess(status: string): boolean {
  return status === 'completed' || status === 'submitting_pr' || status === 'pr_submitted';
}

function isAiGate(state: ProjectOperationState): boolean {
  const proposals = state.proposals ?? [];
  return proposals.length > 0 && isAiRemediationProposal(proposals[0]!);
}

/** Advance latch from the latest operation state. */
export function updateWorkflowLatch(
  latch: WorkflowLatch,
  state: ProjectOperationState,
  includeAi: boolean,
): WorkflowLatch {
  const next = { ...latch };
  const status = state.status;
  const aiGate = isAiGate(state);

  if (status === 'assessed') {
    next.pastFindings = true;
  }

  if (status === 'awaiting_ai_triage') {
    next.pastFindings = true;
    next.pastTier1Review = true;
    next.pastTier1Applied = true;
    next.pastAiEscalation = true;
  }

  if (status === 'awaiting_approval') {
    next.pastFindings = true;
    if (aiGate) {
      next.pastTier1Review = true;
      next.pastTier1Applied = true;
      next.pastAiEscalation = true;
      next.pastAiReview = true;
    } else {
      next.pastTier1Review = true;
    }
  }

  if (status === 'applying') {
    next.pastFindings = true;
    if (next.pastAiReview) {
      // Applying after Gate 2 approve.
      next.pastAiApplied = true;
    } else if (next.pastAiEscalation) {
      // AI running after escalate-ai — stay on AI escalation.
    } else if (next.pastTier1Review || next.pastTier1Applied) {
      next.pastTier1Applied = true;
      // Do not mark pastAiEscalation — wait for awaiting_ai_triage.
    } else {
      // Auto-apply / empty Gate 1: skipped proposal review.
      next.pastTier1Review = true;
      next.pastTier1Applied = true;
    }
  }

  if (isTerminalSuccess(status)) {
    next.pastFindings = true;
    next.pastTier1Review = true;
    next.pastTier1Applied = true;
    if (includeAi) {
      next.pastAiEscalation = true;
      next.pastAiReview = true;
      next.pastAiApplied = true;
    }
  }

  if (status === 'failed' || status === 'expired') {
    // Keep whatever we already latched; do not invent later milestones.
    if (status === 'failed' && !next.pastFindings && state.findings?.length) {
      next.pastFindings = true;
    }
  }

  return next;
}

/** Last meaningful step from latch (for failed / expired). */
function stepFromLatch(latch: WorkflowLatch, includeAi: boolean): WorkflowStepId {
  if (includeAi && latch.pastAiApplied) return 'ai_applied';
  if (includeAi && latch.pastAiReview) return 'ai_proposals';
  if (includeAi && latch.pastAiEscalation) return 'ai_escalation';
  if (latch.pastTier1Applied) return 'tier1_applied';
  if (latch.pastTier1Review) return 'tier1_proposals';
  if (latch.pastFindings) return 'findings';
  return 'scan';
}

export interface WorkflowStepOptions {
  /**
   * User finished or skipped the Commit step (or PR already exists).
   * When false and patches remain, ``completed`` lands on Commit.
   */
  commitFinished?: boolean;
}

/** Resolve which step is current (info / isCurrent). */
export function resolveCurrentWorkflowStep(
  state: ProjectOperationState,
  includeAi: boolean,
  latch: WorkflowLatch,
  options: WorkflowStepOptions = {},
): WorkflowStepId {
  const status = state.status;
  const commitFinished = options.commitFinished === true || Boolean(state.pr_url);

  if (status === 'pr_submitted') {
    return 'complete';
  }

  if (status === 'submitting_pr') {
    return 'commit';
  }

  if (status === 'completed') {
    if (!commitFinished && needsCommitStep(state)) {
      return 'commit';
    }
    return 'complete';
  }

  if (status === 'failed' || status === 'expired' || status === 'cancelled') {
    return stepFromLatch(latch, includeAi);
  }

  if (status === 'assessed') {
    return 'findings';
  }

  if (status === 'awaiting_ai_triage') {
    return 'ai_escalation';
  }

  if (status === 'awaiting_approval') {
    return isAiGate(state) ? 'ai_proposals' : 'tier1_proposals';
  }

  if (status === 'applying') {
    if (latch.pastAiReview) {
      return includeAi ? 'ai_applied' : 'tier1_applied';
    }
    if (includeAi && latch.pastAiEscalation) {
      return 'ai_escalation';
    }
    return 'tier1_applied';
  }

  // queued | cloning | scanning
  if (latch.pastFindings) {
    if (includeAi && latch.pastAiEscalation) {
      return 'ai_escalation';
    }
    if (latch.pastTier1Applied) {
      return 'tier1_applied';
    }
    if (latch.pastTier1Review) {
      return 'tier1_applied';
    }
    return 'tier1_proposals';
  }

  return 'scan';
}

export type StepVisualVariant = 'success' | 'info' | 'pending' | 'danger';

export function stepVisualState(
  stepId: WorkflowStepId,
  currentId: WorkflowStepId,
  defs: WorkflowStepDef[],
  status: string,
): { variant: StepVisualVariant; isCurrent: boolean } {
  const idx = defs.findIndex((d) => d.id === stepId);
  const cur = defs.findIndex((d) => d.id === currentId);
  const failed = status === 'failed' || status === 'expired';

  if (idx < 0 || cur < 0) {
    return { variant: 'pending', isCurrent: false };
  }
  if (idx < cur) {
    return { variant: 'success', isCurrent: false };
  }
  if (idx === cur) {
    if (failed) {
      return { variant: 'danger', isCurrent: true };
    }
    if (currentId === 'complete' && !failed) {
      return { variant: 'success', isCurrent: true };
    }
    return { variant: 'info', isCurrent: true };
  }
  return { variant: 'pending', isCurrent: false };
}
