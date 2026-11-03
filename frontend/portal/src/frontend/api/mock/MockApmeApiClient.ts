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
  Project,
  Violation,
  Rule,
  ScanResult,
  HealthStatus,
  CreateProjectRequest,
  Activity,
  ActivityDetail,
  OperationState,
  Proposal,
  ProjectDependencies,
  RuleConfigUpdate,
  CreateSuppressionRequest,
  Suppression,
  GalaxyServer,
  CreateGalaxyServerRequest,
  UpdateGalaxyServerRequest,
} from "../../../common/types";
import {
  severityLevelToCatalogSeverity,
  severityProtoToLabel,
} from "../../../common/severity";
import { ApmeApi } from "../ApmeApi";
import {
  MOCK_HEALTH,
  MOCK_PROJECTS,
  MOCK_VIOLATIONS,
  MOCK_RULES,
  MOCK_ACTIVITY,
} from "./fixtures";

const delay = (ms: number) => new Promise((r) => setTimeout(r, ms));

/** Last id segment, or empty string when the list is empty. */
export function lastPathSegment(parts: string[]): string {
  return parts.at(-1) ?? "";
}

function severityFromOverride(value: number): Rule["severity"] {
  return severityLevelToCatalogSeverity(severityProtoToLabel(value));
}

interface ScanState {
  operationId: string;
  startedAt: number;
  durationMs: number;
  proposals?: Proposal[];
  completed: boolean;
  projectId: string;
}

interface RemediateState {
  operationId: string;
  startedAt: number;
  durationMs: number;
  projectId: string;
  violationIds: number[];
  completed: boolean;
}

export class MockApmeApiClient implements ApmeApi {
  private projects: Project[] = MOCK_PROJECTS.map((p) => ({ ...p }));
  private violations: Record<string, Violation[]> = { ...MOCK_VIOLATIONS };
  private readonly activeScans: Map<string, ScanState> = new Map();
  private readonly activeRemediations: Map<string, RemediateState> = new Map();
  private nextProjectId = 100;
  private nextPrNumber = 142;
  private nextGalaxyServerId = 2;
  private galaxyServers: GalaxyServer[] = [
    {
      id: 1,
      name: "galaxy",
      url: "https://galaxy.ansible.com/api/",
      auth_url: "",
      has_token: false,
      created_at: "2025-01-01T00:00:00.000Z",
      updated_at: "2025-01-01T00:00:00.000Z",
    },
  ];

  async getHealth(): Promise<HealthStatus> {
    await delay(100);
    return { ...MOCK_HEALTH };
  }

  async getPortalSettings() {
    await delay(50);
    return {
      enableAi: this.enableAi,
      publishViaGateway: true,
      targetAnsibleCoreVersion: this.globalScanTarget,
      defaultAiModelId: this.defaultAiModelId,
      gatewayBaseUrl: this.gatewayBaseUrl,
    };
  }

  async updatePortalSettings(body: {
    targetAnsibleCoreVersion?: string;
    defaultAiModelId?: string | null;
    enableAi?: boolean;
    gatewayBaseUrl?: string | null;
  }) {
    await delay(50);
    if (body.targetAnsibleCoreVersion) {
      this.globalScanTarget = body.targetAnsibleCoreVersion;
    }
    if (body.defaultAiModelId !== undefined) {
      this.defaultAiModelId = body.defaultAiModelId ?? undefined;
    }
    if (body.enableAi !== undefined) {
      this.enableAi = body.enableAi;
    }
    if (body.gatewayBaseUrl !== undefined) {
      this.gatewayBaseUrl = body.gatewayBaseUrl?.trim() || undefined;
    }
    return this.getPortalSettings();
  }

  async getProjectScanTarget(projectId: string) {
    await delay(50);
    const override = this.projectScanTargets[projectId];
    const globalDefault = this.globalScanTarget;
    if (override) {
      return {
        effective: override,
        source: "project" as const,
        globalDefault,
        projectOverride: override,
      };
    }
    return {
      effective: globalDefault,
      source: "global" as const,
      globalDefault,
    };
  }

  async updateProjectScanTarget(
    projectId: string,
    body: { targetAnsibleCoreVersion: string | null },
  ) {
    await delay(50);
    if (body.targetAnsibleCoreVersion === null) {
      delete this.projectScanTargets[projectId];
    } else {
      this.projectScanTargets[projectId] = body.targetAnsibleCoreVersion;
    }
    return this.getProjectScanTarget(projectId);
  }

  private globalScanTarget = "2.16";
  private defaultAiModelId: string | undefined = "mock-provider/mock-model";
  private enableAi = true;
  private gatewayBaseUrl: string | undefined = "http://localhost:8080";

  private projectScanTargets: Record<string, string> = {};

  async getAiStatus() {
    await delay(50);
    return {
      enableAi: this.enableAi,
      connected: true,
      modelCount: 1,
      configuredModelCount: 1,
    };
  }

  async getAiModels() {
    await delay(50);
    return [
      {
        id: "mock-model",
        provider: "mock-provider",
        name: "Mock model",
      },
    ];
  }

  async getAiConfig() {
    await delay(50);
    return {
      providers: {
        "mock-provider": {
          type: "openai",
          models: [{ id: "mock-model", name: "Mock model" }],
        },
      },
    };
  }

  async updateAiConfig(body: unknown) {
    await delay(50);
    return body;
  }

  async getAiProviders() {
    await delay(50);
    return [
      {
        id: "mock-provider",
        engine: "openai",
        models: ["mock-model"],
      },
    ];
  }

  async getAiEngines() {
    await delay(50);
    return {
      engines: [
        {
          id: "openai",
          requiresKey: true,
          defaultBaseUrl: "https://api.openai.com/v1",
        },
      ],
    };
  }

  async configureAiProvider(_id: string, body: unknown) {
    await delay(50);
    return body;
  }

  async deleteAiProvider(_id: string) {
    await delay(50);
  }

  async getProjects(): Promise<Project[]> {
    await delay(200);
    return this.projects.map((p) => {
      const violations = this.violations[p.id] ?? [];
      const counts = {
        critical: 0,
        error: 0,
        high: 0,
        medium: 0,
        low: 0,
        info: 0,
      };
      for (const v of violations) {
        if (v.level === "blocker" || v.level === "critical") counts.critical++;
        else if (v.level === "error") counts.error++;
        else if (v.level === "high") counts.high++;
        else if (v.level === "medium") counts.medium++;
        else if (v.level === "low") counts.low++;
        else counts.info++;
      }
      return { ...p, violationCounts: counts };
    });
  }

  async getProject(projectId: string): Promise<Project> {
    await delay(100);
    const project = this.projects.find((p) => p.id === projectId);
    if (!project) throw new Error(`Project not found: ${projectId}`);
    return { ...project };
  }

  async getProjectByRepoUrl(
    repoUrl: string,
    branch?: string,
  ): Promise<Project | null> {
    await delay(150);
    const normalised = repoUrl.replace(/^url:/, "").replace(/\.git$/, "");
    const project = this.projects.find((p) => {
      const projUrl = p.repo_url.replace(/\.git$/, "");
      const urlMatch =
        projUrl === normalised ||
        projUrl.endsWith(normalised.split("/").slice(-2).join("/"));
      if (!urlMatch) {
        return false;
      }
      if (branch !== undefined && branch !== "") {
        return p.branch === branch;
      }
      return true;
    });
    return project ? { ...project } : null;
  }

  async getViolations(
    projectId: string,
    options?: { limit?: number; offset?: number },
  ): Promise<Violation[]> {
    await delay(200);
    const rows = (this.violations[projectId] ?? []).map((v) => ({ ...v }));
    const offset = options?.offset ?? 0;
    if (options?.limit !== undefined) {
      return rows.slice(offset, offset + options.limit);
    }
    return rows.slice(offset);
  }

  async getProjectDependencies(
    projectId: string,
  ): Promise<ProjectDependencies> {
    await delay(200);
    const violations = this.violations[projectId] ?? [];
    const depViolations = violations.filter(
      (v) => v.category === "dependencies",
    );
    const pythonNames = new Set<string>();
    for (const v of depViolations) {
      if (v.validator_source === "dep_audit") {
        const name = v.message.split(/\s+/)[0];
        if (name) pythonNames.add(name);
      }
    }
    return {
      ansible_core_version: "2.16.0",
      collections: [
        {
          fqcn: "ansible.posix",
          version: "1.5.4",
          source: "specified",
        },
      ],
      python_packages: [...pythonNames].map((name) => ({
        name,
        version: "0.0.0",
      })),
      requirements_files: ["requirements.txt"],
      dependency_tree: "",
    };
  }

  async getRules(): Promise<Rule[]> {
    await delay(300);
    return MOCK_RULES.map((r) => ({ ...r }));
  }

  private mockSuppressions: Suppression[] = [];
  private nextSuppressionId = 1;

  async updateRuleConfig(
    ruleId: string,
    body: RuleConfigUpdate,
  ): Promise<Rule> {
    await delay(150);
    const rule = MOCK_RULES.find((r) => r.id === ruleId);
    if (!rule) throw new Error(`Rule ${ruleId} not found`);
    if (typeof body.enabled_override === "boolean") {
      rule.enabled = body.enabled_override;
    }
    if (typeof body.enforced === "boolean") {
      rule.enforced = body.enforced;
    }
    if (typeof body.severity_override === "number") {
      rule.severity = severityFromOverride(body.severity_override);
    }
    rule.hasOverride = true;
    return { ...rule };
  }

  async deleteRuleConfig(ruleId: string): Promise<void> {
    await delay(100);
    const rule = MOCK_RULES.find((r) => r.id === ruleId);
    if (rule) {
      rule.hasOverride = false;
      rule.enforced = false;
    }
  }

  async createSuppression(
    body: CreateSuppressionRequest,
  ): Promise<Suppression> {
    await delay(150);
    const suppression: Suppression = {
      id: this.nextSuppressionId++,
      fingerprint_hash: body.fingerprint_hash ?? "mock",
      fingerprint_mode: body.fingerprint_mode ?? "full",
      rule_id: body.rule_id,
      scope: body.scope,
      reason: body.reason ?? "",
      created_by: "mock",
      created_at: new Date().toISOString(),
    };
    this.mockSuppressions.push(suppression);
    return suppression;
  }

  async deleteSuppression(suppressionId: number): Promise<void> {
    await delay(100);
    this.mockSuppressions = this.mockSuppressions.filter(
      (s) => s.id !== suppressionId,
    );
  }

  async getSuppressions(scope?: string): Promise<Suppression[]> {
    await delay(100);
    if (!scope) return [...this.mockSuppressions];
    return this.mockSuppressions.filter((s) => s.scope === scope);
  }

  async listGalaxyServers(): Promise<GalaxyServer[]> {
    await delay(100);
    return this.galaxyServers.map((s) => ({ ...s }));
  }

  async createGalaxyServer(
    body: CreateGalaxyServerRequest,
  ): Promise<GalaxyServer> {
    await delay(150);
    const now = new Date().toISOString();
    const server: GalaxyServer = {
      id: this.nextGalaxyServerId++,
      name: body.name,
      url: body.url,
      auth_url: body.auth_url ?? "",
      has_token: Boolean(body.token?.trim()),
      created_at: now,
      updated_at: now,
    };
    this.galaxyServers.push(server);
    return { ...server };
  }

  async updateGalaxyServer(
    serverId: number,
    body: UpdateGalaxyServerRequest,
  ): Promise<GalaxyServer> {
    await delay(150);
    const idx = this.galaxyServers.findIndex((s) => s.id === serverId);
    if (idx < 0) throw new Error(`Galaxy server ${serverId} not found`);
    const existing = this.galaxyServers[idx];
    const updated: GalaxyServer = {
      ...existing,
      name: body.name ?? existing.name,
      url: body.url ?? existing.url,
      auth_url: body.auth_url ?? existing.auth_url,
      has_token:
        body.token !== undefined
          ? Boolean(body.token.trim())
          : existing.has_token,
      updated_at: new Date().toISOString(),
    };
    this.galaxyServers[idx] = updated;
    return { ...updated };
  }

  async deleteGalaxyServer(serverId: number): Promise<void> {
    await delay(100);
    this.galaxyServers = this.galaxyServers.filter((s) => s.id !== serverId);
  }

  async triggerScan(
    projectId: string,
    _options?: { ansibleVersion?: string },
  ): Promise<ScanResult> {
    await delay(300);
    const operationId = `op-scan-${Date.now()}`;
    this.activeScans.set(projectId, {
      operationId,
      startedAt: Date.now(),
      durationMs: 10000, // 10s simulated scan
      completed: false,
      projectId,
    });
    const proj = this.projects.find((p) => p.id === projectId);
    if (proj) proj.active_operation = operationId;
    return { scanId: operationId, projectId, status: "running" };
  }

  async validateRepoBranch(_repoUrl: string, _branch: string): Promise<void> {
    await delay(50);
  }

  async createProject(request: CreateProjectRequest): Promise<Project> {
    await delay(400);
    const newProject: Project = {
      id: `proj-${this.nextProjectId++}`,
      name: request.name,
      repo_url: request.repo_url,
      branch: request.branch ?? "main",
      created_at: new Date().toISOString(),
      health_score: 0,
      total_violations: 0,
      scan_count: 0,
      last_scanned_at: undefined,
      scm_provider: "github",
      has_scm_token: !!request.scm_token,
      last_scanned_commit: undefined,
      has_new_commits: false,
      active_operation: null,
    };
    this.projects.push(newProject);
    this.violations[newProject.id] = [];
    return { ...newProject };
  }

  async deleteProject(projectId: string): Promise<void> {
    await delay(200);
    this.projects = this.projects.filter((p) => p.id !== projectId);
    delete this.violations[projectId];
  }

  async getActivity(projectId: string): Promise<Activity[]> {
    await delay(200);
    return (MOCK_ACTIVITY[projectId] ?? []).map((a) => ({ ...a }));
  }

  async getActivityDetail(activityId: string): Promise<ActivityDetail> {
    await delay(200);
    for (const entries of Object.values(MOCK_ACTIVITY)) {
      const match = entries.find((a) => a.scan_id === activityId);
      if (match) {
        return {
          ...match,
          violations: [],
          proposals: [],
        };
      }
    }
    return {
      scan_id: activityId,
      session_id: "mock",
      project_path: "mock",
      source: "mock",
      created_at: new Date().toISOString(),
      scan_type: "check",
      total_violations: 0,
      fixable: 0,
      ai_candidate: 0,
      ai_proposed: 0,
      ai_declined: 0,
      ai_accepted: 0,
      manual_review: 0,
      remediated_count: 0,
      violations: [],
      proposals: [],
    };
  }

  async getOperationState(projectId: string): Promise<OperationState | null> {
    await delay(150);

    const scan = this.activeScans.get(projectId);
    if (scan) {
      const elapsed = Date.now() - scan.startedAt;
      const progress = Math.min(
        100,
        Math.floor((elapsed / scan.durationMs) * 100),
      );

      if (progress >= 100 && !scan.completed) {
        scan.completed = true;
        const proj = this.projects.find((p) => p.id === projectId);
        if (proj) {
          proj.active_operation = null;
          proj.last_scanned_at = new Date().toISOString();
          proj.total_violations = (this.violations[projectId] ?? []).length;
          proj.scan_count += 1;
          proj.health_score = Math.max(0, 100 - proj.total_violations * 4);
        }
        this.activeScans.delete(projectId);
        return null;
      }

      let phase: string;
      let phaseLabel: string;
      if (progress < 20) {
        phase = "cloning";
        phaseLabel = "Cloning repository";
      } else if (progress < 60) {
        phase = "scanning";
        phaseLabel = "Running validators";
      } else {
        phase = "analyzing";
        phaseLabel = "Analyzing results";
      }
      return {
        operation_id: scan.operationId,
        project_id: projectId,
        status: "running",
        phase,
        progress_pct: progress,
        latest_message: `${phaseLabel}… ${progress}%`,
      };
    }

    const remediation = this.activeRemediations.get(projectId);
    if (remediation) {
      const elapsed = Date.now() - remediation.startedAt;
      const progress = Math.min(
        100,
        Math.floor((elapsed / remediation.durationMs) * 100),
      );
      const processed = Math.floor(
        (progress / 100) * remediation.violationIds.length,
      );

      if (progress >= 100 && !remediation.completed) {
        remediation.completed = true;
        const proj = this.projects.find((p) => p.id === projectId);
        if (proj) proj.active_operation = null;
        this.activeRemediations.delete(projectId);
        return null;
      }

      const proposals: Proposal[] = remediation.violationIds
        .slice(0, processed)
        .map((vid) => {
          const violation = (this.violations[projectId] ?? []).find(
            (v) => v.id === vid,
          );
          const isAi = violation?.remediation_class === 2;
          const original = violation?.original_yaml ?? "";
          const fixed =
            violation?.fixed_yaml ??
            (isAi ? (violation?.ai_suggestion ?? "") : "");
          const diffHunk =
            isAi && original && fixed
              ? [
                  `--- a/${violation?.file ?? ""}`,
                  `+++ b/${violation?.file ?? ""}`,
                  `@@ -${violation?.line ?? 1},1 +${violation?.line ?? 1},1 @@`,
                  ...original
                    .split("\n")
                    .map((line) => `-${line}`)
                    .concat(fixed.split("\n").map((line) => `+${line}`)),
                ].join("\n")
              : undefined;
          return {
            id: `prop-${vid}`,
            violation_id: vid,
            rule_id: violation?.rule_id ?? "UNKNOWN",
            file: violation?.file ?? "",
            line: violation?.line ?? 0,
            original_yaml: original,
            fixed_yaml: fixed,
            status: "pending" as const,
            tier: isAi ? 2 : 1,
            diff_hunk: diffHunk,
            ai_reason: isAi ? violation?.ai_reason : undefined,
          };
        });

      return {
        operation_id: remediation.operationId,
        project_id: projectId,
        status: "running",
        phase: "generating",
        progress_pct: progress,
        latest_message: `Generating fixes… ${processed} of ${remediation.violationIds.length} processed`,
        proposals,
      };
    }

    return null;
  }

  async triggerRemediate(
    projectId: string,
    _violationIds?: number[],
  ): Promise<ScanResult> {
    await delay(300);
    const operationId = `op-rem-${Date.now()}`;
    const fixableViolations = (this.violations[projectId] ?? [])
      .filter((v) => v.remediation_class === 1 || v.remediation_class === 2)
      .map((v) => v.id);

    this.activeRemediations.set(projectId, {
      operationId,
      startedAt: Date.now(),
      durationMs: 8000,
      projectId,
      violationIds: fixableViolations,
      completed: false,
    });
    const proj = this.projects.find((p) => p.id === projectId);
    if (proj) proj.active_operation = operationId;
    return { scanId: operationId, projectId, status: "running" };
  }

  async approveProposals(
    projectId: string,
    proposalIds: string[],
  ): Promise<void> {
    await delay(200);
    const approvedViolationIds = new Set(
      proposalIds.map((pid) => {
        return Number.parseInt(lastPathSegment(pid.split("-")), 10);
      }),
    );
    this.violations[projectId] = (this.violations[projectId] ?? []).filter(
      (v) => !approvedViolationIds.has(v.id),
    );
    const proj = this.projects.find((p) => p.id === projectId);
    if (proj) {
      proj.total_violations = this.violations[projectId].length;
      proj.health_score = Math.max(0, 100 - proj.total_violations * 4);
    }
  }

  async submitRemediation(
    projectId: string,
    activityId: string,
    options?: {
      scmToken?: string;
      branchName?: string;
      createPr?: boolean;
      fileOverrides?: Record<string, string>;
    },
  ) {
    await delay(options?.createPr === false ? 500 : 1500);
    const branchName =
      options?.branchName ?? `apme/remediate-${activityId.slice(0, 8)}`;
    if (options?.createPr === false) {
      return {
        branch_name: branchName,
        commit_sha: "mock-commit-sha",
        pr_url: null,
        provider: "github",
      };
    }
    const prNumber = this.nextPrNumber++;
    const proj = this.projects.find((p) => p.id === projectId);
    const repoName = proj?.name ?? "rhel-patching";
    return {
      branch_name: branchName,
      commit_sha: "mock-commit-sha",
      pr_url: `https://github.com/ansible-demo/${repoName}/pull/${prNumber}`,
      provider: "github",
    };
  }

  async createPullRequest(
    projectId: string,
    activityId: string,
    options?: { scmToken?: string; branchName?: string },
  ): Promise<{ pr_url: string; branch_name?: string; provider?: string }> {
    const result = await this.submitRemediation(projectId, activityId, {
      ...options,
      createPr: true,
    });
    return {
      pr_url: result.pr_url!,
      branch_name: result.branch_name,
      provider: result.provider,
    };
  }

  async pushRemediationBranch(
    projectId: string,
    activityId: string,
    options?: { scmToken?: string; branchName?: string },
  ) {
    return this.submitRemediation(projectId, activityId, {
      ...options,
      createPr: false,
    });
  }
}
