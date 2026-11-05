import {
  normalizeApmeAiProviders,
  mergeApmeAiProviderLists,
  type ApmeAiEnginesResponse,
  type ApmeAiProviderConfigureRequest,
} from "./aiProviders.js";
import type {
  ActivityDetail,
  ActivitySummary,
  AiAcceptanceEntry,
  AiModelInfo,
  CollectionDetail,
  CollectionProjectRef,
  CollectionSummary,
  CreateGalaxyServerRequest,
  CreateProjectRequest,
  CreateSuppressionRequest,
  DashboardSummary,
  DepHealthSummary,
  GalaxyServer,
  HealthStatus,
  NotificationItem,
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
  SubmitResponse,
  SuppressionRecord,
  TopViolation,
  TrendPoint,
  UpdateGalaxyServerRequest,
  UpdateProjectRequest,
  ViolationDetail,
} from "./types.js";
import {
  apmeApiUrl,
  getApmeApiAdapter,
  type ApmeApiAdapter,
} from "./transport.js";

// NOTE (#447): adapter resolution is injectable per call. `apiFetch` and
// `request` take an optional trailing `ApmeApiAdapter` (module default
// otherwise). All exported helpers accept the same optional adapter for
// React callers under `ApmeApiProvider`; pass `useApmeApi()` from components.
// Module defaults remain for non-React callers and tests (`setApmeApiAdapter()`).

class ApiError extends Error {
  status: number;
  body: string;
  constructor(status: number, body: string) {
    super(`${status}: ${body}`);
    this.status = status;
    this.body = body;
  }
}

/** Convert an API error into UI feedback, preferring useful Gateway details. */
export function apiErrorMessage(
  err: unknown,
  fallback = "Request failed",
): string {
  if (err instanceof ApiError) {
    const body = err.body;
    try {
      const parsed = JSON.parse(body) as {
        detail?: string | Array<{ msg?: string }>;
      };
      const detail = parsed.detail;
      if (typeof detail === "string" && detail.trim()) {
        return detail;
      }
      if (Array.isArray(detail)) {
        const messages = detail
          .map((item) => (typeof item?.msg === "string" ? item.msg : ""))
          .filter(Boolean);
        if (messages.length > 0) {
          return messages.join("; ");
        }
      }
    } catch {
      // Fall through to the raw body when JSON parsing fails.
    }
    if (body.trim()) {
      return body;
    }
  }
  if (err instanceof Error && err.message.trim()) {
    return err.message;
  }
  return fallback;
}

async function apiFetch(
  path: string,
  init?: Omit<RequestInit, "headers"> & { headers?: Record<string, string> },
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<Response> {
  const { fetch: doFetch } = adapter;
  return doFetch(apmeApiUrl(path, adapter), init);
}

async function discardResponse(response: Response): Promise<void> {
  await response.body?.cancel().catch(() => undefined);
}

async function request<T>(
  path: string,
  init?: Omit<RequestInit, "headers"> & { headers?: Record<string, string> },
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<T> {
  const { headers: extraHeaders, ...rest } = init ?? {};
  const res = await apiFetch(
    path,
    {
      ...rest,
      headers: { Accept: "application/json", ...extraHeaders },
    },
    adapter,
  );
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text);
  }
  return res.json() as Promise<T>;
}

/** Fetch Gateway health and reject responses that do not report `status: ok`. */
export function getHealth(
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<HealthStatus> {
  return request<HealthStatus>("/health", undefined, adapter).then((health) => {
    if (!health || typeof health !== "object" || health.status !== "ok")
      throw new Error("Gateway health check did not report ok");
    return health;
  });
}

/** List scan sessions using limit and offset pagination. */
export function listSessions(
  limit = 50,
  offset = 0,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<PaginatedResponse<SessionSummary>> {
  return request(
    `/sessions?limit=${limit}&offset=${offset}`,
    undefined,
    adapter,
  );
}

/** List activity records, optionally restricted to one session. */
export function listActivity(
  limit = 50,
  offset = 0,
  sessionId?: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<PaginatedResponse<ActivitySummary>> {
  let url = `/activity?limit=${limit}&offset=${offset}`;
  if (sessionId) url += `&session_id=${sessionId}`;
  return request(url, undefined, adapter);
}

/** Fetch the activity detail for a scan. */
export function getActivity(
  scanId: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<ActivityDetail> {
  return request(`/activity/${encodeURIComponent(scanId)}`, undefined, adapter);
}

/** Delete an activity record by scan ID. */
export async function deleteActivity(
  scanId: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<void> {
  const res = await apiFetch(
    `/activity/${encodeURIComponent(scanId)}`,
    { method: "DELETE" },
    adapter,
  );
  try {
    if (!res.ok) throw new Error(`${res.status}`);
  } finally {
    await discardResponse(res);
  }
}

/** Submit an activity to its project's operation workflow. */
export function submitActivity(
  projectId: string,
  activityId: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<SubmitResponse> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/operation/submit`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ activity_id: activityId }),
    },
    adapter,
  );
}

/** Fetch one session and its current scan state. */
export function getSession(
  sessionId: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<SessionDetail> {
  return request(`/sessions/${sessionId}`, undefined, adapter);
}

/** Fetch the highest-frequency violations for the selected scope. */
export function getTopViolations(
  limit = 20,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<TopViolation[]> {
  return request(`/violations/top?limit=${limit}`, undefined, adapter);
}

/** Fetch the time series used by the session trend view. */
export function getSessionTrend(
  sessionId: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<TrendPoint[]> {
  return request(`/sessions/${sessionId}/trend`, undefined, adapter);
}

/** Fetch remediation success and completion rates. */
export function getRemediationRates(
  limit = 20,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<RemediationRateEntry[]> {
  return request(`/stats/remediation-rates?limit=${limit}`, undefined, adapter);
}

/** Fetch AI proposal acceptance metrics. */
export function getAiAcceptance(
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<AiAcceptanceEntry[]> {
  return request(`/stats/ai-acceptance`, undefined, adapter);
}

/** List models available from configured AI providers. */
export function listAiModels(
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<AiModelInfo[]> {
  return request(`/ai/models`, undefined, adapter);
}

/** Fetch provider settings, returning only public metadata because config may contain secrets. */
export async function getAiProviderSettings(
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
) {
  const [providers, config] = await Promise.all([
    request<unknown>("/ai/providers", undefined, adapter),
    request<unknown>("/ai/config", undefined, adapter),
  ]);
  return mergeApmeAiProviderLists(
    normalizeApmeAiProviders(providers),
    normalizeApmeAiProviders(config),
  );
}

/** List AI engines supported by the Gateway. */
export function getAiEngines(
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<ApmeAiEnginesResponse> {
  return request("/ai/engines", undefined, adapter);
}

function configRecord(raw: unknown): Record<string, unknown> {
  if (!raw || typeof raw !== "object" || Array.isArray(raw))
    throw new Error("Invalid AI configuration response");
  const root = "config" in raw ? raw.config : raw;
  if (!root || typeof root !== "object" || Array.isArray(root))
    throw new Error("Invalid AI configuration response");
  return root as Record<string, unknown>;
}

/** Existing Portal configure + model merge, with visible partial-save failures. */
export async function saveAiProvider(
  id: string,
  configure: ApmeAiProviderConfigureRequest,
  models: string[],
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<void> {
  if (!/^[A-Za-z0-9_-]+$/.test(id))
    throw new Error(
      "Provider name must contain only letters, numbers, underscores or hyphens",
    );
  const configured = await apiFetch(
    `/ai/provider/${encodeURIComponent(id)}/configure`,
    {
      method: "POST",
      body: JSON.stringify(configure),
      headers: { "Content-Type": "application/json" },
    },
    adapter,
  );
  try {
    if (!configured.ok)
      throw new ApiError(configured.status, await configured.text());
  } finally {
    await discardResponse(configured);
  }
  try {
    const config = configRecord(
      await request<unknown>("/ai/config", undefined, adapter),
    );
    const current = config.providers;
    if (
      current != null &&
      (typeof current !== "object" || Array.isArray(current))
    )
      throw new Error("Invalid provider configuration");
    const providers = { ...(current as Record<string, unknown> | undefined) };
    const previous = providers[id];
    const prior =
      previous && typeof previous === "object" && !Array.isArray(previous)
        ? previous
        : {};
    const modelOptions = (prior as Record<string, unknown>).models;
    const retainedModels =
      modelOptions &&
      typeof modelOptions === "object" &&
      !Array.isArray(modelOptions)
        ? (modelOptions as Record<string, unknown>)
        : {};
    Object.defineProperty(providers, id, {
      enumerable: true,
      configurable: true,
      writable: true,
      value: {
        ...prior,
        engine: configure.engine,
        models: Object.fromEntries(
          models.map((model) => [
            model,
            Object.prototype.hasOwnProperty.call(retainedModels, model)
              ? retainedModels[model]
              : {},
          ]),
        ),
        ...(configure.baseUrl ? { base_url: configure.baseUrl } : {}),
      },
    });
    await request(
      "/ai/config",
      {
        method: "POST",
        body: JSON.stringify({
          location: "user",
          config: { ...config, providers },
        }),
        headers: { "Content-Type": "application/json" },
      },
      adapter,
    );
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw Object.assign(
      new Error(
        "Provider credentials were saved, but model configuration could not be saved. Refresh and retry the model update.",
      ),
      { cause: error },
    );
  }
}

/** Delete a configured AI provider and release its response body. */
export async function deleteAiProvider(
  id: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<void> {
  const response = await apiFetch(
    `/ai/provider/${encodeURIComponent(id)}`,
    { method: "DELETE" },
    adapter,
  );
  try {
    if (!response.ok)
      throw new ApiError(response.status, await response.text());
  } finally {
    await discardResponse(response);
  }
}

// ── Project API (ADR-037) ────────────────────────────────────────────

/** Register a repository as an APME project. */
export function createProject(
  body: CreateProjectRequest,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<ProjectSummary> {
  return request(
    "/projects",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    adapter,
  );
}

/** List registered projects with optional pagination and filters. */
export function listProjects(
  limit = 50,
  offset = 0,
  sortBy = "created_at",
  order = "desc",
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<PaginatedResponse<ProjectSummary>> {
  return request(
    `/projects?limit=${limit}&offset=${offset}&sort_by=${sortBy}&order=${order}`,
    undefined,
    adapter,
  );
}

/** Fetch one project by its catalog ID. */
export function getProject(
  projectId: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<ProjectDetail> {
  return request(
    `/projects/${encodeURIComponent(projectId)}`,
    undefined,
    adapter,
  );
}

/** Update the repository settings for an existing project. */
export function updateProject(
  projectId: string,
  body: UpdateProjectRequest,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<ProjectSummary> {
  return request(
    `/projects/${encodeURIComponent(projectId)}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    adapter,
  );
}

/** Remove a project and consume the Gateway's mutation response. */
export async function deleteProject(
  projectId: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<void> {
  const res = await apiFetch(
    `/projects/${encodeURIComponent(projectId)}`,
    { method: "DELETE" },
    adapter,
  );
  if (!res.ok) throw new ApiError(res.status, await res.text());
  await res.text();
}

/** List activity records associated with a project. */
export function listProjectActivity(
  projectId: string,
  limit = 50,
  offset = 0,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<PaginatedResponse<ActivitySummary>> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/activity?limit=${limit}&offset=${offset}`,
    undefined,
    adapter,
  );
}

/** List violations found in a project's scans. */
export function listProjectViolations(
  projectId: string,
  limit = 50,
  offset = 0,
  severity?: string,
  ruleId?: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
  signal?: AbortSignal,
): Promise<ViolationDetail[]> {
  const query = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (severity) query.set("severity", severity);
  if (ruleId) query.set("rule_id", ruleId);
  return request(
    `/projects/${encodeURIComponent(projectId)}/violations?${query}`,
    signal ? { signal } : undefined,
    adapter,
  );
}

/** Fetch scan and remediation trends for one project. */
export function getProjectTrend(
  projectId: string,
  limit = 20,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<TrendPoint[]> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/trend?limit=${limit}`,
    undefined,
    adapter,
  );
}

/** Fetch aggregate counts and health for the dashboard. */
export function getDashboardSummary(
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<DashboardSummary> {
  return request("/dashboard/summary", undefined, adapter);
}

export interface ActiveOperation {
  operation_id: string;
  project_id: string;
  project_name: string;
  scan_id: string;
  status: string;
  scan_type: string;
  started_at: string;
}

/** List currently running Gateway operations. */
export function getActiveOperations(
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<ActiveOperation[]> {
  return request("/operations/active", undefined, adapter);
}

/** Fetch project rankings for the dashboard. */
export function getDashboardRankings(
  sortBy = "health_score",
  order = "desc",
  limit = 10,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<ProjectRanking[]> {
  return request(
    `/dashboard/rankings?sort_by=${sortBy}&order=${order}&limit=${limit}`,
    undefined,
    adapter,
  );
}

// ── Dependencies (ADR-040) ─────────────────────────────────────────────

/** Fetch dependency inventory for a project. */
export function getProjectDependencies(
  projectId: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<ProjectDependencies> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/dependencies`,
    undefined,
    adapter,
  );
}

// ── ContentGraph visualization ─────────────────────────────────────────

export interface GraphData {
  version: number;
  nodes: Array<{ id: string; data: Record<string, unknown> }>;
  edges: Array<{
    source: string;
    target: string;
    edge_type?: string;
    position?: number;
  }>;
  execution_edges?: Array<{ source: string; target: string }>;
}

/** Fetch the content graph for a project. */
export function getProjectGraph(
  projectId: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<GraphData> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/graph`,
    undefined,
    adapter,
  );
}

/** Download a project's software bill of materials. */
export async function getProjectSbom(
  projectId: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<Blob> {
  const res = await apiFetch(
    `/projects/${encodeURIComponent(projectId)}/sbom`,
    { headers: { Accept: "application/vnd.cyclonedx+json" } },
    adapter,
  );
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status}: ${text}`);
  }
  return res.blob();
}

/** List Ansible collections known to the Gateway. */
export function listCollections(
  limit = 200,
  offset = 0,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<CollectionSummary[]> {
  return request(
    `/collections?limit=${limit}&offset=${offset}`,
    undefined,
    adapter,
  );
}

/** Fetch metadata and project usage for one collection. */
export function getCollectionDetail(
  fqcn: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<CollectionDetail> {
  return request(
    `/collections/${encodeURIComponent(fqcn)}`,
    undefined,
    adapter,
  );
}

/** List projects that use a collection. */
export function listCollectionProjects(
  fqcn: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<CollectionProjectRef[]> {
  return request(
    `/collections/${encodeURIComponent(fqcn)}/projects`,
    undefined,
    adapter,
  );
}

/** List Python packages known to the Gateway. */
export function listPythonPackages(
  limit = 200,
  offset = 0,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<PythonPackageSummary[]> {
  return request(
    `/python-packages?limit=${limit}&offset=${offset}`,
    undefined,
    adapter,
  );
}

/** Fetch metadata and project usage for one Python package. */
export function getPythonPackageDetail(
  name: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<PythonPackageDetail> {
  return request(
    `/python-packages/${encodeURIComponent(name)}`,
    undefined,
    adapter,
  );
}

// ── Dependency health (ADR-051) ─────────────────────────────────────────

/** Fetch aggregate dependency health counts. */
export function getDepHealthSummary(
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<DepHealthSummary> {
  return request("/dep-health", undefined, adapter);
}

/** Fetch dependency health findings for one project. */
export function getProjectDepHealth(
  projectId: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<DepHealthSummary> {
  return request(
    `/projects/${encodeURIComponent(projectId)}/dep-health`,
    undefined,
    adapter,
  );
}

// ── Galaxy server settings (ADR-045) ────────────────────────────────────

/** List configured Galaxy servers. */
export function listGalaxyServers(
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<GalaxyServer[]> {
  return request("/settings/galaxy-servers", undefined, adapter);
}

/** Add a Galaxy server configuration. */
export function createGalaxyServer(
  body: CreateGalaxyServerRequest,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<GalaxyServer> {
  return request(
    "/settings/galaxy-servers",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    adapter,
  );
}

/** Update a Galaxy server configuration. */
export function updateGalaxyServer(
  serverId: number,
  body: UpdateGalaxyServerRequest,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<GalaxyServer> {
  return request(
    `/settings/galaxy-servers/${serverId}`,
    {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    adapter,
  );
}

/** Delete a Galaxy server and dispose of the mutation response body. */
export async function deleteGalaxyServer(
  serverId: number,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<void> {
  const res = await apiFetch(
    `/settings/galaxy-servers/${serverId}`,
    { method: "DELETE" },
    adapter,
  );
  try {
    if (!res.ok) throw new Error(`${res.status}`);
  } finally {
    await discardResponse(res);
  }
}

// ── Suppressions (ADR-055) ──────────────────────────────────────────────

/** List suppression records, optionally filtered by scope. */
export function listSuppressions(
  scope?: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<SuppressionRecord[]> {
  const params = scope ? `?scope=${encodeURIComponent(scope)}` : "";
  return request(`/suppressions${params}`, undefined, adapter);
}

/** Create a suppression record. */
export function createSuppression(
  body: CreateSuppressionRequest,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<SuppressionRecord> {
  return request(
    "/suppressions",
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    },
    adapter,
  );
}

/** Delete a suppression record by its numeric ID. */
export async function deleteSuppression(
  id: number,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<void> {
  const res = await apiFetch(
    `/suppressions/${id}`,
    { method: "DELETE" },
    adapter,
  );
  try {
    if (!res.ok) {
      const text = await res.text();
      throw new ApiError(res.status, text);
    }
  } finally {
    await discardResponse(res);
  }
}

// ── Feedback (POC) ─────────────────────────────────────────────────────

/** Check whether user feedback collection is enabled. */
export function getFeedbackEnabled(
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<{ enabled: boolean }> {
  return request("/feedback/enabled", undefined, adapter);
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
    default_severity_int: r.default_severity ?? 1,
    effective_severity_int: r.resolved_severity ?? 1,
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

/** List rules with their configured severity and enablement state. */
export function listRules(
  params?: {
    category?: string;
    source?: string;
    enabled_only?: boolean;
  },
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<RuleDetail[]> {
  const sp = new URLSearchParams();
  if (params?.category) sp.set("category", params.category);
  if (params?.source) sp.set("source", params.source);
  if (params?.enabled_only === true) sp.set("enabled_only", "true");
  const q = sp.toString();
  return request<RuleApiRow[]>(
    `/rules${q ? `?${q}` : ""}`,
    undefined,
    adapter,
  ).then((rows) => rows.map(mapRuleApiToDetail));
}

/** Fetch a rule and its current override configuration. */
export function getRule(
  ruleId: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<RuleDetail> {
  return request<RuleApiRow>(
    `/rules/${encodeURIComponent(ruleId)}`,
    undefined,
    adapter,
  ).then(mapRuleApiToDetail);
}

/** Save an override for one rule. */
export async function updateRuleConfig(
  ruleId: string,
  config: RuleOverrideRequest,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<void> {
  const res = await apiFetch(
    `/rules/${encodeURIComponent(ruleId)}/config`,
    {
      method: "PUT",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(config),
    },
    adapter,
  );
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text);
  }
  await res.text();
}

/** Remove the override for one rule. */
export async function deleteRuleConfig(
  ruleId: string,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<void> {
  const res = await apiFetch(
    `/rules/${encodeURIComponent(ruleId)}/config`,
    { method: "DELETE", headers: { Accept: "application/json" } },
    adapter,
  );
  if (!res.ok) {
    const text = await res.text();
    throw new ApiError(res.status, text);
  }
  await res.text();
}

/** Fetch rule statistics for the current reporting period. */
export function getRuleStats(
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<RuleStats> {
  return request("/rules/stats", undefined, adapter);
}

// ── Notifications ───────────────────────────────────────────────────────

/** List notifications, optionally limited to unread entries. */
export function listNotifications(
  limit = 50,
  offset = 0,
  unreadOnly = false,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<PaginatedResponse<NotificationItem>> {
  let url = `/notifications?limit=${limit}&offset=${offset}`;
  if (unreadOnly) url += "&unread_only=true";
  return request(url, undefined, adapter);
}

/** Mark a notification as read. */
export async function markNotificationRead(
  id: number,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<void> {
  const res = await apiFetch(
    `/notifications/${id}/read`,
    { method: "PATCH" },
    adapter,
  );
  try {
    if (!res.ok) throw new Error(`${res.status}`);
  } finally {
    await discardResponse(res);
  }
}

/** Mark every notification as read. */
export async function markAllNotificationsRead(
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<void> {
  const res = await apiFetch(
    `/notifications/read-all`,
    { method: "POST" },
    adapter,
  );
  try {
    if (!res.ok) throw new Error(`${res.status}`);
  } finally {
    await discardResponse(res);
  }
}

/** Delete one notification. */
export async function deleteNotification(
  id: number,
  adapter: ApmeApiAdapter = getApmeApiAdapter(),
): Promise<void> {
  const res = await apiFetch(
    `/notifications/${id}`,
    { method: "DELETE" },
    adapter,
  );
  try {
    if (!res.ok) throw new Error(`${res.status}`);
  } finally {
    await discardResponse(res);
  }
}
