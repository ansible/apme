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

import {
  createApiRef,
  DiscoveryApi,
  FetchApi,
} from "@backstage/core-plugin-api";
import type {
  Project,
  Violation,
  Rule,
  ScanResult,
  HealthStatus,
  CreateProjectRequest,
  Activity,
  ActivityDetail,
  OperationState,
  CreatePullRequestResult,
  SubmitRemediationResult,
  ApmePortalSettings,
  ApmeAiStatus,
  ApmeAiProviderSummary,
  ApmeAiProviderConfigureRequest,
  ApmeAiEnginesResponse,
  ProjectDependencies,
  RuleConfigUpdate,
  CreateSuppressionRequest,
  Suppression,
  ProjectScanTarget,
  UpdatePortalSettingsRequest,
  UpdateProjectScanTargetRequest,
  ScanTriggerOptions,
  GalaxyServer,
  CreateGalaxyServerRequest,
  UpdateGalaxyServerRequest,
} from "../../common/types";
import { coerceRuleResponse, type GatewayRuleRow } from "../utils/gatewayRules";

export interface ApmeScmRequestOptions {
  scmToken?: string;
  branchName?: string;
}

export interface ApmeViolationsOptions {
  limit?: number;
  offset?: number;
}

export interface ApmeApi {
  getHealth(): Promise<HealthStatus>;
  getPortalSettings(): Promise<ApmePortalSettings>;
  updatePortalSettings(
    body: UpdatePortalSettingsRequest,
  ): Promise<ApmePortalSettings>;
  getProjectScanTarget(projectId: string): Promise<ProjectScanTarget>;
  updateProjectScanTarget(
    projectId: string,
    body: UpdateProjectScanTargetRequest,
  ): Promise<ProjectScanTarget>;
  getAiStatus(): Promise<ApmeAiStatus>;
  /** Inference models from Primary (ListAIModels) — not the Abbenay admin providers list. */
  getAiModels(): Promise<Array<{ id: string; provider: string; name: string }>>;
  getAiConfig(): Promise<unknown>;
  updateAiConfig(body: unknown): Promise<unknown>;
  getAiProviders(): Promise<ApmeAiProviderSummary[]>;
  /** Engine descriptors from Gateway → Abbenay GET /api/v1/ai/engines. */
  getAiEngines(): Promise<ApmeAiEnginesResponse>;
  configureAiProvider(
    id: string,
    body: ApmeAiProviderConfigureRequest,
  ): Promise<unknown>;
  deleteAiProvider(id: string): Promise<void>;
  getProjects(): Promise<Project[]>;
  getProject(projectId: string): Promise<Project>;
  getProjectByRepoUrl(
    repoUrl: string,
    branch?: string,
  ): Promise<Project | null>;
  getViolations(
    projectId: string,
    options?: ApmeViolationsOptions,
  ): Promise<Violation[]>;
  getProjectDependencies(projectId: string): Promise<ProjectDependencies>;
  getRules(): Promise<Rule[]>;
  updateRuleConfig(ruleId: string, body: RuleConfigUpdate): Promise<Rule>;
  deleteRuleConfig(ruleId: string): Promise<void>;
  createSuppression(body: CreateSuppressionRequest): Promise<Suppression>;
  deleteSuppression(suppressionId: number): Promise<void>;
  getSuppressions(scope?: string): Promise<Suppression[]>;
  listGalaxyServers(): Promise<GalaxyServer[]>;
  createGalaxyServer(body: CreateGalaxyServerRequest): Promise<GalaxyServer>;
  updateGalaxyServer(
    serverId: number,
    body: UpdateGalaxyServerRequest,
  ): Promise<GalaxyServer>;
  deleteGalaxyServer(serverId: number): Promise<void>;
  triggerScan(
    projectId: string,
    options?: ScanTriggerOptions,
  ): Promise<ScanResult>;
  createProject(request: CreateProjectRequest): Promise<Project>;
  validateRepoBranch(repoUrl: string, branch: string): Promise<void>;
  deleteProject(projectId: string): Promise<void>;
  getActivity(projectId: string): Promise<Activity[]>;
  getActivityDetail(activityId: string): Promise<ActivityDetail>;
  getOperationState(projectId: string): Promise<OperationState | null>;
  triggerRemediate(
    projectId: string,
    violationIds?: number[],
  ): Promise<ScanResult>;
  approveProposals(projectId: string, proposalIds: string[]): Promise<void>;
  submitRemediation(
    projectId: string,
    activityId: string,
    options?: ApmeScmRequestOptions & { createPr?: boolean },
  ): Promise<SubmitRemediationResult>;
  pushRemediationBranch(
    projectId: string,
    activityId: string,
    options?: ApmeScmRequestOptions,
  ): Promise<SubmitRemediationResult>;
  createPullRequest(
    projectId: string,
    activityId: string,
    options?: ApmeScmRequestOptions,
  ): Promise<CreatePullRequestResult>;
}

export const apmeApiRef = createApiRef<ApmeApi>({ id: "plugin.apme.api" });

export interface ApmeApiClientOptions {
  discoveryApi: DiscoveryApi;
  fetchApi: FetchApi;
  /** Timeout for remediation submit/push/PR. Defaults to 5 minutes. */
  submitTimeoutMs?: number;
}

export class ApmeApiClient implements ApmeApi {
  private readonly discoveryApi: DiscoveryApi;
  private readonly fetchApi: FetchApi;
  private readonly submitTimeoutMs: number;

  constructor(options: ApmeApiClientOptions) {
    this.discoveryApi = options.discoveryApi;
    this.fetchApi = options.fetchApi;
    this.submitTimeoutMs =
      options.submitTimeoutMs !== undefined &&
      Number.isFinite(options.submitTimeoutMs) &&
      options.submitTimeoutMs > 0
        ? Math.floor(options.submitTimeoutMs)
        : 300_000;
  }

  private async getBaseUrl(): Promise<string> {
    return `${await this.discoveryApi.getBaseUrl("catalog")}/apme`;
  }

  private async fetch<T>(
    endpoint: string,
    options?: RequestInit,
    fetchOptions?: { notFoundReturnsNull?: boolean; timeoutMs?: number },
  ): Promise<T> {
    const baseUrl = await this.getBaseUrl();
    const url = `${baseUrl}${endpoint}`;
    const controller = new AbortController();
    const timeoutMs = fetchOptions?.timeoutMs ?? 30_000;
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    let response: Response;
    try {
      response = await this.fetchApi.fetch(url, {
        ...options,
        headers: {
          "Content-Type": "application/json",
          ...options?.headers,
        },
        signal: controller.signal,
      });
    } catch (err) {
      const name = err instanceof Error ? err.name : "";
      const message = err instanceof Error ? err.message : String(err);
      if (name === "AbortError" || /aborted/i.test(message)) {
        throw new Error(`APME API request timed out or was aborted: ${url}`);
      }
      if (err instanceof TypeError || /Failed to fetch/i.test(message)) {
        throw new Error(
          `Could not reach APME catalog API at ${url}. Check that the portal backend is available.`,
        );
      }
      throw err;
    } finally {
      clearTimeout(timeoutId);
    }

    if (response.status === 404 && fetchOptions?.notFoundReturnsNull) {
      return null as T;
    }

    if (!response.ok) {
      const errorText = await response.text();
      const method = (options?.method ?? "GET").toUpperCase();
      const isStartOperation =
        method === "POST" && endpoint.endsWith("/operation");
      if (response.status === 409 && isStartOperation) {
        throw new Error("A scan is already in progress for this project");
      }
      if (response.status === 409) {
        throw new Error(
          errorText
            ? `APME API conflict: ${errorText}`
            : "APME API conflict: request could not be completed",
        );
      }
      throw new Error(
        `APME API error: ${response.status} - ${errorText || response.statusText}`,
      );
    }

    if (response.status === 204) {
      return undefined as T;
    }

    return response.json();
  }

  async getHealth(): Promise<HealthStatus> {
    return this.fetch<HealthStatus>("/health");
  }

  async getPortalSettings(): Promise<ApmePortalSettings> {
    return this.fetch<ApmePortalSettings>("/settings");
  }

  async updatePortalSettings(
    body: UpdatePortalSettingsRequest,
  ): Promise<ApmePortalSettings> {
    return this.fetch<ApmePortalSettings>("/settings", {
      method: "PUT",
      body: JSON.stringify(body),
    });
  }

  async listGalaxyServers(): Promise<GalaxyServer[]> {
    return this.fetch<GalaxyServer[]>("/settings/galaxy-servers");
  }

  async createGalaxyServer(
    body: CreateGalaxyServerRequest,
  ): Promise<GalaxyServer> {
    return this.fetch<GalaxyServer>("/settings/galaxy-servers", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  async updateGalaxyServer(
    serverId: number,
    body: UpdateGalaxyServerRequest,
  ): Promise<GalaxyServer> {
    return this.fetch<GalaxyServer>(
      `/settings/galaxy-servers/${encodeURIComponent(String(serverId))}`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
    );
  }

  async deleteGalaxyServer(serverId: number): Promise<void> {
    await this.fetch<void>(
      `/settings/galaxy-servers/${encodeURIComponent(String(serverId))}`,
      { method: "DELETE" },
    );
  }

  async getProjectScanTarget(projectId: string): Promise<ProjectScanTarget> {
    return this.fetch<ProjectScanTarget>(
      `/projects/${encodeURIComponent(projectId)}/scan-target`,
    );
  }

  async updateProjectScanTarget(
    projectId: string,
    body: UpdateProjectScanTargetRequest,
  ): Promise<ProjectScanTarget> {
    return this.fetch<ProjectScanTarget>(
      `/projects/${encodeURIComponent(projectId)}/scan-target`,
      {
        method: "PUT",
        body: JSON.stringify(body),
      },
    );
  }

  async getAiStatus(): Promise<ApmeAiStatus> {
    return this.fetch<ApmeAiStatus>("/ai/status");
  }

  async getAiModels(): Promise<
    Array<{ id: string; provider: string; name: string }>
  > {
    return this.fetch<Array<{ id: string; provider: string; name: string }>>(
      "/ai/models",
    );
  }

  async getAiConfig(): Promise<unknown> {
    return this.fetch<unknown>("/ai/config");
  }

  async updateAiConfig(body: unknown): Promise<unknown> {
    return this.fetch<unknown>("/ai/config", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  async getAiProviders(): Promise<ApmeAiProviderSummary[]> {
    return this.fetch<ApmeAiProviderSummary[]>("/ai/providers");
  }

  async configureAiProvider(
    id: string,
    body: ApmeAiProviderConfigureRequest,
  ): Promise<unknown> {
    return this.fetch<unknown>(
      `/ai/provider/${encodeURIComponent(id)}/configure`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    );
  }

  async deleteAiProvider(id: string): Promise<void> {
    await this.fetch<void>(`/ai/provider/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  }

  async getAiEngines(): Promise<ApmeAiEnginesResponse> {
    return this.fetch<ApmeAiEnginesResponse>("/ai/engines");
  }

  async getProjects(): Promise<Project[]> {
    const response = await this.fetch<{ items: Project[] }>("/projects");
    return response.items || [];
  }

  async getProject(projectId: string): Promise<Project> {
    return this.fetch<Project>(`/projects/${encodeURIComponent(projectId)}`);
  }

  async getProjectByRepoUrl(
    repoUrl: string,
    branch?: string,
  ): Promise<Project | null> {
    const branchQuery =
      branch !== undefined && branch !== ""
        ? `&branch=${encodeURIComponent(branch)}`
        : "";
    return this.fetch<Project | null>(
      `/lookup?repo_url=${encodeURIComponent(repoUrl)}${branchQuery}`,
      undefined,
      { notFoundReturnsNull: true },
    );
  }

  async getViolations(
    projectId: string,
    options?: ApmeViolationsOptions,
  ): Promise<Violation[]> {
    const params = new URLSearchParams();
    if (options?.limit !== undefined) {
      params.set("limit", String(options.limit));
    }
    if (options?.offset !== undefined) {
      params.set("offset", String(options.offset));
    }
    const query = params.toString() ? `?${params.toString()}` : "";
    const response = await this.fetch<Violation[]>(
      `/projects/${encodeURIComponent(projectId)}/violations${query}`,
    );
    return response || [];
  }

  async getProjectDependencies(
    projectId: string,
  ): Promise<ProjectDependencies> {
    return this.fetch<ProjectDependencies>(
      `/projects/${encodeURIComponent(projectId)}/dependencies`,
    );
  }

  async getRules(): Promise<Rule[]> {
    const response = await this.fetch<{ items: (GatewayRuleRow | Rule)[] }>(
      "/rules",
    );
    const items = response.items || [];
    return items.map((item) => coerceRuleResponse(item));
  }

  async updateRuleConfig(
    ruleId: string,
    body: RuleConfigUpdate,
  ): Promise<Rule> {
    const response = await this.fetch<GatewayRuleRow | Rule>(
      `/rules/${encodeURIComponent(ruleId)}/config`,
      {
        method: "PUT",
        body: JSON.stringify(body),
      },
    );
    return coerceRuleResponse(response);
  }

  async deleteRuleConfig(ruleId: string): Promise<void> {
    await this.fetch<void>(`/rules/${encodeURIComponent(ruleId)}/config`, {
      method: "DELETE",
    });
  }

  async createSuppression(
    body: CreateSuppressionRequest,
  ): Promise<Suppression> {
    return this.fetch<Suppression>("/suppressions", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  async deleteSuppression(suppressionId: number): Promise<void> {
    await this.fetch<void>(`/suppressions/${suppressionId}`, {
      method: "DELETE",
    });
  }

  async getSuppressions(scope?: string): Promise<Suppression[]> {
    const query =
      scope !== undefined && scope !== ""
        ? `?scope=${encodeURIComponent(scope)}`
        : "";
    return this.fetch<Suppression[]>(`/suppressions${query}`);
  }

  async triggerScan(
    projectId: string,
    options?: ScanTriggerOptions,
  ): Promise<ScanResult> {
    const scanOptions: Record<string, unknown> = {};
    const version = options?.ansibleVersion?.trim();
    if (version) {
      scanOptions.ansible_version = version;
    }
    const response = await this.fetch<{ operation_id: string }>(
      `/projects/${encodeURIComponent(projectId)}/operation`,
      {
        method: "POST",
        body: JSON.stringify({ action: "check", options: scanOptions }),
      },
    );
    return { scanId: response.operation_id, projectId, status: "running" };
  }

  async createProject(request: CreateProjectRequest): Promise<Project> {
    return this.fetch<Project>("/projects", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  async validateRepoBranch(repoUrl: string, branch: string): Promise<void> {
    const params = new URLSearchParams({
      repo_url: repoUrl,
      branch,
    });
    await this.fetch<{ valid: boolean }>(`/repos/branch-check?${params}`);
  }

  async deleteProject(projectId: string): Promise<void> {
    await this.fetch<void>(`/projects/${encodeURIComponent(projectId)}`, {
      method: "DELETE",
    });
  }

  async getActivity(projectId: string): Promise<Activity[]> {
    return this.fetch<Activity[]>(
      `/projects/${encodeURIComponent(projectId)}/activity`,
    );
  }

  async getActivityDetail(activityId: string): Promise<ActivityDetail> {
    return this.fetch<ActivityDetail>(
      `/activity/${encodeURIComponent(activityId)}`,
    );
  }

  async getOperationState(projectId: string): Promise<OperationState | null> {
    return this.fetch<OperationState | null>(
      `/projects/${encodeURIComponent(projectId)}/operation/state`,
      undefined,
      { notFoundReturnsNull: true },
    );
  }

  async triggerRemediate(
    projectId: string,
    violationIds?: number[],
  ): Promise<ScanResult> {
    const options: Record<string, unknown> = {};
    if (violationIds && violationIds.length > 0) {
      options.violation_ids = violationIds;
    }
    const response = await this.fetch<{ operation_id: string }>(
      `/projects/${encodeURIComponent(projectId)}/operation`,
      {
        method: "POST",
        body: JSON.stringify({ action: "remediate", options }),
      },
    );
    return { scanId: response.operation_id, projectId, status: "running" };
  }

  async approveProposals(
    projectId: string,
    proposalIds: string[],
  ): Promise<void> {
    await this.fetch<void>(
      `/projects/${encodeURIComponent(projectId)}/operation/approve`,
      {
        method: "POST",
        body: JSON.stringify({ approved_ids: proposalIds }),
      },
    );
  }

  private scmHeaders(options?: ApmeScmRequestOptions): Record<string, string> {
    const headers: Record<string, string> = {};
    const token = options?.scmToken?.trim();
    if (token) {
      headers["X-SCM-Token"] = token;
    }
    return headers;
  }

  async submitRemediation(
    projectId: string,
    activityId: string,
    options?: ApmeScmRequestOptions & { createPr?: boolean },
  ): Promise<SubmitRemediationResult> {
    // Large remedia pushes (hundreds of blobs) often exceed the default 30s.
    return this.fetch<SubmitRemediationResult>(
      `/projects/${encodeURIComponent(projectId)}/submit`,
      {
        method: "POST",
        headers: this.scmHeaders(options),
        body: JSON.stringify({
          activity_id: activityId,
          branch_name: options?.branchName,
          create_pr: options?.createPr,
        }),
      },
      { timeoutMs: this.submitTimeoutMs },
    );
  }

  async pushRemediationBranch(
    projectId: string,
    activityId: string,
    options?: ApmeScmRequestOptions,
  ): Promise<SubmitRemediationResult> {
    return this.submitRemediation(projectId, activityId, {
      ...options,
      createPr: false,
    });
  }

  async createPullRequest(
    projectId: string,
    activityId: string,
    options?: ApmeScmRequestOptions,
  ): Promise<CreatePullRequestResult> {
    const result = await this.submitRemediation(projectId, activityId, {
      ...options,
      createPr: true,
    });
    if (!result.pr_url) {
      throw new Error("Submit completed without a PR URL");
    }
    return {
      pr_url: result.pr_url,
      branch_name: result.branch_name,
      provider: result.provider,
      commit_sha: result.commit_sha,
    };
  }
}
