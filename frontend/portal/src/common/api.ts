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

import { createApiRef } from "@backstage/core-plugin-api";
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
  SubmitRemediationResult,
  RuleConfigUpdate,
  CreateSuppressionRequest,
  Suppression,
} from "./types";

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
  getRules(): Promise<Rule[]>;
  updateRuleConfig(ruleId: string, body: RuleConfigUpdate): Promise<Rule>;
  deleteRuleConfig(ruleId: string): Promise<void>;
  createSuppression(body: CreateSuppressionRequest): Promise<Suppression>;
  deleteSuppression(suppressionId: number): Promise<void>;
  getSuppressions(scope?: string): Promise<Suppression[]>;
  triggerScan(projectId: string): Promise<ScanResult>;
  createProject(request: CreateProjectRequest): Promise<Project>;
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

export const apmeApiRef = createApiRef<ApmeApi>({
  id: "plugin.apme.api",
});
