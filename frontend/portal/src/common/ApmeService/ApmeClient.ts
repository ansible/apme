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

import { Agent } from "undici";
import { Config } from "@backstage/config";
import { LoggerService } from "@backstage/backend-plugin-api";
import { ConflictError, InputError, NotFoundError } from "@backstage/errors";
import { getApmeConfig } from "../config";
import {
  normalizeGatewayRules,
  normalizeGatewayRule,
  type GatewayRuleRow,
} from "../gatewayRules";
import { normalizeRemediationClass } from "../severity";
import {
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
  RemediationClass,
  ProjectDependencies,
  RuleConfigUpdate,
  CreateSuppressionRequest,
  Suppression,
  SubmitRemediationRequest,
  SubmitRemediationResult,
  ScanTriggerOptions,
  GalaxyServer,
  CreateGalaxyServerRequest,
  UpdateGalaxyServerRequest,
  ApmeAiProviderConfigureRequest,
  ApmeAiEnginesResponse,
} from "../types";

export interface ApmeClientOptions {
  rootConfig: Config;
  logger: LoggerService;
  /**
   * Optional per-request Gateway URL (portal-settings override).
   * Return a stored URL or undefined to use `ansible.apme.baseUrl`.
   */
  resolveBaseUrl?: () => Promise<string | undefined>;
}

export { getApmeConfig } from "../config";

export class ApmeClient {
  private readonly configBaseUrl: string;
  private resolveBaseUrlFn?: () => Promise<string | undefined>;
  private readonly submitTimeoutMs: number;
  private readonly logger: LoggerService;
  private readonly dispatcher: Agent | undefined;

  constructor(options: ApmeClientOptions) {
    const config = getApmeConfig(options.rootConfig);
    this.configBaseUrl = config.baseUrl;
    this.resolveBaseUrlFn = options.resolveBaseUrl;
    this.submitTimeoutMs = config.submitTimeoutMs;
    if (!config.checkSSL) {
      this.dispatcher = new Agent({ connect: { rejectUnauthorized: false } });
    }
    this.logger = options.logger.child({ service: "ApmeClient" });
    this.logger.debug(
      `APME client initialized with baseUrl: ${this.configBaseUrl}`,
    );
  }

  /**
   * Attach a portal-settings resolver after construction (catalog module).
   */
  setResolveBaseUrl(resolveBaseUrl: () => Promise<string | undefined>): void {
    this.resolveBaseUrlFn = resolveBaseUrl;
  }

  private async currentBaseUrl(): Promise<string> {
    if (!this.resolveBaseUrlFn) {
      return this.configBaseUrl;
    }
    // NOSONAR - Simple trailing slash pattern; no backtracking risk
    const override = (await this.resolveBaseUrlFn())
      ?.trim()
      .replace(/\/+$/, ""); // NOSONAR
    return override || this.configBaseUrl;
  }

  private scanOperationOptions(
    ansibleVersion?: string,
    enableAiOverride?: boolean,
  ): Record<string, unknown> {
    const options: Record<string, unknown> = {
      enable_ai: enableAiOverride ?? false,
    };
    const version = ansibleVersion?.trim();
    if (version) {
      options.ansible_version = version;
    }
    return options;
  }

  private buildUrl(endpoint: string, baseUrl: string): string {
    const path = endpoint.startsWith("/") ? endpoint : `/${endpoint}`;
    return `${baseUrl}${path}`;
  }

  private async executeRequest<T>(
    endpoint: string,
    options: RequestInit = {},
    requestOptions?: { timeoutMs?: number },
  ): Promise<T> {
    const baseUrl = this.resolveBaseUrlFn
      ? await this.currentBaseUrl()
      : this.configBaseUrl;
    const url = this.buildUrl(endpoint, baseUrl);
    const customHeaders = options.headers as Record<string, string> | undefined;
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (customHeaders) {
      Object.assign(headers, customHeaders);
    }

    const timeoutMs = requestOptions?.timeoutMs ?? 30_000;
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
    try {
      const response = await fetch(url, {
        ...options,
        headers,
        signal: controller.signal,
        ...(this.dispatcher ? { dispatcher: this.dispatcher } : {}),
      } as RequestInit);

      if (response.status === 404) {
        throw new NotFoundError(`Resource not found: ${endpoint}`);
      }

      if (response.status === 409) {
        const errorBody = await response.text();
        const method = (options?.method ?? "GET").toUpperCase();
        const isStartOperation =
          method === "POST" && endpoint.endsWith("/operation");
        if (isStartOperation) {
          throw new ConflictError(
            "A scan is already in progress for this project",
          );
        }
        throw new ConflictError(
          errorBody || "Request conflicted with existing state",
        );
      }

      if (!response.ok) {
        const errorBody = await response.text();
        const msg = `APME request failed: ${response.status} ${response.statusText} - ${errorBody}`;
        if (response.status >= 500) {
          throw new Error(msg);
        }
        throw new InputError(msg);
      }

      // Handle 204 No Content responses
      if (response.status === 204) {
        return undefined as T;
      }

      return response.json() as Promise<T>;
    } catch (error) {
      if (
        error instanceof NotFoundError ||
        error instanceof InputError ||
        error instanceof ConflictError
      ) {
        throw error;
      }
      this.logger.error(`APME request failed: ${endpoint}`, error as Error);
      throw new InputError(
        `Failed to connect to APME: ${(error as Error).message}`,
      );
    } finally {
      clearTimeout(timeoutId);
    }
  }

  async getHealth(): Promise<HealthStatus> {
    return this.executeRequest<HealthStatus>("/api/v1/health");
  }

  async getAiModels(): Promise<
    Array<{ id: string; provider: string; name: string }>
  > {
    try {
      return await this.executeRequest<
        Array<{ id: string; provider: string; name: string }>
      >("/api/v1/ai/models");
    } catch {
      return [];
    }
  }

  async getAiConfig(): Promise<unknown> {
    try {
      return await this.executeRequest<unknown>("/api/v1/ai/config");
    } catch {
      return {};
    }
  }

  async updateAiConfig(body: unknown): Promise<unknown> {
    return this.executeRequest<unknown>("/api/v1/ai/config", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  async getAiProviders(): Promise<unknown> {
    try {
      return await this.executeRequest<unknown>("/api/v1/ai/providers");
    } catch {
      return [];
    }
  }

  async getAiEngines(): Promise<ApmeAiEnginesResponse> {
    try {
      return await this.executeRequest<ApmeAiEnginesResponse>(
        "/api/v1/ai/engines",
      );
    } catch {
      return { engines: [] };
    }
  }

  async configureAiProvider(
    id: string,
    body: ApmeAiProviderConfigureRequest,
  ): Promise<unknown> {
    return this.executeRequest<unknown>(
      `/api/v1/ai/provider/${encodeURIComponent(id)}/configure`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    );
  }

  async deleteAiProvider(id: string): Promise<void> {
    await this.executeRequest<void>(
      `/api/v1/ai/provider/${encodeURIComponent(id)}`,
      { method: "DELETE" },
    );
  }

  async getProjects(): Promise<Project[]> {
    const response = await this.executeRequest<{ items: Project[] }>(
      "/api/v1/projects",
    );
    return response.items || [];
  }

  async getProject(projectId: string): Promise<Project> {
    return this.executeRequest<Project>(
      `/api/v1/projects/${encodeURIComponent(projectId)}`,
    );
  }

  async getProjectByRepoUrl(
    repoUrl: string,
    branch?: string,
  ): Promise<Project | null> {
    try {
      const branchQuery =
        branch !== undefined && branch !== ""
          ? `&branch=${encodeURIComponent(branch)}`
          : "";
      const project = await this.executeRequest<Project>(
        `/api/v1/projects/lookup?repo_url=${encodeURIComponent(
          repoUrl,
        )}${branchQuery}`,
      );
      return project;
    } catch (error) {
      if (error instanceof NotFoundError) {
        return null;
      }
      throw error;
    }
  }

  async getViolations(
    projectId: string,
    options?: { limit?: number; offset?: number },
  ): Promise<Violation[]> {
    const params = new URLSearchParams();
    if (options?.limit !== undefined) {
      params.set("limit", String(options.limit));
    }
    if (options?.offset !== undefined) {
      params.set("offset", String(options.offset));
    }
    const query = params.toString() ? `?${params.toString()}` : "";
    // APME returns violations as a direct array, not wrapped in {items: []}
    const response = await this.executeRequest<Violation[]>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/violations${query}`,
    );
    return (response || []).map((v) => ({
      ...v,
      remediation_class: normalizeRemediationClass(
        v.remediation_class,
      ) as RemediationClass,
    }));
  }

  async getProjectDependencies(
    projectId: string,
  ): Promise<ProjectDependencies> {
    return this.executeRequest<ProjectDependencies>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/dependencies`,
    );
  }

  async getRules(): Promise<Rule[]> {
    const response = await this.executeRequest<
      { items: GatewayRuleRow[] } | GatewayRuleRow[]
    >("/api/v1/rules");
    const rows = Array.isArray(response) ? response : response.items || [];
    return normalizeGatewayRules(rows);
  }

  async updateRuleConfig(
    ruleId: string,
    body: RuleConfigUpdate,
  ): Promise<Rule> {
    const response = await this.executeRequest<GatewayRuleRow>(
      `/api/v1/rules/${encodeURIComponent(ruleId)}/config`,
      {
        method: "PUT",
        body: JSON.stringify(body),
      },
    );
    return normalizeGatewayRule(response);
  }

  async deleteRuleConfig(ruleId: string): Promise<void> {
    await this.executeRequest<void>(
      `/api/v1/rules/${encodeURIComponent(ruleId)}/config`,
      { method: "DELETE" },
    );
  }

  async createSuppression(
    body: CreateSuppressionRequest,
  ): Promise<Suppression> {
    return this.executeRequest<Suppression>("/api/v1/suppressions", {
      method: "POST",
      body: JSON.stringify(body),
    });
  }

  async deleteSuppression(suppressionId: number): Promise<void> {
    await this.executeRequest<void>(`/api/v1/suppressions/${suppressionId}`, {
      method: "DELETE",
    });
  }

  async getSuppressions(scope?: string): Promise<Suppression[]> {
    const query =
      scope !== undefined && scope !== ""
        ? `?scope=${encodeURIComponent(scope)}`
        : "";
    return this.executeRequest<Suppression[]>(`/api/v1/suppressions${query}`);
  }

  async listGalaxyServers(): Promise<GalaxyServer[]> {
    return this.executeRequest<GalaxyServer[]>(
      "/api/v1/settings/galaxy-servers",
    );
  }

  async createGalaxyServer(
    body: CreateGalaxyServerRequest,
  ): Promise<GalaxyServer> {
    return this.executeRequest<GalaxyServer>(
      "/api/v1/settings/galaxy-servers",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    );
  }

  async updateGalaxyServer(
    serverId: number,
    body: UpdateGalaxyServerRequest,
  ): Promise<GalaxyServer> {
    return this.executeRequest<GalaxyServer>(
      `/api/v1/settings/galaxy-servers/${serverId}`,
      {
        method: "PATCH",
        body: JSON.stringify(body),
      },
    );
  }

  async deleteGalaxyServer(serverId: number): Promise<void> {
    await this.executeRequest<void>(
      `/api/v1/settings/galaxy-servers/${serverId}`,
      { method: "DELETE" },
    );
  }

  async triggerScan(
    projectId: string,
    options?: ScanTriggerOptions,
  ): Promise<ScanResult> {
    const headers: Record<string, string> = {};
    const userIdentity = options?.userIdentity;
    if (userIdentity) {
      headers["X-User"] = userIdentity.userEntityRef;
      if (userIdentity.orgEntityRef) {
        headers["X-Org"] = userIdentity.orgEntityRef;
      }
    }

    // Use the operation endpoint (ADR-052) to trigger a check
    const body: Record<string, unknown> = {
      action: "check",
      options: this.scanOperationOptions(
        options?.ansibleVersion,
        options?.enableAi,
      ),
    };
    if (options?.scmToken) {
      body.scm_token = options.scmToken;
    }
    const response = await this.executeRequest<{ operation_id: string }>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/operation`,
      {
        method: "POST",
        headers,
        body: JSON.stringify(body),
      },
    );

    return {
      scanId: response.operation_id,
      projectId,
      status: "running",
    };
  }

  async getScanStatus(projectId: string, scanId: string): Promise<ScanResult> {
    return this.executeRequest<ScanResult>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/scans/${encodeURIComponent(scanId)}`,
    );
  }

  async createProject(request: CreateProjectRequest): Promise<Project> {
    return this.executeRequest<Project>("/api/v1/projects", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  async deleteProject(projectId: string): Promise<void> {
    await this.executeRequest<void>(
      `/api/v1/projects/${encodeURIComponent(projectId)}`,
      {
        method: "DELETE",
      },
    );
  }

  async getActivity(projectId: string): Promise<Activity[]> {
    const response = await this.executeRequest<{ items: Activity[] }>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/activity`,
    );
    return response.items || [];
  }

  async getActivityDetail(activityId: string): Promise<ActivityDetail> {
    const detail = await this.executeRequest<ActivityDetail>(
      `/api/v1/activity/${encodeURIComponent(activityId)}`,
    );
    return {
      ...detail,
      violations: (detail.violations ?? []).map((v) => ({
        ...v,
        remediation_class: normalizeRemediationClass(
          v.remediation_class,
        ) as RemediationClass,
      })),
    };
  }

  async getOperationState(projectId: string): Promise<OperationState | null> {
    try {
      return await this.executeRequest<OperationState>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/operation`,
      );
    } catch (error) {
      if (error instanceof NotFoundError) {
        return null;
      }
      throw error;
    }
  }

  async triggerRemediate(
    projectId: string,
    violationIds?: number[],
    options?: ScanTriggerOptions,
  ): Promise<ScanResult> {
    const scanOptions: Record<string, unknown> = {
      ...this.scanOperationOptions(options?.ansibleVersion, options?.enableAi),
    };
    if (violationIds && violationIds.length > 0) {
      scanOptions.violation_ids = violationIds;
    }
    const body: Record<string, unknown> = {
      action: "remediate",
      options: scanOptions,
    };
    const scmToken = options?.scmToken?.trim();
    if (scmToken) {
      body.scm_token = scmToken;
    }
    const response = await this.executeRequest<{ operation_id: string }>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/operation`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
    );
    return {
      scanId: response.operation_id,
      projectId,
      status: "running",
    };
  }

  async approveProposals(
    projectId: string,
    proposalIds: string[],
  ): Promise<void> {
    await this.executeRequest<void>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/operation/approve`,
      {
        method: "POST",
        body: JSON.stringify({ approved_ids: proposalIds }),
      },
    );
  }

  async submitRemediation(
    projectId: string,
    body: SubmitRemediationRequest,
  ): Promise<SubmitRemediationResult> {
    // Large remedia pushes often exceed the default short fetch timeout.
    return this.executeRequest<SubmitRemediationResult>(
      `/api/v1/projects/${encodeURIComponent(projectId)}/operation/submit`,
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      { timeoutMs: this.submitTimeoutMs },
    );
  }

  async createPullRequest(
    projectId: string,
    activityId: string,
    scmToken?: string,
    branchName?: string,
  ): Promise<CreatePullRequestResult> {
    const body: SubmitRemediationRequest = {
      activity_id: activityId,
      create_pr: true,
    };
    if (scmToken) {
      body.scm_token = scmToken;
    }
    if (branchName) {
      body.branch_name = branchName;
    }
    const result = await this.submitRemediation(projectId, body);
    if (!result.pr_url) {
      throw new InputError("Submit completed without a PR URL");
    }
    return {
      pr_url: result.pr_url,
      branch_name: result.branch_name,
      provider: result.provider,
      commit_sha: result.commit_sha,
    };
  }
}

export type IApmeService = Pick<
  ApmeClient,
  | "getHealth"
  | "getAiModels"
  | "getAiConfig"
  | "updateAiConfig"
  | "getAiProviders"
  | "getAiEngines"
  | "configureAiProvider"
  | "deleteAiProvider"
  | "getProjects"
  | "getProject"
  | "getProjectByRepoUrl"
  | "getViolations"
  | "getProjectDependencies"
  | "getRules"
  | "updateRuleConfig"
  | "deleteRuleConfig"
  | "createSuppression"
  | "deleteSuppression"
  | "getSuppressions"
  | "listGalaxyServers"
  | "createGalaxyServer"
  | "updateGalaxyServer"
  | "deleteGalaxyServer"
  | "triggerScan"
  | "getScanStatus"
  | "createProject"
  | "deleteProject"
  | "getActivity"
  | "getActivityDetail"
  | "getOperationState"
  | "triggerRemediate"
  | "approveProposals"
  | "submitRemediation"
  | "createPullRequest"
>;
