/**
 * Host-facing controller for the scan → pause → choose → remediate workflow.
 * Owns attach/session state and operation actions; presentation is ProjectWorkflowPanel.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  fetchProjectOperationState,
  LIVE_OPERATION_STATUSES,
  useProjectOperationState,
  type ProjectOperationState,
} from './hooks/useProjectOperationState';
import {
  SessionExpiredError,
  useProjectOperationActions,
  WorkingSetConflictError,
} from './hooks/useProjectOperationActions';
import type { AiEscalateTarget } from './components/AiEscalationPanel';

export interface ProjectWorkflowCheckOptions {
  ansibleVersion: string;
  /** Comma-separated collection specs. */
  collections: string;
  enableAi: boolean;
  autoApplyTier1: boolean;
}

export interface UseProjectWorkflowOptions {
  checkOptions: ProjectWorkflowCheckOptions;
  /** Resolve AI model id when enableAi (e.g. localStorage). */
  getAiModel?: () => string | undefined;
  /** Start attached (e.g. ?resume=1). */
  initiallyAttached?: boolean;
  /** Notify host when Session should become the active tab. */
  onOpenSession?: () => void;
  /** Notify host when Session is dismissed back to Overview. */
  onDismissSession?: () => void;
}

export interface ProjectWorkflowController {
  attachOp: boolean;
  setAttachOp: (v: boolean) => void;
  opState: ProjectOperationState | null;
  isRunning: boolean;
  /** True while POST cancel is in flight (op snapshot may clear before dismiss). */
  isCancelling: boolean;
  operationActive: boolean;
  sessionTabVisible: boolean;
  refreshOp: () => void;
  clearOp: () => void;
  startScan: () => Promise<void>;
  beginRemediate: () => Promise<void>;
  escalateAi: (targets: AiEscalateTarget[]) => Promise<void>;
  approve: ReturnType<typeof useProjectOperationActions>['approve'];
  cancel: () => Promise<void>;
  createPR: ReturnType<typeof useProjectOperationActions>['createPR'];
  patchProposals: ReturnType<typeof useProjectOperationActions>['patchProposals'];
  dismiss: () => void;
  resumeSession: () => void;
  startOver: () => Promise<void>;
  /** Latest history scan_id that matches a live op (for Resume UI). */
  findResumableScanId: (
    latestScanId: string | undefined,
  ) => Promise<string | null>;
}

export function useProjectWorkflow(
  projectId: string,
  options: UseProjectWorkflowOptions,
): ProjectWorkflowController {
  const {
    checkOptions,
    getAiModel,
    initiallyAttached = false,
    onOpenSession,
    onDismissSession,
  } = options;
  const { ansibleVersion, collections, enableAi, autoApplyTier1 } = checkOptions;

  const [attachOp, setAttachOp] = useState(initiallyAttached);
  const [isCancelling, setIsCancelling] = useState(false);
  /** Bumped when session dismissed or replaced so stale cancelOp() completions are ignored. */
  const cancelGenerationRef = useRef(0);
  /** operation_id for the attached session — gates server-side auto-dismiss on cancelled. */
  const trackedOpIdRef = useRef<string | null>(null);

  const { state: opState, refresh: refreshOp, clear: clearOp, applyLocalApprovalAck } =
    useProjectOperationState(projectId, {
      enabled: Boolean(projectId) && attachOp,
    });

  // Resume (?resume=1 / initiallyAttached): if Gateway has no live op (404),
  // detach so hosts do not keep a permanent "Starting scan…" Session tab.
  // Probe failures must not detach — a transient Gateway error can hide a
  // real in-flight operation (Dismiss remains available on the loading panel).
  useEffect(() => {
    if (!initiallyAttached || !projectId) return;
    let cancelled = false;
    fetchProjectOperationState(projectId)
      .then((op) => {
        if (cancelled || op) return;
        setAttachOp(false);
        onDismissSession?.();
      })
      .catch(() => {
        /* keep attached on probe failure */
      });
    return () => {
      cancelled = true;
    };
  }, [initiallyAttached, projectId, onDismissSession]);

  const {
    start: startOp,
    approve,
    beginRemediate: beginRemediateOp,
    cancel: cancelOp,
    createPR,
    patchProposals,
    escalateAi: escalateAiOp,
  } = useProjectOperationActions(projectId);

  const isRunning =
    isCancelling ||
    (opState != null && LIVE_OPERATION_STATUSES.has(opState.status));
  const operationActive =
    attachOp && opState != null && opState.status !== 'cancelled';
  const sessionTabVisible = attachOp;

  useEffect(() => {
    if (
      attachOp &&
      opState?.status === 'awaiting_ai_triage' &&
      opState.ai_triage_candidates === undefined
    ) {
      refreshOp();
    }
  }, [attachOp, opState?.status, opState?.ai_triage_candidates, refreshOp]);

  // Gate 2: proposals SSE can be missed while status stays applying — poll GET.
  useEffect(() => {
    if (!attachOp || !enableAi || opState?.status !== 'applying') {
      return;
    }
    if (opState.ai_triage_candidates === undefined) {
      return;
    }
    const timer = setInterval(() => {
      refreshOp();
    }, 3000);
    return () => clearInterval(timer);
  }, [
    attachOp,
    enableAi,
    opState?.status,
    opState?.ai_triage_candidates,
    refreshOp,
  ]);

  const approveWithState = useCallback(
    async (approvedIds: string[]) => {
      const result = await approve(approvedIds);
      applyLocalApprovalAck();
      return result;
    },
    [approve, applyLocalApprovalAck],
  );

  const invalidatePendingCancel = useCallback(() => {
    cancelGenerationRef.current += 1;
    setIsCancelling(false);
  }, []);

  const openSession = useCallback(() => {
    setAttachOp(true);
    onOpenSession?.();
  }, [onOpenSession]);

  const buildColls = useCallback(
    () =>
      collections
        .split(',')
        .map((c) => c.trim())
        .filter(Boolean),
    [collections],
  );

  const startScan = useCallback(async () => {
    invalidatePendingCancel();
    clearOp();
    const colls = buildColls();
    openSession();

    const startOnce = (abandonWorkingSet: boolean) =>
      startOp('check', {
        ansible_version: ansibleVersion || undefined,
        collection_specs: colls.length ? colls : undefined,
        enable_ai: enableAi,
        ai_model: enableAi ? getAiModel?.() : undefined,
        assess_pause: true,
        interactive: !autoApplyTier1,
        ...(abandonWorkingSet ? { abandon_working_set: true } : {}),
      });

    try {
      await startOnce(false);
      refreshOp();
    } catch (err) {
      if (err instanceof WorkingSetConflictError) {
        const ok = window.confirm(
          `${err.message}\n\nDiscard the draft working set and start a new scan?`,
        );
        if (ok) {
          try {
            await startOnce(true);
            refreshOp();
          } catch (retryErr) {
            console.error('Failed to start operation after abandon:', retryErr);
            window.alert(
              retryErr instanceof Error
                ? retryErr.message
                : 'Failed to start scan after discarding draft.',
            );
          }
        } else {
          refreshOp();
        }
        return;
      }
      console.error('Failed to start operation:', err);
      refreshOp();
      const msg = err instanceof Error ? err.message : 'Failed to start scan.';
      if (!/already has an active operation/i.test(msg)) {
        window.alert(msg);
      }
    }
  }, [
    ansibleVersion,
    autoApplyTier1,
    buildColls,
    enableAi,
    getAiModel,
    clearOp,
    invalidatePendingCancel,
    openSession,
    refreshOp,
    startOp,
  ]);

  const beginRemediate = useCallback(async () => {
    try {
      openSession();
      await beginRemediateOp();
      refreshOp();
    } catch (err) {
      if (err instanceof SessionExpiredError) {
        const ok = window.confirm(
          'Assessment session expired. Start a full remediate (rescan)?',
        );
        if (ok) {
          const colls = buildColls();
          openSession();
          await startOp('remediate', {
            ansible_version: ansibleVersion || undefined,
            collection_specs: colls.length ? colls : undefined,
            enable_ai: enableAi,
            ai_model: enableAi ? getAiModel?.() : undefined,
            interactive: !autoApplyTier1,
            abandon_working_set: true,
          });
          refreshOp();
        }
        return;
      }
      throw err;
    }
  }, [
    ansibleVersion,
    autoApplyTier1,
    beginRemediateOp,
    buildColls,
    enableAi,
    getAiModel,
    openSession,
    refreshOp,
    startOp,
  ]);

  const escalateAi = useCallback(
    async (targets: AiEscalateTarget[]) => {
      openSession();
      await escalateAiOp(targets);
      refreshOp();
    },
    [escalateAiOp, openSession, refreshOp],
  );

  const dismiss = useCallback(() => {
    invalidatePendingCancel();
    trackedOpIdRef.current = null;
    setAttachOp(false);
    clearOp();
    onDismissSession?.();
  }, [clearOp, invalidatePendingCancel, onDismissSession]);

  useEffect(() => {
    if (attachOp && opState?.operation_id) {
      trackedOpIdRef.current = opState.operation_id;
    }
  }, [attachOp, opState?.operation_id]);

  // Server-side cancel — detach without blank panel; ignore stale cancelled snapshots.
  useEffect(() => {
    if (
      attachOp &&
      opState?.status === 'cancelled' &&
      !isCancelling &&
      opState.operation_id === trackedOpIdRef.current
    ) {
      dismiss();
    }
  }, [
    attachOp,
    opState?.status,
    opState?.operation_id,
    isCancelling,
    dismiss,
  ]);

  const resumeSession = useCallback(() => {
    openSession();
  }, [openSession]);

  const startOver = useCallback(async () => {
    const ok = window.confirm(
      'Discard the current interactive session and start a new scan?',
    );
    if (!ok) return;

    const colls = buildColls();
    invalidatePendingCancel();
    clearOp();
    setAttachOp(true);
    onOpenSession?.();
    try {
      await startOp('check', {
        ansible_version: ansibleVersion || undefined,
        collection_specs: colls.length ? colls : undefined,
        enable_ai: enableAi,
        ai_model: enableAi ? getAiModel?.() : undefined,
        assess_pause: true,
        interactive: true,
        abandon_working_set: true,
      });
      refreshOp();
    } catch (err) {
      console.error('Failed to start over:', err);
      window.alert('Could not start over. Try Scan again from Options.');
    }
  }, [
    ansibleVersion,
    buildColls,
    clearOp,
    enableAi,
    getAiModel,
    invalidatePendingCancel,
    onOpenSession,
    refreshOp,
    startOp,
  ]);

  const findResumableScanId = useCallback(
    async (latestScanId: string | undefined): Promise<string | null> => {
      if (!projectId || !latestScanId) return null;
      if (attachOp && opState && LIVE_OPERATION_STATUSES.has(opState.status)) {
        return opState.scan_id === latestScanId ? latestScanId : null;
      }
      try {
        const op = await fetchProjectOperationState(projectId);
        if (
          op &&
          op.scan_id === latestScanId &&
          LIVE_OPERATION_STATUSES.has(op.status)
        ) {
          return latestScanId;
        }
      } catch {
        /* probe failed — cannot confirm resumable */
      }
      return null;
    },
    [attachOp, opState, projectId],
  );

  const cancel = useCallback(async () => {
    if (isCancelling) return;
    const generation = cancelGenerationRef.current;
    setIsCancelling(true);
    try {
      await cancelOp();
      if (generation !== cancelGenerationRef.current) return;
      dismiss();
    } catch (err) {
      if (generation !== cancelGenerationRef.current) return;
      console.error('Failed to cancel operation:', err);
      setIsCancelling(false);
      window.alert(
        err instanceof Error ? err.message : 'Failed to cancel operation.',
      );
    }
  }, [cancelOp, dismiss, isCancelling]);

  return {
    attachOp,
    setAttachOp,
    opState,
    isRunning,
    isCancelling,
    operationActive,
    sessionTabVisible,
    refreshOp,
    clearOp,
    startScan,
    beginRemediate,
    escalateAi,
    approve: approveWithState,
    cancel,
    createPR,
    patchProposals,
    dismiss,
    resumeSession,
    startOver,
    findResumableScanId,
  };
}
