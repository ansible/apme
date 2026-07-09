export interface ViolationDetail {
  id: number;
  rule_id: string;
  level: string;
  message: string;
  file: string;
  line: number | null;
  path: string;
  remediation_class: number;
  remediation_resolution: number;
  scope: number;
  validator_source?: string;
  original_yaml?: string;
  fixed_yaml?: string;
  co_fixes?: string[];
  node_line_start?: number;
  ai_reason?: string;
  ai_suggestion?: string;
  suppressed?: boolean;
  /** Human/gate decision (ADR-062); null/undefined if never reviewed. */
  review_status?: string | null;
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
  path?: string;
  source?: string;
  gate?: string;
  rule_ids?: string[];
  violation_ids?: number[];
  line_start?: number;
  diff_hunk?: string;
  explanation?: string;
  suggestion?: string;
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
  pr_url: string | null;
}

export interface PatchDetail {
  id: number;
  file: string;
  diff: string;
}

export interface ActivityDetail extends ActivitySummary {
  project_id: string | null;
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

// ── Project types (ADR-037, ADR-052) ─────────────────────────────────

export interface ActiveOperationSummary {
  operation_id: string;
  status: string;
  scan_type: string;
  started_at: string;
}

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
  scm_provider: string | null;
  has_scm_token: boolean;
  last_scanned_commit: string;
  has_new_commits: boolean;
  active_operation: ActiveOperationSummary | null;
}

export interface ProjectDetail extends ProjectSummary {
  latest_scan: ActivitySummary | null;
  severity_breakdown: Record<string, number>;
  active_operation: ActiveOperationSummary | null;
}

export interface CreateProjectRequest {
  name: string;
  repo_url: string;
  branch?: string;
  scm_token?: string;
  scm_provider?: string;
}

export interface UpdateProjectRequest {
  name?: string;
  repo_url?: string;
  branch?: string;
  scm_token?: string;
  scm_provider?: string;
}

// ── SCM submit types (ADR-050) ───────────────────────────────────────

export interface SubmitRequest {
  activity_id?: string;
  branch_name?: string;
  create_pr?: boolean;
  title?: string;
  body?: string;
  scm_token?: string;
}

export interface SubmitResponse {
  branch_name: string;
  commit_sha: string;
  pr_url: string | null;
  provider: string;
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
  last_scan_id: string;
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
  last_scan_id: string;
}

export interface PythonPackageDetail {
  name: string;
  versions: string[];
  project_count: number;
  projects: PythonPackageProjectRef[];
}

// ── Dependency health types (ADR-051) ─────────────────────────────

export interface CollectionHealthSummary {
  fqcn: string;
  finding_count: number;
  critical: number;
  error: number;
  high: number;
  medium: number;
  low: number;
  info: number;
}

export interface PythonCveSummary {
  rule_id: string;
  level: string;
  message: string;
  occurrence_count: number;
}

export interface DepHealthSummary {
  collection_findings: CollectionHealthSummary[];
  python_cves: PythonCveSummary[];
  suppressed_count: number;
}

// ── Galaxy server types (ADR-045) ────────────────────────────────────

export interface GalaxyServer {
  id: number;
  name: string;
  url: string;
  auth_url: string;
  has_token: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreateGalaxyServerRequest {
  name: string;
  url: string;
  token?: string;
  auth_url?: string;
}

export interface UpdateGalaxyServerRequest {
  name?: string;
  url?: string;
  token?: string;
  auth_url?: string;
}

// ── Rule catalog (ADR-041) ───────────────────────────────────────────

export interface RuleDetail {
  rule_id: string;
  default_severity: string;
  effective_severity: string;
  default_severity_int: number;
  effective_severity_int: number;
  category: string;
  source: string;
  description: string;
  scope: string;
  enabled: boolean;
  enforced: boolean;
  has_override: boolean;
  registered_at: string;
}

export interface RuleOverrideRequest {
  severity_override?: number;
  enabled_override?: boolean;
  enforced?: boolean;
}

export interface RuleStats {
  total: number;
  by_category: Record<string, number>;
  by_source: Record<string, number>;
  override_count: number;
}

// ── Suppression types (ADR-055) ──────────────────────────────────────

export interface SuppressionRecord {
  id: number;
  fingerprint_hash: string;
  fingerprint_mode: string;
  rule_id: string;
  scope: string;
  reason: string;
  created_by: string;
  created_at: string;
}

export interface CreateSuppressionRequest {
  fingerprint_hash?: string;
  fingerprint_mode?: string;
  rule_id: string;
  original_yaml?: string;
  module_fqcn?: string;
  scope?: string;
  reason?: string;
}

// ── Notification types ───────────────────────────────────────────────

export interface NotificationItem {
  id: number;
  type: string;
  title: string;
  message: string;
  variant: 'success' | 'danger' | 'warning' | 'info';
  project_id: string | null;
  scan_id: string | null;
  link: string;
  created_at: string;
  read: boolean;
}
