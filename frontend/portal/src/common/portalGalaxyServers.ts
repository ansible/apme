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

import { Config } from "@backstage/config";
import type { LoggerService } from "@backstage/backend-plugin-api";
import type { IApmeService } from "./ApmeService";
import { trimTrailingSlashes } from "./trimTrailingSlashes";
import type {
  CreateGalaxyServerRequest,
  GalaxyServer,
  UpdateGalaxyServerRequest,
} from "./types";

/** Prefix for galaxy servers managed by portal PAH sync bootstrap. */
export const PORTAL_HUB_GALAXY_SERVER_PREFIX = "portal_hub_";

/** PAH content repo path segment — same charset as user-facing galaxy server names. */
const PAH_REPO_NAME_RE = /^[A-Za-z0-9_-]+$/;

export interface PortalPahGalaxyServerSpec {
  name: string;
  url: string;
  token?: string;
}

/**
 * Normalize a PAH repository name into a stable galaxy-server id segment
 * (same rules as EE builder `normalizePahRepoIdentifier`).
 */
export function normalizePahRepoIdentifier(repo: string): string {
  const normalized = repo
    .toString()
    .trim()
    .toLowerCase()
    .replaceAll(/[^a-z0-9]+/g, "_")
    .replaceAll(/_+/g, "_");

  let start = 0;
  let end = normalized.length;
  while (start < end && normalized[start] === "_") {
    start += 1;
  }
  while (end > start && normalized[end - 1] === "_") {
    end -= 1;
  }

  return normalized.slice(start, end);
}

export function isPortalManagedGalaxyServerName(name: string): boolean {
  return name.startsWith(PORTAL_HUB_GALAXY_SERVER_PREFIX);
}

/**
 * Builds desired galaxy-server specs from
 * `catalog.providers.rhaap.<env>.sync.pahCollections` plus AAP credentials.
 */
export function buildPortalPahGalaxyServers(
  config: Config,
): PortalPahGalaxyServerSpec[] {
  const pahBaseUrl =
    config.getOptionalString("ansible.rhaap.baseUrl")?.trim() ?? "";
  if (!pahBaseUrl) {
    return [];
  }

  let base = pahBaseUrl;
  while (base.endsWith("/")) {
    base = base.slice(0, -1);
  }

  const token = config.getOptionalString("ansible.rhaap.token")?.trim();
  const providerConfigs = config.getOptionalConfig("catalog.providers.rhaap");
  if (!providerConfigs) {
    return [];
  }

  const servers: PortalPahGalaxyServerSpec[] = [];
  const seen = new Set<string>();

  for (const envId of providerConfigs.keys()) {
    const envConfig = providerConfigs.getConfig(envId);
    if (
      envConfig.has("sync.pahCollections.enabled") &&
      !envConfig.getBoolean("sync.pahCollections.enabled")
    ) {
      continue;
    }
    if (!envConfig.has("sync.pahCollections.repositories")) {
      continue;
    }
    const entries =
      envConfig.getOptionalConfigArray("sync.pahCollections.repositories") ??
      [];
    for (const entry of entries) {
      const repoName = entry.getString("name").trim();
      // Reject path metacharacters before interpolating into a credentialed URL.
      if (!PAH_REPO_NAME_RE.test(repoName)) {
        continue;
      }
      const normalizedRepo = normalizePahRepoIdentifier(repoName);
      if (!normalizedRepo || seen.has(normalizedRepo)) {
        continue;
      }
      seen.add(normalizedRepo);
      const spec: PortalPahGalaxyServerSpec = {
        name: `${PORTAL_HUB_GALAXY_SERVER_PREFIX}${normalizedRepo}`,
        url: `${base}/api/galaxy/content/${encodeURIComponent(repoName)}/`,
      };
      if (token) {
        spec.token = token;
      }
      servers.push(spec);
    }
  }

  return servers;
}

export interface SyncPortalGalaxyServersResult {
  created: number;
  updated: number;
  unchanged: number;
  deleted: number;
  desired: number;
}

function urlsEqual(a: string, b: string): boolean {
  const normalize = (u: string) => trimTrailingSlashes(u.trim());
  return normalize(a) === normalize(b);
}

/**
 * Upserts portal-managed galaxy servers on the gateway, then prunes
 * obsolete `portal_hub_*` entries not in `desired`.
 * Never deletes non-portal_hub_* servers (manual Quality-settings entries).
 */
export async function syncPortalGalaxyServers(
  apmeService: Pick<
    IApmeService,
    | "listGalaxyServers"
    | "createGalaxyServer"
    | "updateGalaxyServer"
    | "deleteGalaxyServer"
  >,
  desired: PortalPahGalaxyServerSpec[],
  logger?: LoggerService,
): Promise<SyncPortalGalaxyServersResult> {
  const result: SyncPortalGalaxyServersResult = {
    created: 0,
    updated: 0,
    unchanged: 0,
    deleted: 0,
    desired: desired.length,
  };

  if (desired.length === 0) {
    logger?.info(
      "No portal PAH galaxy servers desired; pruning obsolete portal_hub_* entries",
    );
  }

  const existing = await apmeService.listGalaxyServers();
  const byName = new Map<string, GalaxyServer>();
  for (const server of existing) {
    byName.set(server.name, server);
  }

  const desiredNames = new Set(desired.map((spec) => spec.name));

  for (const spec of desired) {
    const current = byName.get(spec.name);
    if (!current) {
      const body: CreateGalaxyServerRequest = {
        name: spec.name,
        url: spec.url,
      };
      if (spec.token) {
        body.token = spec.token;
      }
      await apmeService.createGalaxyServer(body); // NOSONAR - sequential galaxy server reconciliation
      result.created += 1;
      logger?.info(`Created portal galaxy server ${spec.name}`);
      continue;
    }

    const needsUrlUpdate = !urlsEqual(current.url, spec.url);
    // Gateway never returns token values; always refresh when we have one so
    // AAP token rotations converge on the hourly sync.
    const hasToken = Boolean(spec.token);

    if (!needsUrlUpdate && !hasToken) {
      result.unchanged += 1;
      continue;
    }

    // URL matches and token present — still push token, count as unchanged
    // when only the opaque token refresh runs with identical URL.
    if (!needsUrlUpdate && hasToken && current.has_token) {
      // prettier-ignore
      await apmeService.updateGalaxyServer(current.id, { token: spec.token }); // NOSONAR - sequential galaxy server reconciliation
      result.unchanged += 1;
      continue;
    }

    const patch: UpdateGalaxyServerRequest = {};
    if (needsUrlUpdate) {
      patch.url = spec.url;
    }
    if (spec.token) {
      patch.token = spec.token;
    }

    await apmeService.updateGalaxyServer(current.id, patch); // NOSONAR - sequential galaxy server reconciliation
    result.updated += 1;
    logger?.info(`Updated portal galaxy server ${spec.name}`);
  }

  for (const server of existing) {
    if (!isPortalManagedGalaxyServerName(server.name)) {
      continue;
    }
    if (desiredNames.has(server.name)) {
      continue;
    }
    await apmeService.deleteGalaxyServer(server.id); // NOSONAR - sequential galaxy server reconciliation
    result.deleted += 1;
    logger?.info(`Deleted obsolete portal galaxy server ${server.name}`);
  }

  return result;
}
