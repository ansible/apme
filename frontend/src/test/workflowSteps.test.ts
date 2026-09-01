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
    let latch = emptyWorkflowLatch();
    latch = updateWorkflowLatch(
      latch,
      baseState({ status: 'applying', scan_type: 'remediate' }),
      true,
    );
    expect(latch.tier1GateSkipped).toBe(true);
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
    latch = updateWorkflowLatch(latch, state, true);
    expect(resolveCurrentWorkflowStep(state, true, latch)).toBe('ai_escalation');
    expect(latch.pastTier1Applied).toBe(false);
    expect(latch.pastAiEscalation).toBe(true);
    expect(workflowStepDefs(true, { skipTier1: true }).map((s) => s.id)).not.toContain(
      'tier1_proposals',
    );
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
    expect(latch.seenGate2Review).toBe(true);
    expect(latch.pastAiReview).toBe(false);
  });

  it('uses AI proposals while applying when Gate 2 payloads beat status_changed', () => {
    let latch = emptyWorkflowLatch();
    latch = updateWorkflowLatch(
      latch,
      baseState({ status: 'awaiting_ai_triage', ai_triage_candidates: [] }),
      true,
    );
    const state = baseState({
      status: 'applying',
      scan_type: 'remediate',
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
    latch = updateWorkflowLatch(latch, state, true);
    expect(resolveCurrentWorkflowStep(state, true, latch)).toBe('ai_proposals');
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

  it('uses AI applied while applying after Gate 2 approve even if proposals linger', () => {
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
    const applying = baseState({
      status: 'applying',
      scan_type: 'remediate',
      proposals: review.proposals,
    });
    latch = updateWorkflowLatch(latch, applying, true);
    expect(resolveCurrentWorkflowStep(applying, true, latch)).toBe('ai_applied');
    expect(latch.pastAiApplied).toBe(true);
  });

  it('lands on AI applied when completed after Gate 2 until acknowledged', () => {
    let latch = emptyWorkflowLatch();
    latch = updateWorkflowLatch(
      latch,
      baseState({
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
      }),
      true,
    );
    latch = updateWorkflowLatch(
      latch,
      baseState({ status: 'applying', scan_type: 'remediate' }),
      true,
    );
    const completed = baseState({
      status: 'completed',
      scan_type: 'remediate',
      result: {
        total_violations: 5,
        fixable: 2,
        ai_proposed: 1,
        ai_declined: 0,
        ai_accepted: 1,
        manual_review: 0,
        remediated_count: 2,
        fixed_violations: [],
        patches: [{ file: 'a.yml', diff: '@@' }],
      },
    });
    latch = updateWorkflowLatch(latch, completed, true);
    expect(resolveCurrentWorkflowStep(completed, true, latch)).toBe('ai_applied');
    expect(
      resolveCurrentWorkflowStep(completed, true, latch, { aiApplyFinished: true }),
    ).toBe('commit');
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

  it('does not mark AI review/applied on completed when Gate 2 was skipped', () => {
    const state = baseState({
      status: 'completed',
      scan_type: 'remediate',
      ai_triage_candidates: [{ rule_id: 'L001', message: 'x', file: 'a.yml' }],
      result: {
        total_violations: 5,
        fixable: 4,
        ai_proposed: 0,
        ai_declined: 0,
        ai_accepted: 0,
        manual_review: 0,
        remediated_count: 4,
        fixed_violations: [],
        patches: [{ file: 'a.yml', diff: '@@' }],
      },
    });
    const latch = updateWorkflowLatch(emptyWorkflowLatch(), state, true);
    expect(latch.pastAiEscalation).toBe(true);
    expect(latch.pastAiReview).toBe(false);
    expect(latch.pastAiApplied).toBe(false);
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

describe('edge cases', () => {
  it('skipTier1 omits tier1_proposals and tier1_applied from step defs', () => {
    const defs = workflowStepDefs(true, { skipTier1: true });
    const ids = defs.map((s) => s.id);
    expect(ids).not.toContain('tier1_proposals');
    expect(ids).not.toContain('tier1_applied');
    expect(ids).toContain('ai_escalation');
    expect(ids).toContain('ai_proposals');
  });

  it('tier1GateSkipped stays false when Gate 1 proposals arrive', () => {
    let latch = emptyWorkflowLatch();
    const state = baseState({
      status: 'awaiting_approval',
      proposals: [
        { id: 't1-0', rule_id: 'M001', file: 'a.yml', tier: 1, confidence: 1, source: 'deterministic' },
      ],
    });
    latch = updateWorkflowLatch(latch, state, true);
    expect(latch.tier1GateSkipped).toBe(false);
    expect(latch.pastTier1Review).toBe(true);
  });

  it('applying twice does not double-set tier1GateSkipped', () => {
    let latch = emptyWorkflowLatch();
    const applying = baseState({ status: 'applying', scan_type: 'remediate' });
    latch = updateWorkflowLatch(latch, applying, true);
    expect(latch.tier1GateSkipped).toBe(true);
    // Second applying should not flip it back
    latch = updateWorkflowLatch(latch, applying, true);
    expect(latch.tier1GateSkipped).toBe(true);
  });

  it('full flow: Gate 1 → Gate 2 → completed latches correctly', () => {
    let latch = emptyWorkflowLatch();
    // Gate 1 proposals arrive
    latch = updateWorkflowLatch(
      latch,
      baseState({
        status: 'awaiting_approval',
        proposals: [{ id: 't1-0', rule_id: 'M001', file: 'a.yml', tier: 1, confidence: 1, source: 'deterministic' }],
      }),
      true,
    );
    expect(latch.pastTier1Review).toBe(true);
    expect(latch.tier1GateSkipped).toBe(false);

    // Applying after Gate 1 approve
    latch = updateWorkflowLatch(latch, baseState({ status: 'applying', scan_type: 'remediate' }), true);
    expect(latch.pastTier1Applied).toBe(true);
    expect(latch.pastAiEscalation).toBe(false);

    // AI triage
    latch = updateWorkflowLatch(
      latch,
      baseState({ status: 'awaiting_ai_triage', ai_triage_candidates: [{ rule_id: 'L001', message: 'x', file: 'a.yml', path: 'a.yml::0' }] }),
      true,
    );
    expect(latch.pastAiEscalation).toBe(true);

    // Gate 2 proposals
    latch = updateWorkflowLatch(
      latch,
      baseState({
        status: 'awaiting_approval',
        proposals: [{ id: 'ai-0', rule_id: 'L001', file: 'a.yml', tier: 2, confidence: 0.8, source: 'ai' }],
      }),
      true,
    );
    expect(latch.seenGate2Review).toBe(true);
    expect(latch.pastAiReview).toBe(false);

    // Applying after Gate 2 approve
    latch = updateWorkflowLatch(latch, baseState({ status: 'applying', scan_type: 'remediate' }), true);
    expect(latch.pastAiApplied).toBe(true);

    // Completed
    latch = updateWorkflowLatch(
      latch,
      baseState({
        status: 'completed',
        scan_type: 'remediate',
        result: { total_violations: 5, fixable: 3, ai_proposed: 1, ai_declined: 0, ai_accepted: 1, manual_review: 1, remediated_count: 3, fixed_violations: [], patches: [{ file: 'a.yml', diff: '@@' }] },
      }),
      true,
    );
    expect(latch.pastAiEscalation).toBe(true);
    expect(latch.pastAiReview).toBe(true);
    expect(latch.pastAiApplied).toBe(true);
  });

  it('mixed tier2 proposals (AI + post-AI deterministic) are recognized as aiGate', () => {
    const state = baseState({
      status: 'awaiting_approval',
      proposals: [
        { id: 'ai-0', rule_id: 'L001', file: 'a.yml', tier: 2, confidence: 0.8, source: 'ai' },
        { id: 't1-0001', rule_id: 'M002', file: 'b.yml', tier: 2, confidence: 1, source: 'deterministic' },
      ],
    });
    const latch = updateWorkflowLatch(emptyWorkflowLatch(), state, true);
    expect(latch.seenGate2Review).toBe(true);
    expect(latch.pastAiReview).toBe(false);
    expect(resolveCurrentWorkflowStep(state, true, latch)).toBe('ai_proposals');
  });

  it('failed status preserves latch without inventing later milestones', () => {
    let latch = emptyWorkflowLatch();
    latch = updateWorkflowLatch(
      latch,
      baseState({ status: 'awaiting_approval', proposals: [{ id: 't1-0', rule_id: 'M001', file: 'a.yml', tier: 1, confidence: 1, source: 'deterministic' }] }),
      true,
    );
    latch = updateWorkflowLatch(latch, baseState({ status: 'failed' }), true);
    expect(latch.pastTier1Review).toBe(true);
    expect(latch.pastAiEscalation).toBe(false);
    expect(latch.pastAiReview).toBe(false);
  });

  it('infers seenGate2Review on completed after AI triage produced fixes', () => {
    const completed = baseState({
      status: 'completed',
      scan_type: 'remediate',
      ai_triage_candidates: [{ rule_id: 'L001', message: 'x', file: 'a.yml', path: 'a.yml::0' }],
      result: {
        total_violations: 5,
        fixable: 2,
        ai_proposed: 1,
        ai_declined: 0,
        ai_accepted: 1,
        manual_review: 0,
        remediated_count: 2,
        fixed_violations: [],
        patches: [{ file: 'a.yml', diff: '@@' }],
      },
    });
    const latch = updateWorkflowLatch(emptyWorkflowLatch(), completed, true);
    expect(latch.seenGate2Review).toBe(true);
    expect(resolveCurrentWorkflowStep(completed, true, latch)).toBe('ai_applied');
  });

  it('completed with no AI activity does not mark ai steps (AI-enabled but no triage)', () => {
    const state = baseState({
      status: 'completed',
      scan_type: 'remediate',
      result: { total_violations: 2, fixable: 2, ai_proposed: 0, ai_declined: 0, ai_accepted: 0, manual_review: 0, remediated_count: 2, fixed_violations: [], patches: [{ file: 'a.yml', diff: '@@' }] },
    });
    const latch = updateWorkflowLatch(emptyWorkflowLatch(), state, true);
    expect(latch.pastAiEscalation).toBe(false);
    expect(latch.pastAiReview).toBe(false);
    expect(latch.pastAiApplied).toBe(false);
  });
});
