export interface ViolationDetail {
  id: number;
  rule_id: string;
  level: string;
  message: string;
  file: string;
  line: number | null;
  path: string;
  remediation_class: number;
  scope: number;
}

export interface LogEntry {
  id: number;
  message: string;
  phase: string;
  progress: number;
  level: number;
}

export interface ProposalDetail {
  id: number;
  proposal_id: string;
  rule_id: string;
  file: string;
  tier: number;
  confidence: number;
  status: string;
}

export interface ScanSummary {
  scan_id: string;
  session_id: string;
  project_path: string;
  source: string;
  created_at: string;
  scan_type: string;
  total_violations: number;
  auto_fixable: number;
  ai_candidate: number;
  manual_review: number;
}

export interface ScanDetail extends ScanSummary {
  diagnostics_json: string | null;
  violations: ViolationDetail[];
  proposals: ProposalDetail[];
  logs: LogEntry[];
}

export interface SessionSummary {
  session_id: string;
  project_path: string;
  first_seen: string;
  last_seen: string;
}

export interface SessionDetail extends SessionSummary {
  scans: ScanSummary[];
}

export interface TopViolation {
  rule_id: string;
  count: number;
}

export interface TrendPoint {
  scan_id: string;
  created_at: string;
  total_violations: number;
  auto_fixable: number;
  scan_type: string;
}

export interface FixRateEntry {
  rule_id: string;
  fix_count: number;
}

export interface AiAcceptanceEntry {
  rule_id: string;
  approved: number;
  rejected: number;
  pending: number;
  avg_confidence: number;
}

export interface PaginatedResponse<T> {
  total: number;
  limit: number;
  offset: number;
  items: T[];
}

export interface HealthStatus {
  status: string;
  database: string;
}
