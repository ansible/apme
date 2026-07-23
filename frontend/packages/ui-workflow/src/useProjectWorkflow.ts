/**
 * Host-facing controller for the scan → pause → choose → remediate workflow.
 * Owns attach/session state and operation actions; presentation is ProjectWorkflowPanel.
 */

import { useCallback, useEffect, useState } from 'react';
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

  const { state: opState, refresh: refreshOp, clear: clearOp } =
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
    opState != null && LIVE_OPERATION_STATUSES.has(opState.status);
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
    clearOp();
    setAttachOp(false);
    onDismissSession?.();
  }, [clearOp, onDismissSession]);

  const resumeSession = useCallback(() => {
    openSession();
  }, [openSession]);

  const startOver = useCallback(async () => {
    const ok = window.confirm(
      'Discard the current interactive session and start a new scan?',
    );
    if (!ok) return;

    const colls = buildColls();
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
    enableAi,
    getAiModel,
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
    await cancelOp();
  }, [cancelOp]);

  return {
    attachOp,
    setAttachOp,
    opState,
    isRunning,
    operationActive,
    sessionTabVisible,
    refreshOp,
    clearOp,
    startScan,
    beginRemediate,
    escalateAi,
    approve,
    cancel,
    createPR,
    patchProposals,
    dismiss,
    resumeSession,
    startOver,
    findResumableScanId,
  };
}
