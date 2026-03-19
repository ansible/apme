/** API models matching the FastAPI gateway Pydantic schemas. */

export type ScanStatus = 'running' | 'completed' | 'failed';
export type ProposalStatus = 'pending' | 'accepted' | 'rejected' | 'applied';
export type ViolationLevel = 'error' | 'warning' | 'hint';

export interface ScanCreate {
  project_name: string;
  files: Record<string, string>;
  ansible_core_version?: string;
  collection_specs?: string[];
}

export type ScanType = 'scan' | 'fix';

export interface ScanSummary {
  id: string;
  project_name: string;
  created_at: string;
  status: ScanStatus;
  scan_type: ScanType;
  total_violations: number;
  error_count: number;
  warning_count: number;
  hint_count: number;
  fixed_count: number;
}

export interface Violation {
  id: string;
  rule_id: string;
  level: ViolationLevel;
  message: string;
  file: string;
  line: number | null;
  line_end: number | null;
  path: string | null;
}

export interface ValidatorDiagnostics {
  validator_name: string;
  request_id: string;
  total_ms: number;
  files_received: number;
  violations_found: number;
  rule_timings: RuleTiming[];
  metadata: Record<string, string>;
}

export interface RuleTiming {
  rule_id: string;
  elapsed_ms: number;
  violations: number;
}

export interface Diagnostics {
  engine_parse_ms: number;
  engine_annotate_ms: number;
  engine_total_ms: number;
  files_scanned: number;
  trees_built: number;
  total_violations: number;
  validators: ValidatorDiagnostics[];
  fan_out_ms: number;
  total_ms: number;
}

export interface ScanDetail extends ScanSummary {
  violations: Violation[];
  diagnostics: Diagnostics | null;
  options: Record<string, unknown> | null;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface Rule {
  rule_id: string;
  validator: string;
  description: string;
  has_fixer: boolean;
}

export interface RuleDetail extends Rule {
  examples: Record<string, string>;
}

export interface FormatDiff {
  path: string;
  diff: string;
}

export interface FixJobStatus {
  job_id: string;
  status: string;
  progress: Record<string, unknown>[];
  result: Record<string, unknown> | null;
}

export interface RemediationProposal {
  id: string;
  scan_id: string;
  violation_id: string;
  tier: number;
  status: ProposalStatus;
  diff: string | null;
  proposed_by: string | null;
  reviewed_by: string | null;
  reviewed_at: string | null;
}

export interface ServiceHealth {
  name: string;
  status: string;
  latency_ms: number | null;
}

export interface HealthStatus {
  status: string;
  services: ServiceHealth[];
}
