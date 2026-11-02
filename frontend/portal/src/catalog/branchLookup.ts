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

import type { LoggerService } from "@backstage/backend-plugin-api";
import type { Config } from "@backstage/config";
import { InputError } from "@backstage/errors";
import {
  DefaultGithubCredentialsProvider,
  ScmIntegrations,
} from "@backstage/integration";
import { normalizeRepoUrl } from "../common/index";

type ScmProvider = "github" | "gitlab";

export interface ParsedGitRepo {
  provider: ScmProvider;
  host: string;
  owner: string;
  repo: string;
}

export interface ValidateRepoBranchOptions {
  rootConfig: Config;
  repoUrl: string;
  branch: string;
  scmToken?: string;
  logger?: LoggerService;
}

function authHeaders(token?: string): Record<string, string> {
  if (!token?.trim()) {
    return {};
  }
  return { Authorization: `Bearer ${token.trim()}` };
}

/** Last path segment, or empty string when the list is empty. */
export function lastPathSegment(segments: string[]): string {
  return segments.at(-1) ?? "";
}

/** Parses a normalized GitHub/GitLab HTTPS clone URL into SCM coordinates. */
export function parseGitRepoUrl(repoUrl: string): ParsedGitRepo {
  const normalized = normalizeRepoUrl(repoUrl);
  let url: URL;
  try {
    url = new URL(normalized);
  } catch {
    throw new InputError(`Invalid repository URL: ${repoUrl}`);
  }

  const host = url.hostname.toLowerCase();
  const segments = url.pathname.split("/").filter(Boolean);
  if (segments.length < 2) {
    throw new InputError(`Invalid repository URL: ${repoUrl}`);
  }

  const provider: ScmProvider = host.includes("github") ? "github" : "gitlab";
  if (provider === "github") {
    return {
      provider,
      host,
      owner: segments[0],
      repo: segments[1],
    };
  }

  return {
    provider,
    host,
    owner: segments.slice(0, -1).join("/"),
    repo: lastPathSegment(segments),
  };
}

function githubApiBase(rootConfig: Config, host: string): string {
  const integration =
    ScmIntegrations.fromConfig(rootConfig).github.byHost(host);
  return integration?.config.apiBaseUrl ?? `https://api.${host}`;
}

function gitlabApiBase(rootConfig: Config, host: string): string {
  const integration =
    ScmIntegrations.fromConfig(rootConfig).gitlab.byHost(host);
  return integration?.config.apiBaseUrl ?? `https://${host}/api/v4`;
}

function integrationToken(
  rootConfig: Config,
  provider: ScmProvider,
  host: string,
): string | undefined {
  if (provider === "github") {
    return ScmIntegrations.fromConfig(rootConfig).github.byHost(host)?.config
      .token;
  }
  return ScmIntegrations.fromConfig(rootConfig).gitlab.byHost(host)?.config
    .token;
}

/**
 * Prefer request scmToken, then GitHub App installation token, then PAT.
 * App-only configs have no integrations.github[].token — unauthenticated
 * api.github.com calls fail under rate limit (403) and look like "metadata" errors.
 */
async function resolveScmAuthToken(
  rootConfig: Config,
  provider: ScmProvider,
  host: string,
  owner: string,
  repo: string,
  scmToken?: string,
  logger?: LoggerService,
): Promise<string | undefined> {
  const trimmed = scmToken?.trim();
  if (trimmed) {
    return trimmed;
  }

  if (provider === "github") {
    const integrations = ScmIntegrations.fromConfig(rootConfig);
    const credentialsProvider =
      DefaultGithubCredentialsProvider.fromIntegrations(integrations);
    const url = `https://${host}/${owner}/${repo}`;
    try {
      const credentials = await credentialsProvider.getCredentials({ url });
      if (credentials.token?.trim()) {
        return credentials.token.trim();
      }
    } catch (err) {
      logger?.debug(
        `APME branch validation: GitHub App credentials unavailable for ${url}: ${
          err instanceof Error ? err.message : String(err)
        }`,
      );
    }
  }

  return integrationToken(rootConfig, provider, host)?.trim();
}

async function fetchGithubDefaultBranch(
  apiBaseUrl: string,
  owner: string,
  repo: string,
  token?: string,
): Promise<{ defaultBranch?: string; status: number }> {
  const response = await fetch(
    `${apiBaseUrl}/repos/${encodeURIComponent(owner)}/${encodeURIComponent(
      repo,
    )}`,
    {
      headers: {
        Accept: "application/vnd.github+json",
        ...authHeaders(token),
      },
    },
  );
  if (!response.ok) {
    return { status: response.status };
  }
  const data = (await response.json()) as { default_branch?: string };
  return {
    status: response.status,
    defaultBranch: data.default_branch?.trim() || undefined,
  };
}

async function githubBranchExists(
  apiBaseUrl: string,
  owner: string,
  repo: string,
  branch: string,
  token?: string,
): Promise<boolean> {
  const response = await fetch(
    `${apiBaseUrl}/repos/${encodeURIComponent(owner)}/${encodeURIComponent(
      repo,
    )}/branches/${encodeURIComponent(branch)}`,
    {
      method: "HEAD",
      headers: authHeaders(token),
    },
  );
  return response.ok;
}

async function fetchGitlabDefaultBranch(
  apiBaseUrl: string,
  owner: string,
  repo: string,
  token?: string,
): Promise<string | undefined> {
  const projectPath = `${owner}/${repo}`;
  const response = await fetch(
    `${apiBaseUrl}/projects/${encodeURIComponent(projectPath)}`,
    {
      headers: authHeaders(token),
    },
  );
  if (!response.ok) {
    return undefined;
  }
  const data = (await response.json()) as { default_branch?: string };
  return data.default_branch?.trim() || undefined;
}

async function gitlabBranchExists(
  apiBaseUrl: string,
  owner: string,
  repo: string,
  branch: string,
  token?: string,
): Promise<boolean> {
  const response = await fetch(
    `${apiBaseUrl}/projects/${encodeURIComponent(
      `${owner}/${repo}`,
    )}/repository/branches/${encodeURIComponent(branch)}`,
    {
      method: "HEAD",
      headers: authHeaders(token),
    },
  );
  return response.ok;
}

/**
 * Validates that a branch exists in the SCM before APME clone/scan.
 * Throws InputError with an actionable message when the branch is missing.
 */
export async function validateRepoBranch(
  options: ValidateRepoBranchOptions,
): Promise<void> {
  const { rootConfig, repoUrl, logger } = options;
  const branch = options.branch?.trim();
  if (!branch) {
    throw new InputError("branch is required");
  }

  const { provider, host, owner, repo } = parseGitRepoUrl(repoUrl);
  const token = await resolveScmAuthToken(
    rootConfig,
    provider,
    host,
    owner,
    repo,
    options.scmToken,
    logger,
  );

  const apiBase =
    provider === "github"
      ? githubApiBase(rootConfig, host)
      : gitlabApiBase(rootConfig, host);

  let defaultBranch: string | undefined;
  let metadataStatus: number | undefined;
  if (provider === "github") {
    const meta = await fetchGithubDefaultBranch(apiBase, owner, repo, token);
    defaultBranch = meta.defaultBranch;
    metadataStatus = meta.status;
  } else {
    defaultBranch = await fetchGitlabDefaultBranch(apiBase, owner, repo, token);
  }

  if (!defaultBranch) {
    const statusHint =
      metadataStatus !== undefined ? ` (SCM HTTP ${metadataStatus})` : "";
    throw new InputError(
      `Could not load repository metadata for ${owner}/${repo}${statusHint}. ` +
        "Check the repository URL and Backstage SCM integration credentials " +
        "(GitHub App installation or PAT under integrations.github).",
    );
  }

  const branchExists =
    provider === "github"
      ? await githubBranchExists(apiBase, owner, repo, branch, token)
      : await gitlabBranchExists(apiBase, owner, repo, branch, token);

  if (branchExists) {
    return;
  }

  logger?.warn(
    `APME branch validation failed: branch '${branch}' not found in ${owner}/${repo} ` +
      `(default '${defaultBranch}')`,
  );

  throw new InputError(
    `Branch '${branch}' was not found in ${owner}/${repo}. ` +
      `Use the repository default branch '${defaultBranch}' or verify the branch name in your SCM.`,
  );
}
