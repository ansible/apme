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
  validator_source?: string;
  snippet?: string;
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

export interface ActivitySummary {
  scan_id: string;
  session_id: string;
  project_path: string;
  source: string;
  created_at: string;
  scan_type: string;
  total_violations: number;
  fixable: number;
  ai_candidate: number;
  ai_proposed: number;
  ai_declined: number;
  ai_accepted: number;
  manual_review: number;
  remediated_count: number;
}

export interface PatchDetail {
  id: number;
  file: string;
  diff: string;
}

export interface ActivityDetail extends ActivitySummary {
  diagnostics_json: string | null;
  violations: ViolationDetail[];
  proposals: ProposalDetail[];
  logs: LogEntry[];
  patches: PatchDetail[];
}

export interface SessionSummary {
  session_id: string;
  project_path: string;
  first_seen: string;
  last_seen: string;
}

export interface SessionDetail extends SessionSummary {
  scans: ActivitySummary[];
}

export interface TopViolation {
  rule_id: string;
  count: number;
}

export interface TrendPoint {
  scan_id: string;
  created_at: string;
  total_violations: number;
  fixable: number;
  /** Whether this point is from a check-only run or a remediate run (`check` / `remediate`). */
  scan_type: string;
}

export interface RemediationRateEntry {
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

export interface ComponentHealth {
  name: string;
  status: string;
  address: string;
}

export interface HealthStatus {
  status: string;
  database: string;
  components: ComponentHealth[];
}

export interface AiModelInfo {
  id: string;
  provider: string;
  name: string;
}

// ── Project types (ADR-037) ──────────────────────────────────────────

export interface ProjectSummary {
  id: string;
  name: string;
  repo_url: string;
  branch: string;
  created_at: string;
  health_score: number;
  total_violations: number;
  violation_trend: 'improving' | 'declining' | 'stable';
  scan_count: number;
  last_scanned_at: string | null;
}

export interface ProjectDetail extends ProjectSummary {
  latest_scan: ActivitySummary | null;
  severity_breakdown: Record<string, number>;
}

export interface CreateProjectRequest {
  name: string;
  repo_url: string;
  branch?: string;
}

export interface UpdateProjectRequest {
  name?: string;
  repo_url?: string;
  branch?: string;
}

export interface DashboardSummary {
  total_projects: number;
  total_scans: number;
  total_violations: number;
  current_violations: number;
  current_fixable: number;
  current_ai_candidates: number;
  total_fixed: number;
  avg_health_score: number;
}

export interface ProjectRanking {
  id: string;
  name: string;
  health_score: number;
  total_violations: number;
  scan_count: number;
  last_scanned_at: string | null;
  days_since_last_scan: number | null;
}

// ── Dependencies types (ADR-040) ─────────────────────────────────────

export interface CollectionRef {
  fqcn: string;
  version: string;
  source: string;
}

export interface PythonPackageRef {
  name: string;
  version: string;
}

export interface ProjectDependencies {
  ansible_core_version: string;
  collections: CollectionRef[];
  python_packages: PythonPackageRef[];
  requirements_files: string[];
  dependency_tree: string;
}

export interface CollectionSummary {
  fqcn: string;
  version: string;
  source: string;
  project_count: number;
}

export interface CollectionProjectRef {
  id: string;
  name: string;
  health_score: number;
  collection_version: string;
}

export interface CollectionDetail {
  fqcn: string;
  versions: string[];
  source: string;
  project_count: number;
  projects: CollectionProjectRef[];
}

export interface PythonPackageSummary {
  name: string;
  version: string;
  project_count: number;
}

export interface PythonPackageProjectRef {
  id: string;
  name: string;
  health_score: number;
  package_version: string;
}

export interface PythonPackageDetail {
  name: string;
  versions: string[];
  project_count: number;
  projects: PythonPackageProjectRef[];
}
