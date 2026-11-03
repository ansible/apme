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

export type Severity =
  "blocker" | "critical" | "high" | "medium" | "low" | "info";

export type RemediationClass = 1 | 2 | 3 | 9; // 1=auto, 2=assisted, 3=manual, 9=none

export interface Violation {
  id: number;
  rule_id: string;
  level: string; // 'blocker', 'critical', 'high', 'medium', 'low', 'info'
  message: string;
  file: string;
  line: number;
  path?: string;
  /** ContentGraph NodeType (task, block, play, …); empty when not graph-backed. */
  node_type?: string;
  remediation_class: RemediationClass;
  remediation_resolution?: number;
  scope?: string; // 'task', 'playbook', 'inventory', 'collection'
  category?: string; // 'lint', 'modernize', 'risk', 'secrets', 'dependencies'
  validator_source: string; // 'native', 'opa', 'ansible', 'gitleaks', 'dep_audit', 'collection_health'
  original_yaml?: string;
  fixed_yaml?: string;
  co_fixes?: string[];
  node_line_start?: number;
  ai_reason?: string;
  ai_suggestion?: string;
  /** True when an active suppression matches this violation (ADR-055). */
  suppressed?: boolean;
}

export interface ActiveOperationSummary {
  operation_id: string;
  status: string;
  scan_type?: string;
  started_at?: string;
}

/** Latest scan summary from gateway ProjectDetail.latest_scan. */
export interface LatestScanSummary {
  scan_id: string;
  scan_type: "check" | "remediate";
  total_violations: number;
  fixable: number;
  ai_candidate: number;
  ai_proposed?: number;
  ai_declined?: number;
  manual_review: number;
  remediated_count: number;
}

export interface Project {
  id: string;
  name: string;
  repo_url: string;
  branch: string;
  created_at: string;
  health_score: number;
  total_violations: number;
  violation_trend?: string;
  scan_count: number;
  last_scanned_at?: string;
  scm_provider?: string;
  has_scm_token: boolean;
  last_scanned_commit?: string;
  has_new_commits: boolean;
  active_operation?: ActiveOperationSummary | string | null;
  latest_scan?: LatestScanSummary | null;
  /** Raw severity counts from gateway ProjectDetail / list API. */
  severity_breakdown?: Record<string, number>;
  // Computed on frontend for display (mock / legacy)
  violationCounts?: {
    critical: number;
    error: number;
    high: number;
    medium: number;
    low: number;
    info: number;
  };
}

export interface Rule {
  id: string;
  name: string;
  description: string;
  severity: Severity;
  /** Catalog default severity before overrides. */
  defaultSeverity?: Severity;
  category: string;
  remediationClass: RemediationClass;
  enabled: boolean;
  /** Validator source from gateway (native, opa, ansible, gitleaks). */
  source?: string;
  /** Override: enforce despite inline ignores. */
  enforced?: boolean;
  /** True when any portal override is active for this rule. */
  hasOverride?: boolean;
}

/** Payload for PUT /rules/{rule_id}/config (partial update). */
export interface RuleConfigUpdate {
  severity_override?: number | null;
  enabled_override?: boolean | null;
  enforced?: boolean | null;
}

/** Request body for POST /suppressions (ADR-055). */
export interface CreateSuppressionRequest {
  fingerprint_hash?: string;
  fingerprint_mode?: "full" | "rule_only";
  rule_id: string;
  original_yaml?: string;
  module_fqcn?: string;
  scope: string;
  reason?: string;
}

/** Suppression record from gateway. */
export interface Suppression {
  id: number;
  fingerprint_hash: string;
  fingerprint_mode: string;
  rule_id: string;
  scope: string;
  reason: string;
  created_by: string;
  created_at: string;
}

export interface ScanResult {
  projectId: string;
  scanId: string;
  status: "pending" | "running" | "completed" | "failed";
  startedAt?: string;
  completedAt?: string;
  violations?: Violation[];
  healthScore?: number;
}

export interface HealthStatus {
  status: "healthy" | "degraded" | "unhealthy";
  database: string;
  components: Array<{
    name: string;
    status: string;
    address: string;
  }>;
}

export interface ApmeConfig {
  enabled: boolean;
  baseUrl: string;
  checkSSL: boolean;
  /** When true, portal proxies PR creation to the APME gateway (standalone path). */
  publishViaGateway: boolean;
  /**
   * Client timeout for remediation submit/push/PR (ms).
   * Default 300000 (5 minutes). Large remedia pushes often need longer than 30s.
   */
  submitTimeoutMs: number;
  /** Default ansible-core version shown in UI and sent when scans omit a version. */
  targetAnsibleCoreVersion?: string;
  /** Optional path for persisted portal scan-target settings JSON. */
  portalSettingsPath?: string;
}

/** Portal-side APME settings (Quality settings store). */
export interface ApmePortalSettings {
  enableAi: boolean;
  publishViaGateway: boolean;
  targetAnsibleCoreVersion?: string;
  /** Abbenay chat model id (provider/model) for workflow remediate + escalate-ai. */
  defaultAiModelId?: string;
  /**
   * Effective APME Gateway URL (portal-settings override, else app-config).
   * Backend scan/remediate/proxy calls use this, not the frontend origin.
   */
  gatewayBaseUrl?: string;
}

export type ScanTargetSource = "project" | "global" | "config" | "default";

/** Effective scan target for a project (portal API). */
export interface ProjectScanTarget {
  effective: string;
  source: ScanTargetSource;
  globalDefault: string;
  projectOverride?: string;
}

export interface UpdatePortalSettingsRequest {
  targetAnsibleCoreVersion?: string;
  defaultAiModelId?: string | null;
  /** When set, persists a portal override for AI-assisted remediation. */
  enableAi?: boolean;
  /** Set to override app-config; null or empty clears the override. */
  gatewayBaseUrl?: string | null;
}

/** Galaxy / Automation Hub server (ADR-045). Token value is never exposed. */
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
  validate_certs?: boolean | null;
  name: string;
  url: string;
  token?: string;
  auth_url?: string;
}

export interface UpdateGalaxyServerRequest {
  validate_certs?: boolean | null;
  name?: string;
  url?: string;
  token?: string;
  auth_url?: string;
}

export interface UpdateProjectScanTargetRequest {
  targetAnsibleCoreVersion: string | null;
}

export interface ScanTriggerOptions {
  ansibleVersion?: string;
  userIdentity?: { userEntityRef: string; orgEntityRef?: string };
  /** One-time SCM token for private-repo clone (portal integration / user). */
  scmToken?: string;
  /** Per-scan override (portal settings store). */
  enableAi?: boolean;
}

/** Gateway AI service reachability (Abbenay via Primary). */
export interface ApmeAiStatus {
  /** Portal AI gate from Quality settings — scans/remediate send enable_ai when true. */
  enableAi: boolean;
  /**
   * True when inference models are listed, or Abbenay health reports ok.
   * Does not flip true solely because admin config lists models.
   */
  connected: boolean;
  /** Count of live inference models from ListAIModels. */
  modelCount: number;
  /** Models listed in Abbenay admin config (informational; may exceed modelCount). */
  configuredModelCount?: number;
}

/** Engine descriptor from GET /api/v1/ai/engines (Gateway → Abbenay). */
export interface ApmeAiEngineInfo {
  id: string;
  requiresKey: boolean;
  defaultBaseUrl?: string;
  defaultEnvVar?: string;
}

/** Response shape of GET /api/v1/ai/engines. */
export interface ApmeAiEnginesResponse {
  engines: ApmeAiEngineInfo[];
}

/** Origin of an AI provider row in Quality settings. */
export type ApmeAiProviderSource = "managed" | "config";

/** Provider summary returned by the portal catalog proxy — no secrets. */
export interface ApmeAiProviderSummary {
  id: string;
  engine: string;
  /**
   * Real model data only when normalized from GET /apme/ai/config. When
   * normalized from GET /apme/ai/providers, always []. Abbenay's provider
   * registry doesn't carry model IDs. Use mergeApmeAiProviderLists to combine
   * both sources into rows with populated models.
   */
  models: string[];
  /**
   * `managed` = portal / Abbenay file-store (editable).
   * `config` = deploy-time ConfigMap only (read-only).
   */
  source?: ApmeAiProviderSource;
}

/** Model row from portal-side provider discovery (Ollama / OpenAI-compatible APIs). */
export interface DiscoveredAiModel {
  id: string;
  name: string;
  provider: string;
  engine: string;
}

/** Raw shape of GET /api/v1/ai/config from the Gateway. */
export interface ApmeAiConfigResponse {
  config?: {
    providers?: unknown;
  };
  path?: string;
  [key: string]: unknown;
}

/** Request body for POST …/provider/{id}/configure (Abbenay via Gateway ADR-070). */
export interface ApmeAiProviderConfigureRequest {
  engine: string;
  /**
   * Write-only API key. With ``secretStore: "file"`` (Abbenay ≥ v2026.8.6),
   * Abbenay persists the key on the config volume — Gateway does not store it.
   */
  apiKey?: string;
  baseUrl?: string;
  /** Logical Abbenay secret name; defaults to ``{PROVIDER}_API_KEY`` when omitted. */
  secretName?: string;
  /**
   * Abbenay secret backend. Portal uses ``file`` for durable container keys.
   * ``env`` + ``envVarName`` remains for deploy-time secrets.
   */
  secretStore?: "file" | "memory" | "keychain" | "env";
  /** Deploy-time process env var name (sets ``secretStore: env``). */
  envVarName?: string;
  /** @deprecated Prefer ``secretStore``. */
  secretStorage?: "env" | "keychain" | "file" | "memory";
}

function normalizeProviderEntry(p: unknown): ApmeAiProviderSummary {
  if (!p || typeof p !== "object") {
    const id = typeof p === "string" || typeof p === "number" ? String(p) : "";
    return { id, engine: "", models: [] };
  }
  const obj = p as Record<string, unknown>;
  let id = "";
  if (typeof obj.id === "string") {
    id = obj.id;
  } else if (typeof obj.id === "number") {
    id = String(obj.id);
  }
  const engine = typeof obj.engine === "string" ? obj.engine : "";
  let models: string[] = [];
  if (Array.isArray(obj.models)) {
    models = obj.models.filter((m): m is string => typeof m === "string");
  } else if (obj.models && typeof obj.models === "object") {
    models = Object.keys(obj.models as Record<string, unknown>);
  }
  return { id, engine, models };
}

/**
 * Normalise the raw AI providers response from the Gateway into a flat array.
 * Handles: bare array, `{providers: array}`, `{providers: record}`,
 * and `{config: {providers: record}}`. Never exposes secrets.
 */
export function normalizeApmeAiProviders(
  raw: unknown,
): ApmeAiProviderSummary[] {
  if (Array.isArray(raw)) {
    return raw.map((p) => normalizeProviderEntry(p));
  }
  if (raw && typeof raw === "object") {
    const obj = raw as Record<string, unknown>;
    let providersSource: unknown;
    if ("providers" in obj) {
      providersSource = obj.providers;
    } else if (obj.config && typeof obj.config === "object") {
      providersSource = (obj.config as Record<string, unknown>).providers;
    }
    if (Array.isArray(providersSource)) {
      return providersSource.map((p) => normalizeProviderEntry(p));
    }
    if (providersSource && typeof providersSource === "object") {
      return Object.entries(providersSource as Record<string, unknown>).map(
        ([id, val]) =>
          normalizeProviderEntry({
            id,
            ...(typeof val === "object" && val !== null ? val : {}),
          }),
      );
    }
  }
  return [];
}

/**
 * Merge HTTP `/providers` (portal-managed) with `/config` providers (ConfigMap).
 * Managed wins on id collision; config-only rows are tagged read-only.
 */
export function mergeApmeAiProviderLists(
  fromProviders: ApmeAiProviderSummary[],
  fromConfig: ApmeAiProviderSummary[],
): ApmeAiProviderSummary[] {
  const configById = new Map(fromConfig.map((p) => [p.id, p]));
  const managedIds = new Set(fromProviders.map((p) => p.id));

  const managed = fromProviders.map((p) => {
    const cfg = configById.get(p.id);
    return {
      ...p,
      models: p.models.length > 0 ? p.models : (cfg?.models ?? p.models ?? []),
      source: "managed" as const,
    };
  });

  const configOnly = fromConfig
    .filter((p) => !managedIds.has(p.id))
    .map((p) => ({ ...p, source: "config" as const }));

  return [...managed, ...configOnly].sort((a, b) =>
    a.id.localeCompare(b.id, undefined, { sensitivity: "base" }),
  );
}

export interface CreatePullRequestResult {
  pr_url: string;
  branch_name?: string;
  provider?: string;
  commit_sha?: string;
}

/** Request body for gateway SCM submit (ADR-050). */
export interface SubmitRemediationRequest {
  activity_id: string;
  branch_name?: string;
  create_pr?: boolean;
  title?: string;
  body?: string;
  scm_token?: string;
}

/** Response from gateway SCM submit (ADR-050). */
export interface SubmitRemediationResult {
  branch_name: string;
  commit_sha: string;
  pr_url: string | null;
  provider: string;
}

export interface RemediationBundleFile {
  path: string;
  content_base64: string;
}

export interface RemediationBundle {
  activity_id: string;
  project_id: string;
  repo_url: string;
  base_branch: string;
  scm_provider: string;
  branch_name: string;
  title: string;
  body: string;
  files: RemediationBundleFile[];
  pr_url?: string | null;
  fixed_count: number;
  total_violations: number;
}

export interface PushBranchResult {
  branch_name: string;
  provider: string;
  repo_url: string;
}

export interface CreateProjectRequest {
  name: string;
  repo_url: string;
  branch?: string;
  scm_token?: string;
}

export interface Activity {
  scan_id: string;
  session_id: string;
  project_path: string;
  source: string;
  created_at: string;
  scan_type: "check" | "remediate";
  total_violations: number;
  fixable: number;
  ai_candidate: number;
  ai_proposed: number;
  ai_declined: number;
  ai_accepted: number;
  manual_review: number;
  remediated_count: number;
  pr_url?: string | null;
  branch_name?: string | null;
  commit_sha?: string | null;
}

/** Summary row persisted for an AI proposal on a scan (gateway activity detail). */
export interface ActivityProposalSummary {
  id: number;
  proposal_id: string;
  rule_id: string;
  file: string;
  tier: number;
  confidence: number;
  status: string;
}

/** Full scan run detail from gateway GET /activity/{scan_id}. */
export interface ActivityDetail extends Activity {
  project_id?: string;
  violations: Violation[];
  proposals: ActivityProposalSummary[];
}

export interface CollectionRef {
  fqcn: string;
  version: string;
  source: string;
  license?: string;
  supplier?: string;
}

export interface PythonPackageRef {
  name: string;
  version: string;
  license?: string;
  supplier?: string;
}

/** Project dependency manifest from gateway GET /projects/{id}/dependencies (ADR-040). */
export interface ProjectDependencies {
  ansible_core_version: string;
  collections: CollectionRef[];
  python_packages: PythonPackageRef[];
  requirements_files: string[];
  dependency_tree: string;
}

export interface Proposal {
  id: string;
  violation_id: number;
  rule_id: string;
  file: string;
  line: number;
  original_yaml: string;
  fixed_yaml: string;
  status: "pending" | "accepted" | "declined";
  /** Portal / mock field; gateway sends `explanation` (mapped on ingest). */
  ai_reason?: string;
  /** Gateway remediation tier: 1 = deterministic, 2+ = AI-assisted. */
  tier?: number;
  confidence?: number;
  explanation?: string;
  diff_hunk?: string;
  suggestion?: string;
}

export interface OperationProgressEntry {
  phase: string;
  message: string;
  timestamp: string;
  progress?: number | null;
  level?: number | null;
}

export interface OperationState {
  operation_id: string;
  project_id: string;
  scan_id?: string;
  status: string;
  scan_type?: string;
  started_at?: string;
  phase?: string;
  progress_pct?: number;
  latest_message?: string;
  progress?: OperationProgressEntry[];
  proposals?: Proposal[];
  error?: string;
  result?: {
    total_violations: number;
    fixable: number;
    remediated: number;
    remediated_count?: number;
    fixed_violations?: Array<{
      rule_id: string;
      file: string;
      line?: number | null;
      message?: string;
      path?: string;
      severity?: string;
    }>;
    patches?: Array<{ file: string; diff: string }>;
  };
}
