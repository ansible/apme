/**
 * Scan → Remediate workflow tracker — portal-style circles + always-visible labels.
 *
 * Visual pattern aligned with ansible-backstage-plugins QualityWorkflowStepper:
 * numbered/check badges, label beside each step, → connectors.
 */

import { useRef, type CSSProperties, type ReactNode } from 'react';
import { Card, CardBody, Spinner } from '@patternfly/react-core';
import { CheckIcon } from '@patternfly/react-icons';
import type { ProjectOperationState } from '../hooks/useProjectOperationState';
import {
  emptyWorkflowLatch,
  resolveCurrentWorkflowStep,
  shouldIncludeAiSteps,
  updateWorkflowLatch,
  type WorkflowLatch,
  type WorkflowStepId,
  workflowStepDefs,
} from '../remediation/workflowSteps';

export interface OperationWorkflowStepperProps {
  state: ProjectOperationState;
  /** User opted into AI for this run (Activity options). */
  enableAi?: boolean;
  /** Commit step finished / skipped (or PR already exists). */
  commitFinished?: boolean;
}

const SPINNING_STEPS = new Set<WorkflowStepId>([
  'scan',
  'tier1_applied',
  'ai_escalation',
  'ai_applied',
  'commit',
]);

const styles = {
  root: {
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    flexWrap: 'wrap' as const,
  },
  step: {
    display: 'flex',
    alignItems: 'center',
    gap: 8,
  },
  badge: {
    width: 28,
    height: 28,
    borderRadius: '50%',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    fontSize: 12,
    fontWeight: 700,
    flexShrink: 0,
    transition: 'all 0.2s',
  },
  label: {
    fontSize: 13,
    fontWeight: 500,
    whiteSpace: 'nowrap' as const,
  },
  arrow: {
    color: 'var(--pf-t--global--text--color--disabled)',
    fontSize: 18,
    margin: '0 4px',
    userSelect: 'none' as const,
  },
} satisfies Record<string, CSSProperties>;

function badgeStyle(
  isComplete: boolean,
  isActive: boolean,
  isDanger: boolean,
): CSSProperties {
  if (isDanger && isActive) {
    return {
      ...styles.badge,
      backgroundColor: 'var(--pf-t--global--color--status--danger--default)',
      color: 'var(--pf-t--global--text--color--on-status--danger--default, #fff)',
      border: '2px solid var(--pf-t--global--color--status--danger--default)',
    };
  }
  if (isComplete) {
    return {
      ...styles.badge,
      backgroundColor: 'var(--pf-t--global--color--status--success--default)',
      color: 'var(--pf-t--global--text--color--on-status--success--default, #fff)',
      border: '2px solid var(--pf-t--global--color--status--success--default)',
    };
  }
  if (isActive) {
    return {
      ...styles.badge,
      backgroundColor: 'var(--pf-t--global--color--brand--default)',
      color: 'var(--pf-t--global--text--color--on-brand--default, #fff)',
      border: '2px solid var(--pf-t--global--color--brand--default)',
    };
  }
  return {
    ...styles.badge,
    backgroundColor: 'var(--pf-t--global--background--color--secondary--default)',
    color: 'var(--pf-t--global--text--color--secondary)',
    border: '2px solid var(--pf-t--global--border--color--default)',
  };
}

function labelStyle(isActive: boolean, isPending: boolean): CSSProperties {
  if (isActive) {
    return {
      ...styles.label,
      color: 'var(--pf-t--global--text--color--regular)',
      fontWeight: 600,
    };
  }
  if (isPending) {
    return {
      ...styles.label,
      color: 'var(--pf-t--global--text--color--secondary)',
    };
  }
  return {
    ...styles.label,
    color: 'var(--pf-t--global--text--color--regular)',
  };
}

function badgeContent(
  isComplete: boolean,
  isSpinning: boolean,
  index: number,
): ReactNode {
  if (isComplete) {
    return <CheckIcon style={{ width: 14, height: 14 }} />;
  }
  if (isSpinning) {
    return <Spinner size="sm" aria-label="In progress" />;
  }
  return index + 1;
}

export function OperationWorkflowStepper({
  state,
  enableAi = false,
  commitFinished = false,
}: OperationWorkflowStepperProps) {
  const includeAi = shouldIncludeAiSteps(enableAi, state);
  const defs = workflowStepDefs(includeAi);
  const latchRef = useRef<WorkflowLatch>(emptyWorkflowLatch());
  const opIdRef = useRef(state.operation_id);

  if (opIdRef.current !== state.operation_id) {
    opIdRef.current = state.operation_id;
    latchRef.current = emptyWorkflowLatch();
  }
  latchRef.current = updateWorkflowLatch(latchRef.current, state, includeAi);
  const currentId = resolveCurrentWorkflowStep(state, includeAi, latchRef.current, {
    commitFinished,
  });
  const activeIndex = defs.findIndex((d) => d.id === currentId);
  const failed = state.status === 'failed' || state.status === 'expired';

  return (
    <Card style={{ marginBottom: 16 }}>
      <CardBody style={{ paddingTop: 16, paddingBottom: 12 }}>
        <div
          style={styles.root}
          role="navigation"
          aria-label="Remediation workflow progress"
        >
          {defs.map((step, index) => {
            const isComplete =
              index < activeIndex ||
              (currentId === 'complete' && index === activeIndex && !failed);
            const isActive = index === activeIndex && !isComplete;
            const isPending = index > activeIndex;
            const isSpinning =
              isActive &&
              SPINNING_STEPS.has(step.id) &&
              (state.status === 'queued' ||
                state.status === 'cloning' ||
                state.status === 'scanning' ||
                state.status === 'applying' ||
                state.status === 'submitting_pr');

            return (
              <span key={step.id} style={styles.step}>
                {index > 0 && (
                  <span style={styles.arrow} aria-hidden>
                    →
                  </span>
                )}
                <span
                  style={badgeStyle(isComplete, isActive, failed && isActive)}
                  aria-label={`${step.label}${isComplete ? ', completed' : isActive ? ', current' : ', pending'}`}
                >
                  {badgeContent(isComplete, isSpinning, index)}
                </span>
                <span style={labelStyle(isActive, isPending)}>{step.label}</span>
              </span>
            );
          })}
        </div>
      </CardBody>
    </Card>
  );
}
