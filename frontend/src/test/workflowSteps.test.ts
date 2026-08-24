import { describe, expect, it } from 'vitest';
import type { ProjectOperationState } from '@apme/ui-workflow';
import {
  emptyWorkflowLatch,
  needsCommitStep,
  resolveCurrentWorkflowStep,
  shouldIncludeAiSteps,
  stepVisualState,
  updateWorkflowLatch,
  workflowStepDefs,
} from '../../packages/ui-workflow/src/remediation/workflowSteps';

function baseState(
  overrides: Partial<ProjectOperationState> = {},
): ProjectOperationState {
  return {
    operation_id: 'op-1',
    project_id: 'p-1',
    scan_id: 's-1',
    status: 'scanning',
    scan_type: 'check',
    started_at: new Date().toISOString(),
    progress: [],
    ...overrides,
  };
}

describe('workflowStepDefs', () => {
  it('uses simplified Apply findings step when AI is off (AAP-88780)', () => {
    const defs = workflowStepDefs(false);
    expect(defs.map((s) => s.id)).toEqual([
      'scan',
      'findings',
      'apply_findings',
      'commit',
      'complete',
    ]);
    expect(defs.find((s) => s.id === 'apply_findings')?.label).toBe('Apply findings');
    expect(defs.find((s) => s.id === 'commit')?.label).toBe('Create branch');
    expect(defs.find((s) => s.id === 'complete')?.label).toBe('Commit');
  });

  it('includes AI steps with separate tier1 when AI is on', () => {
    const defs = workflowStepDefs(true);
    expect(defs.map((s) => s.id)).toEqual([
      'scan',
      'findings',
      'tier1_proposals',
      'tier1_applied',
      'ai_escalation',
      'ai_proposals',
      'ai_applied',
      'commit',
      'complete',
    ]);
    expect(defs.find((s) => s.id === 'ai_escalation')?.label).toBe(
      'AI escalation',
    );
    expect(defs.find((s) => s.id === 'tier1_proposals')?.label).toBe(
      'Rule-based fix proposals',
    );
    expect(defs.find((s) => s.id === 'commit')?.label).toBe('Create branch');
    expect(defs.find((s) => s.id === 'complete')?.label).toBe('Commit');
  });
});

describe('resolveCurrentWorkflowStep', () => {
  it('starts on Scan', () => {
    const latch = emptyWorkflowLatch();
    expect(resolveCurrentWorkflowStep(baseState(), false, latch)).toBe('scan');
  });

  it('moves to Review findings when assessed', () => {
    const latch = updateWorkflowLatch(
      emptyWorkflowLatch(),
      baseState({ status: 'assessed', findings: [] }),
      false,
    );
    expect(
      resolveCurrentWorkflowStep(
        baseState({ status: 'assessed' }),
        false,
        latch,
      ),
    ).toBe('findings');
  });

  it('uses apply_findings for Gate 1 awaiting_approval when AI is off (AAP-88780)', () => {
    const state = baseState({
      status: 'awaiting_approval',
      proposals: [
        {
          id: 't1-1',
          rule_id: 'M001',
          file: 'a.yml',
          tier: 1,
          confidence: 1,
          source: 'deterministic',
        },
      ],
    });
    const latch = updateWorkflowLatch(emptyWorkflowLatch(), state, false);
    expect(resolveCurrentWorkflowStep(state, false, latch)).toBe(
      'apply_findings',
    );
  });

  it('uses tier1_proposals for Gate 1 awaiting_approval when AI is on', () => {
    const state = baseState({
      status: 'awaiting_approval',
      proposals: [
        {
          id: 't1-1',
          rule_id: 'M001',
          file: 'a.yml',
          tier: 1,
          confidence: 1,
          source: 'deterministic',
        },
      ],
    });
    const latch = updateWorkflowLatch(emptyWorkflowLatch(), state, true);
    expect(resolveCurrentWorkflowStep(state, true, latch)).toBe(
      'tier1_proposals',
    );
  });

  it('uses AI escalation for awaiting_ai_triage', () => {
    const state = baseState({
      status: 'awaiting_ai_triage',
      ai_triage_candidates: [
        {
          rule_id: 'L001',
          message: 'needs AI',
          file: 'a.yml',
          path: 'playbooks/a.yml::0',
        },
      ],
    });
    const latch = updateWorkflowLatch(emptyWorkflowLatch(), state, true);
    expect(resolveCurrentWorkflowStep(state, true, latch)).toBe('ai_escalation');
    expect(latch.pastTier1Applied).toBe(true);
    expect(latch.pastAiEscalation).toBe(true);
  });

  it('keeps AI escalation while applying after escalate-ai', () => {
    let latch = emptyWorkflowLatch();
    latch = updateWorkflowLatch(
      latch,
      baseState({ status: 'awaiting_ai_triage', ai_triage_candidates: [] }),
      true,
    );
    const applying = baseState({ status: 'applying', scan_type: 'remediate' });
    latch = updateWorkflowLatch(latch, applying, true);
    expect(resolveCurrentWorkflowStep(applying, true, latch)).toBe(
      'ai_escalation',
    );
  });

  it('uses AI proposals for Gate 2 awaiting_approval', () => {
    const state = baseState({
      status: 'awaiting_approval',
      proposals: [
        {
          id: 'ai-1',
          rule_id: 'L001',
          file: 'a.yml',
          tier: 2,
          confidence: 0.8,
          source: 'ai',
          explanation: 'rewrite',
        },
      ],
    });
    const latch = updateWorkflowLatch(emptyWorkflowLatch(), state, true);
    expect(resolveCurrentWorkflowStep(state, true, latch)).toBe('ai_proposals');
    expect(latch.pastTier1Applied).toBe(true);
  });

  it('uses AI applied while applying after Gate 2 approve', () => {
    let latch = emptyWorkflowLatch();
    const review = baseState({
      status: 'awaiting_approval',
      proposals: [
        {
          id: 'ai-1',
          rule_id: 'L001',
          file: 'a.yml',
          tier: 2,
          confidence: 0.8,
          source: 'ai',
        },
      ],
    });
    latch = updateWorkflowLatch(latch, review, true);
    const applying = baseState({ status: 'applying', scan_type: 'remediate' });
    latch = updateWorkflowLatch(latch, applying, true);
    expect(resolveCurrentWorkflowStep(applying, true, latch)).toBe('ai_applied');
    expect(latch.pastAiApplied).toBe(true);
  });

  it('lands on Commit when completed with remediations', () => {
    const state = baseState({
      status: 'completed',
      scan_type: 'remediate',
      result: {
        total_violations: 10,
        fixable: 2,
        ai_proposed: 0,
        ai_declined: 0,
        ai_accepted: 0,
        manual_review: 0,
        remediated_count: 2,
        fixed_violations: [],
        patches: [{ file: 'a.yml', diff: '@@' }],
      },
    });
    const latch = updateWorkflowLatch(emptyWorkflowLatch(), state, false);
    expect(resolveCurrentWorkflowStep(state, false, latch)).toBe('commit');
    expect(
      resolveCurrentWorkflowStep(state, false, latch, { commitFinished: true }),
    ).toBe('complete');
  });

  it('marks Complete on completed with nothing to commit', () => {
    let latch = emptyWorkflowLatch();
    latch = updateWorkflowLatch(
      latch,
      baseState({ status: 'assessed' }),
      false,
    );
    latch = updateWorkflowLatch(
      latch,
      baseState({ status: 'completed' }),
      false,
    );
    expect(
      resolveCurrentWorkflowStep(
        baseState({ status: 'completed' }),
        false,
        latch,
      ),
    ).toBe('complete');
  });

  it('marks Complete after PR submitted', () => {
    const state = baseState({
      status: 'pr_submitted',
      pr_url: 'https://example.com/pr/1',
      result: {
        total_violations: 1,
        fixable: 1,
        ai_proposed: 0,
        ai_declined: 0,
        ai_accepted: 0,
        manual_review: 0,
        remediated_count: 1,
        fixed_violations: [],
        patches: [],
      },
    });
    expect(
      resolveCurrentWorkflowStep(state, true, emptyWorkflowLatch()),
    ).toBe('complete');
  });
});

describe('needsCommitStep', () => {
  it('is true when remediated_count > 0', () => {
    expect(
      needsCommitStep(
        baseState({
          status: 'completed',
          result: {
            total_violations: 1,
            fixable: 1,
            ai_proposed: 0,
            ai_declined: 0,
            ai_accepted: 0,
            manual_review: 0,
            remediated_count: 1,
            fixed_violations: [],
            patches: [],
          },
        }),
      ),
    ).toBe(true);
  });

  it('is false when a PR already exists', () => {
    expect(
      needsCommitStep(
        baseState({
          status: 'pr_submitted',
          pr_url: 'https://example.com/pr/1',
          result: {
            total_violations: 1,
            fixable: 1,
            ai_proposed: 0,
            ai_declined: 0,
            ai_accepted: 0,
            manual_review: 0,
            remediated_count: 1,
            fixed_violations: [],
            patches: [],
          },
        }),
      ),
    ).toBe(false);
  });
});

describe('shouldIncludeAiSteps', () => {
  it('infers AI from result even if toggle is off', () => {
    expect(
      shouldIncludeAiSteps(
        false,
        baseState({
          result: {
            total_violations: 1,
            fixable: 0,
            ai_proposed: 2,
            ai_declined: 0,
            ai_accepted: 1,
            manual_review: 0,
            remediated_count: 1,
            fixed_violations: [],
            patches: [],
          },
        }),
      ),
    ).toBe(true);
  });
});

describe('stepVisualState', () => {
  it('marks prior steps success and current info (non-AI)', () => {
    const defs = workflowStepDefs(false);
    expect(stepVisualState('scan', 'findings', defs, 'assessed')).toEqual({
      variant: 'success',
      isCurrent: false,
    });
    expect(stepVisualState('findings', 'findings', defs, 'assessed')).toEqual({
      variant: 'info',
      isCurrent: true,
    });
    expect(
      stepVisualState('apply_findings', 'findings', defs, 'assessed'),
    ).toEqual({ variant: 'pending', isCurrent: false });
  });

  it('marks prior steps success and current info (AI)', () => {
    const defs = workflowStepDefs(true);
    expect(stepVisualState('scan', 'findings', defs, 'assessed')).toEqual({
      variant: 'success',
      isCurrent: false,
    });
    expect(
      stepVisualState('tier1_proposals', 'findings', defs, 'assessed'),
    ).toEqual({ variant: 'pending', isCurrent: false });
  });
});
