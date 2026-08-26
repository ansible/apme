/**
 * Proposal / gate helpers adapted from ansible-backstage-plugins@prototype/apme
 * (plugins/backstage-apme-common/src/proposalTier.ts) for ADR-062 Option C.
 *
 * Live Gate 1/2 review keys on engine proposal ``id`` + ``path`` / ``source``,
 * not violation PKs (stubs often lack violation_ids until FixCompleted).
 */

import type { OperationProposal } from '../types/operation';

/** Split coupled rule IDs from an engine proposal (e.g. ``L026,M001``). */
export function splitRuleIds(ruleId: string): string[] {
  return ruleId
    .split(',')
    .map((r) => r.trim())
    .filter(Boolean);
}

/** Node title for a proposal card: prefer graph path, else file + line. */
export function proposalNodeTitle(proposal: OperationProposal): string {
  const path = proposal.path?.trim();
  if (path) {
    return path;
  }
  const file = proposal.file || '(unknown file)';
  if (proposal.line_start != null && proposal.line_start > 0) {
    return `${file}:${proposal.line_start}`;
  }
  return file;
}

/** True when any proposal belongs to Gate 2 (incl. ai-declined rows). */
export function proposalsLookLikeAiGate(
  proposals: OperationProposal[],
): boolean {
  return proposals.some(
    (p) =>
      isAiRemediationProposal(p) ||
      (p.tier ?? 0) >= 2 ||
      p.source === 'ai' ||
      p.source === 'ai-candidate' ||
      p.id.startsWith('ai-'),
  );
}

export type ProposalReviewGate = 'tier1' | 'ai';

/**
 * Resolve Gate 1 vs Gate 2 review copy and behavior.
 *
 * Proposal shape alone is not enough: Gate 2 can be declined-only (no diffs)
 * while ``ai_triage_candidates`` proves escalation already happened.
 */
export function resolveProposalReviewGate(
  proposals: OperationProposal[],
  options: { enableAi?: boolean; pastAiEscalation?: boolean } = {},
): ProposalReviewGate {
  if (proposalsLookLikeAiGate(proposals)) {
    return 'ai';
  }
  if (options.enableAi && options.pastAiEscalation) {
    return 'ai';
  }
  return 'tier1';
}

/** True when the proposal is Gate 2 AI (not rule-based fix / deterministic). */
export function isAiRemediationProposal(proposal: OperationProposal): boolean {
  if (proposal.source === 'ai' || proposal.source === 'ai-candidate') {
    return true;
  }
  if (proposal.source === 'deterministic') {
    return false;
  }
  if (typeof proposal.tier === 'number') {
    if (proposal.tier >= 2) {
      return true;
    }
    if (proposal.tier === 1) {
      return false;
    }
  }
  return Boolean(proposal.explanation?.trim());
}

/** True when the review UI can show a meaningful code change. */
export function proposalHasVisibleDiff(proposal: OperationProposal): boolean {
  if (proposal.diff_hunk?.trim()) {
    return true;
  }
  const before = proposal.before_text?.trim() ?? '';
  const after = proposal.after_text?.trim() ?? '';
  return Boolean(before && after && before !== after);
}

/** Stable key for resetting selection when a new gate's proposals arrive. */
export function proposalsGateKey(proposals: OperationProposal[]): string {
  if (proposals.length === 0) {
    return '';
  }
  const ids = proposals.map((p) => p.id).sort();
  const gate = proposalsLookLikeAiGate(proposals) ? 'ai' : 't1';
  return `${gate}:${ids.join(',')}`;
}

/** Human label for the current approval gate. */
export function gateLabel(
  proposals: OperationProposal[],
  reviewGate?: ProposalReviewGate,
): string {
  if (proposals.length === 0 && !reviewGate) {
    return 'Review';
  }
  const gate =
    reviewGate ??
    (proposalsLookLikeAiGate(proposals) ? 'ai' : 'tier1');
  return gate === 'ai' ? 'AI proposals' : 'Rule-based fix proposals';
}

/**
 * Path-prefix descendants among proposals (client-side hierarchy).
 * ``site.yml/plays[0]`` is a parent of ``site.yml/plays[0]/tasks[1]``.
 */
export function descendantProposalIds(
  parent: OperationProposal,
  all: OperationProposal[],
): string[] {
  const prefix = (parent.path || '').trim();
  if (!prefix) {
    return [];
  }
  return all
    .filter((p) => {
      const path = (p.path || '').trim();
      return p.id !== parent.id && path.startsWith(`${prefix}/`);
    })
    .map((p) => p.id);
}
