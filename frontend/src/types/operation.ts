/**
 * Common types for check/remediate operations shared by the Playground
 * (useSessionStream) and project operation components (ADR-052).
 */

export type OperationStatus =
  | "idle"
  | "connecting"
  | "preparing"
  | "cloning"
  | "checking"
  | "tier1_done"
  | "awaiting_approval"
  | "applying"
  | "complete"
  | "disconnected"
  | "error";

export interface OperationProgress {
  phase: string;
  message: string;
  timestamp: number;
  progress?: number;
  level?: number;
}

export interface OperationProposal {
  id: string;
  rule_id: string;
  file: string;
  tier: number;
  confidence: number;
  explanation?: string;
  diff_hunk?: string;
  /** Draft / review status from engine or Gateway working set. */
  status?: "proposed" | "declined" | "pending" | "approved" | "rejected";
  suggestion?: string;
  line_start?: number;
  /** Stable graph node path (ADR-062 / Option C Gate 1 grouping key). */
  path?: string;
  /** ContentGraph NodeType (task, block, play, …); empty when not graph-backed. */
  node_type?: string;
  /** ``deterministic`` (Gate 1) or ``ai`` / ``ai-candidate`` (Gate 2). */
  source?: string;
  before_text?: string;
  after_text?: string;
}

export interface OperationResult {
  total_violations: number;
  fixable: number;
  ai_candidate: number;
  ai_proposed: number;
  ai_declined: number;
  ai_accepted: number;
  manual_review: number;
  remediated_count?: number;
}

export interface OperationState {
  status: OperationStatus;
  progress: OperationProgress[];
  proposals: OperationProposal[];
  result: OperationResult | null;
  error: string | null;
  approve: (ids: string[]) => void;
  cancel: () => void;
  reset: () => void;
}
