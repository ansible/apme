/**
 * Scan → Remediate workflow tracker — portal-style circles + always-visible labels.
 *
 * Visual pattern aligned with ansible-backstage-plugins QualityWorkflowStepper:
 * numbered/check badges, label beside each step, → connectors.
 */

import { useRef, type CSSProperties, type ReactNode } from 'react';
import { Card, CardBody } from '@backstage/ui';
import { RiCheckLine } from '@remixicon/react';
import { LoadingIndicator } from './WorkflowProgress';
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
  /** Gate 2 apply step acknowledged — advance to Create branch. */
  aiApplyFinished?: boolean;
  /** Commit step finished / skipped (or PR already exists). */
  commitFinished?: boolean;
}

const SPINNING_STEPS = new Set<WorkflowStepId>([
  'scan',
  'apply_findings',
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
    color: 'var(--bui-fg-disabled)',
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
      backgroundColor: 'var(--bui-bg-danger)',
      color: 'var(--bui-fg-danger-on-bg)',
      border: '2px solid var(--bui-bg-danger)',
    };
  }
  if (isComplete) {
    return {
      ...styles.badge,
      backgroundColor: 'var(--bui-bg-success)',
      color: 'var(--bui-fg-success-on-bg)',
      border: '2px solid var(--bui-bg-success)',
    };
  }
  if (isActive) {
    return {
      ...styles.badge,
      backgroundColor: 'var(--bui-bg-solid)',
      color: 'var(--bui-fg-solid)',
      border: '2px solid var(--bui-bg-solid)',
    };
  }
  return {
    ...styles.badge,
    backgroundColor: 'var(--bui-bg-neutral-2)',
    color: 'var(--bui-fg-secondary)',
    border: '2px solid var(--bui-border-1)',
  };
}

function labelStyle(isActive: boolean, isPending: boolean): CSSProperties {
  if (isActive) {
    return {
      ...styles.label,
      color: 'var(--bui-fg-primary)',
      fontWeight: 600,
    };
  }
  if (isPending) {
    return {
      ...styles.label,
      color: 'var(--bui-fg-secondary)',
    };
  }
  return {
    ...styles.label,
    color: 'var(--bui-fg-primary)',
  };
}

function badgeContent(
  isComplete: boolean,
  isSpinning: boolean,
  index: number,
): ReactNode {
  if (isComplete) {
    return <RiCheckLine aria-hidden style={{ width: 14, height: 14 }} />;
  }
  if (isSpinning) {
    return <LoadingIndicator size="small" label="In progress" />;
  }
  return index + 1;
}

export function OperationWorkflowStepper({
  state,
  enableAi = false,
  aiApplyFinished = false,
  commitFinished = false,
}: OperationWorkflowStepperProps) {
  const includeAi = shouldIncludeAiSteps(enableAi, state);
  const latchRef = useRef<WorkflowLatch>(emptyWorkflowLatch());
  const opIdRef = useRef(state.operation_id);

  if (opIdRef.current !== state.operation_id) {
    opIdRef.current = state.operation_id;
    latchRef.current = emptyWorkflowLatch();
  }
  latchRef.current = updateWorkflowLatch(latchRef.current, state, includeAi);
  const defs = workflowStepDefs(includeAi, {
    skipTier1: latchRef.current.tier1GateSkipped,
  });
  const currentId = resolveCurrentWorkflowStep(
    state,
    includeAi,
    latchRef.current,
    {
      aiApplyFinished,
      commitFinished,
    },
  );
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
              <span
                key={step.id}
                style={styles.step}
                aria-current={isActive ? 'step' : undefined}
              >
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
                <span style={labelStyle(isActive, isPending)}>
                  {step.label}
                </span>
              </span>
            );
          })}
        </div>
      </CardBody>
    </Card>
  );
}
