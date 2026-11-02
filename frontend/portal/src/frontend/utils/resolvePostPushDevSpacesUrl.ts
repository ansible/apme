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

import { buildDevSpacesRemediationUrl } from "@ansible/backstage-rhaap-common/devSpaces";

export type ResolvePostPushDevSpacesUrlOptions = {
  sessionVisible: boolean;
  devSpacesBaseUrl?: string;
  repoUrl?: string | null;
  /** Branch returned by the last successful createPR/push. */
  pushedBranchName?: string | null;
  prUrl?: string | null;
  operationStatus?: string | null;
};

/**
 * Build the Dev Spaces factory URL for the post-push Quality-tab CTA.
 * Returns null when config/repo are missing or push/PR has not happened yet.
 *
 * Intentionally omits `projectBranch`: Dev Spaces must target the remediation
 * branch or PR, never the catalog project's default branch.
 */
export function resolvePostPushDevSpacesUrl(
  options: ResolvePostPushDevSpacesUrlOptions,
): string | null {
  const {
    sessionVisible,
    devSpacesBaseUrl,
    repoUrl,
    pushedBranchName,
    prUrl,
    operationStatus,
  } = options;

  if (!sessionVisible || !devSpacesBaseUrl || !repoUrl) {
    return null;
  }

  const pushOrPrKnown = Boolean(
    pushedBranchName || prUrl || operationStatus === "pr_submitted",
  );
  if (!pushOrPrKnown) {
    return null;
  }

  return buildDevSpacesRemediationUrl(devSpacesBaseUrl, repoUrl, {
    branch: pushedBranchName,
    prUrl: pushedBranchName ? undefined : prUrl,
  });
}
