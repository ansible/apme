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

import { buildDevSpacesUrlFromRepoUrl } from "@ansible/backstage-rhaap-common/devSpaces";

export type ResolveDefaultBranchDevSpacesUrlOptions = {
  devSpacesBaseUrl?: string;
  repoUrl?: string | null;
  /** Catalog default branch; falls back to main when omitted. */
  branch?: string | null;
};

/** Dev Spaces factory URL for a repo on its configured default branch (US-011). */
export function resolveDefaultBranchDevSpacesUrl(
  options: ResolveDefaultBranchDevSpacesUrlOptions,
): string | null {
  const { devSpacesBaseUrl, repoUrl, branch } = options;
  if (!devSpacesBaseUrl || !repoUrl) {
    return null;
  }
  return buildDevSpacesUrlFromRepoUrl(
    devSpacesBaseUrl,
    repoUrl,
    branch?.trim() || "main",
  );
}
