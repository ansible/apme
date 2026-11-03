/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import type { Proposal, Violation } from "./types";
import { effectiveFixType } from "./severity";

type RawProposal = Omit<Partial<Proposal>, "status"> & {
  line_start?: number;
  suggestion?: string;
  explanation?: string;
  fixed_yaml?: string;
  ai_reason?: string;
  status?: string;
};

/** Match a gateway proposal to a scan violation when violation_id is absent. */
export function findViolationForProposal(
  proposal: Pick<Proposal, "violation_id" | "rule_id" | "file" | "line">,
  violations: Violation[],
): Violation | undefined {
  if (proposal.violation_id > 0) {
    const byId = violations.find((v) => v.id === proposal.violation_id);
    if (byId) {
      return byId;
    }
  }
  const matches = violations.filter(
    (v) => v.rule_id === proposal.rule_id && v.file === proposal.file,
  );
  if (matches.length === 0) {
    return undefined;
  }
  if (matches.length === 1) {
    return matches[0];
  }
  if (proposal.line > 0) {
    const byLine = matches.find((v) => v.line === proposal.line);
    if (byLine) {
      return byLine;
    }
  }
  return matches[0];
}

function mapProposalStatus(raw: unknown): Proposal["status"] {
  if (raw === "accepted") {
    return "accepted";
  }
  if (raw === "declined") {
    return "declined";
  }
  return "pending";
}

/** Normalize gateway operation proposals into portal Proposal shape. */
export function normalizeGatewayProposal(
  raw: RawProposal,
  violations: Violation[],
): Proposal {
  const line =
    (typeof raw.line === "number" ? raw.line : undefined) ??
    (typeof raw.line_start === "number" ? raw.line_start : 0);
  const violation = findViolationForProposal(
    {
      violation_id: typeof raw.violation_id === "number" ? raw.violation_id : 0,
      rule_id: String(raw.rule_id ?? ""),
      file: String(raw.file ?? ""),
      line,
    },
    violations,
  );
  const tier = typeof raw.tier === "number" ? raw.tier : undefined;
  const explanation = String(raw.explanation ?? raw.ai_reason ?? "").trim();
  const suggestion = String(
    raw.suggestion ?? raw.fixed_yaml ?? violation?.fixed_yaml ?? "",
  ).trim();

  return {
    id: String(raw.id ?? ""),
    violation_id: violation?.id ?? (raw.violation_id as number) ?? 0,
    rule_id: String(raw.rule_id ?? violation?.rule_id ?? ""),
    file: String(raw.file ?? violation?.file ?? ""),
    line: line || violation?.line || 0,
    original_yaml: String(raw.original_yaml ?? violation?.original_yaml ?? ""),
    fixed_yaml: suggestion,
    status: mapProposalStatus(raw.status),
    ai_reason: explanation || undefined,
    tier,
    confidence: typeof raw.confidence === "number" ? raw.confidence : undefined,
    explanation: explanation || undefined,
    diff_hunk: typeof raw.diff_hunk === "string" ? raw.diff_hunk : undefined,
    suggestion: suggestion || undefined,
  };
}

export function normalizeProposals(
  raw: RawProposal[],
  violations: Violation[],
): Proposal[] {
  return raw.map((p) => normalizeGatewayProposal(p, violations));
}

/** True when the review UI can show a meaningful code change for this proposal. */
export function proposalHasVisibleDiff(proposal: Proposal): boolean {
  if (proposal.diff_hunk?.trim()) {
    return true;
  }
  const before = proposal.original_yaml?.trim() ?? "";
  const after = proposal.fixed_yaml?.trim() ?? "";
  return Boolean(before && after && before !== after);
}

/** True when the engine could not produce an automated fix for this proposal. */
export function isDeclinedProposal(proposal: Proposal): boolean {
  return proposal.status === "declined";
}

/** Proto RemediationResolution values indicating AI was involved. */
const AI_REMEDIATION_RESOLUTIONS = new Set([4, 5, 6, 11]);

/** True when remediation attempted AI for this violation (including abstained). */
export function violationHadAiAttempt(violation: Violation): boolean {
  const resolution = violation.remediation_resolution;
  return (
    typeof resolution === "number" && AI_REMEDIATION_RESOLUTIONS.has(resolution)
  );
}

/** Violation IDs that received (or were offered) an AI proposal on the latest run. */
export function collectAiAssistedViolationIds(
  violations: Violation[],
  proposals: Array<
    Pick<Proposal, "rule_id" | "file" | "line" | "tier" | "violation_id">
  >,
  enableAi: boolean,
): Set<number> {
  const ids = new Set<number>();
  if (!enableAi) {
    return ids;
  }
  for (const violation of violations) {
    if (violationHadAiAttempt(violation)) {
      ids.add(violation.id);
    }
  }
  for (const proposal of proposals) {
    if (typeof proposal.tier === "number" && proposal.tier < 2) {
      continue;
    }
    const violation = findViolationForProposal(proposal, violations);
    if (violation) {
      ids.add(violation.id);
    }
  }
  return ids;
}

/** Fix-type label for a violation, including AI overlay from proposals / resolution. */
export function effectiveViolationFixType(
  violation: Violation,
  enableAi: boolean,
  aiAssistedIds?: ReadonlySet<number>,
): ReturnType<typeof effectiveFixType> {
  if (
    enableAi &&
    (aiAssistedIds?.has(violation.id) || violationHadAiAttempt(violation))
  ) {
    return "ai";
  }
  return effectiveFixType(violation.remediation_class, enableAi);
}

export function isAiRemediationProposal(
  proposal: Proposal,
  violations: Violation[],
  enableAi: boolean,
): boolean {
  if (typeof proposal.tier === "number") {
    if (proposal.tier >= 2) {
      return true;
    }
    if (proposal.tier === 1) {
      return false;
    }
  }
  if (proposal.ai_reason?.trim() || proposal.explanation?.trim()) {
    return true;
  }
  const violation = findViolationForProposal(proposal, violations);
  return violation
    ? effectiveFixType(violation.remediation_class, enableAi) === "ai"
    : false;
}

/** True when the user must explicitly include this proposal via checkbox before PR. */
export function proposalNeedsUserReview(
  _proposal: Proposal,
  _violations: Violation[],
  _enableAi: boolean,
): boolean {
  return true;
}
