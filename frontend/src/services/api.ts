import type {
  ActivityDetail,
  ActivitySummary,
  AiAcceptanceEntry,
  AiModelInfo,
  CollectionDetail,
  CollectionSummary,
  CreateGalaxyServerRequest,
  CreateProjectRequest,
  DashboardSummary,
  GalaxyServer,
  HealthStatus,
  PaginatedResponse,
  ProjectDependencies,
  ProjectDetail,
  ProjectRanking,
  ProjectSummary,
  PythonPackageDetail,
  PythonPackageSummary,
  RemediationRateEntry,
  RuleDetail,
  RuleOverrideRequest,
  RuleStats,
  SessionDetail,
  SessionSummary,
  TopViolation,
  TrendPoint,
  UpdateGalaxyServerRequest,
  UpdateProjectRequest,
  ViolationDetail,
} from "../types/api";

const BASE = "/api/v1";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: { Accept: "application/json" },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export function getHealth(): Promise<HealthStatus> {
  return request("/health");
}

export function listSessions(
  limit = 50,
  offset = 0,
): Promise<PaginatedResponse<SessionSummary>> {
  return request(`/sessions?limit=${limit}&offset=${offset}`);
}

export function listActivity(
  limit = 50,
  offset = 0,
  sessionId?: string,
): Promise<PaginatedResponse<ActivitySummary>> {
  let url = `/activity?limit=${limit}&offset=${offset}`;
  if (sessionId) url += `&session_id=${sessionId}`;
  return request(url);
}

export function getActivity(scanId: string): Promise<ActivityDetail> {
  return request(`/activity/${scanId}`);
}

export async function deleteActivity(scanId: string): Promise<void> {
  const res = await fetch(`${BASE}/activity/${scanId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`${res.status}`);
}

export function getSession(sessionId: string): Promise<SessionDetail> {
  return request(`/sessions/${sessionId}`);
}

export function getTopViolations(limit = 20): Promise<TopViolation[]> {
  return request(`/violations/top?limit=${limit}`);
}

export function getSessionTrend(sessionId: string): Promise<TrendPoint[]> {
  return request(`/sessions/${sessionId}/trend`);
}

export function getRemediationRates(limit = 20): Promise<RemediationRateEntry[]> {
  return request(`/stats/remediation-rates?limit=${limit}`);
}

export function getAiAcceptance(): Promise<AiAcceptanceEntry[]> {
  return request(`/stats/ai-acceptance`);
}

export function listAiModels(): Promise<AiModelInfo[]> {
  return request(`/ai/models`);
}

// ── Project API (ADR-037) ────────────────────────────────────────────

export function createProject(body: CreateProjectRequest): Promise<ProjectSummary> {
  return request("/projects", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function listProjects(
  limit = 50,
  offset = 0,
  sortBy = "created_at",
  order = "desc",
): Promise<PaginatedResponse<ProjectSummary>> {
  return request(`/projects?limit=${limit}&offset=${offset}&sort_by=${sortBy}&order=${order}`);
}

export function getProject(projectId: string): Promise<ProjectDetail> {
  return request(`/projects/${encodeURIComponent(projectId)}`);
}

export function updateProject(
  projectId: string,
  body: UpdateProjectRequest,
): Promise<ProjectSummary> {
  return request(`/projects/${encodeURIComponent(projectId)}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deleteProject(projectId: string): Promise<void> {
  const res = await fetch(`${BASE}/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`${res.status}`);
}

export function listProjectActivity(
  projectId: string,
  limit = 50,
  offset = 0,
): Promise<PaginatedResponse<ActivitySummary>> {
  return request(`/projects/${encodeURIComponent(projectId)}/activity?limit=${limit}&offset=${offset}`);
}

export function listProjectViolations(
  projectId: string,
  limit = 50,
  offset = 0,
  severity?: string,
  ruleId?: string,
): Promise<ViolationDetail[]> {
  let url = `/projects/${encodeURIComponent(projectId)}/violations?limit=${limit}&offset=${offset}`;
  if (severity) url += `&severity=${severity}`;
  if (ruleId) url += `&rule_id=${ruleId}`;
  return request(url);
}

export function getProjectTrend(
  projectId: string,
  limit = 20,
): Promise<TrendPoint[]> {
  return request(`/projects/${encodeURIComponent(projectId)}/trend?limit=${limit}`);
}

export function getDashboardSummary(): Promise<DashboardSummary> {
  return request("/dashboard/summary");
}

export function getDashboardRankings(
  sortBy = "health_score",
  order = "desc",
  limit = 10,
): Promise<ProjectRanking[]> {
  return request(`/dashboard/rankings?sort_by=${sortBy}&order=${order}&limit=${limit}`);
}

// ── Dependencies (ADR-040) ─────────────────────────────────────────────

export function getProjectDependencies(projectId: string): Promise<ProjectDependencies> {
  return request(`/projects/${encodeURIComponent(projectId)}/dependencies`);
}

// ── ContentGraph visualization ─────────────────────────────────────────

export interface GraphData {
  version: number;
  nodes: Array<{ id: string; data: Record<string, unknown> }>;
  edges: Array<{ source: string; target: string; edge_type?: string; position?: number }>;
  execution_edges?: Array<{ source: string; target: string }>;
}

export function getProjectGraph(projectId: string): Promise<GraphData> {
  return request(`/projects/${encodeURIComponent(projectId)}/graph`);
}

export function listCollections(
  limit = 200,
  offset = 0,
): Promise<CollectionSummary[]> {
  return request(`/collections?limit=${limit}&offset=${offset}`);
}

export function getCollectionDetail(fqcn: string): Promise<CollectionDetail> {
  return request(`/collections/${encodeURIComponent(fqcn)}`);
}

export function listPythonPackages(
  limit = 200,
  offset = 0,
): Promise<PythonPackageSummary[]> {
  return request(`/python-packages?limit=${limit}&offset=${offset}`);
}

export function getPythonPackageDetail(name: string): Promise<PythonPackageDetail> {
  return request(`/python-packages/${encodeURIComponent(name)}`);
}

// ── Galaxy server settings (ADR-045) ────────────────────────────────────

export function listGalaxyServers(): Promise<GalaxyServer[]> {
  return request("/settings/galaxy-servers");
}

export function createGalaxyServer(body: CreateGalaxyServerRequest): Promise<GalaxyServer> {
  return request("/settings/galaxy-servers", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export function updateGalaxyServer(
  serverId: number,
  body: UpdateGalaxyServerRequest,
): Promise<GalaxyServer> {
  return request(`/settings/galaxy-servers/${serverId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function deleteGalaxyServer(serverId: number): Promise<void> {
  const res = await fetch(`${BASE}/settings/galaxy-servers/${serverId}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`${res.status}`);
}

// ── Feedback (POC) ─────────────────────────────────────────────────────

export function getFeedbackEnabled(): Promise<{ enabled: boolean }> {
  return request("/feedback/enabled");
}

// ── Rule catalog (ADR-041) ─────────────────────────────────────────────

/** Gateway ``RuleListItem`` / ``RuleDetailOut`` JSON (resolved_* + nested override). */
interface RuleApiRow {
  rule_id: string;
  category: string;
  source: string;
  description: string;
  scope: number;
  default_severity: number;
  default_severity_label: string;
  resolved_severity: number;
  resolved_severity_label: string;
  enabled: boolean;
  resolved_enabled: boolean;
  registered_at: string;
  override: {
    severity_override: number | null;
    enabled_override: boolean | null;
    enforced: boolean;
    updated_at: string;
  } | null;
}

function mapRuleApiToDetail(r: RuleApiRow): RuleDetail {
  return {
    rule_id: r.rule_id,
    default_severity: r.default_severity_label,
    effective_severity: r.resolved_severity_label,
    category: r.category,
    source: r.source,
    description: r.description,
    scope: String(r.scope),
    enabled: r.resolved_enabled,
    enforced: r.override?.enforced ?? false,
    has_override: r.override != null,
    registered_at: r.registered_at,
  };
}

export function listRules(params?: {
  category?: string;
  source?: string;
  enabled_only?: boolean;
}): Promise<RuleDetail[]> {
  const sp = new URLSearchParams();
  if (params?.category) sp.set("category", params.category);
  if (params?.source) sp.set("source", params.source);
  if (params?.enabled_only === true) sp.set("enabled_only", "true");
  const q = sp.toString();
  return request<RuleApiRow[]>(`/rules${q ? `?${q}` : ""}`).then((rows) => rows.map(mapRuleApiToDetail));
}

export function getRule(ruleId: string): Promise<RuleDetail> {
  return request<RuleApiRow>(`/rules/${encodeURIComponent(ruleId)}`).then(mapRuleApiToDetail);
}

export async function updateRuleConfig(
  ruleId: string,
  config: RuleOverrideRequest,
): Promise<void> {
  const res = await fetch(`${BASE}/rules/${encodeURIComponent(ruleId)}/config`, {
    method: "PUT",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(config),
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
}

export async function deleteRuleConfig(ruleId: string): Promise<void> {
  const res = await fetch(`${BASE}/rules/${encodeURIComponent(ruleId)}/config`, {
    method: "DELETE",
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
}

export function getRuleStats(): Promise<RuleStats> {
  return request("/rules/stats");
}
