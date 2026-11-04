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

import { Entity } from "@backstage/catalog-model";
import {
  defaultBranchFromEntity,
  normalizeRepoUrlFromEntity,
  scmOrganizationFromEntity,
} from "./catalogEntity";
import { ApmeGitContentsSyncConfig, ApmeOrgSyncScope } from "./apmeSyncConfig";
import { CreateProjectRequest } from "./types";

/** Returns true when a catalog entity falls within an opted-in org scope. */
export function entityMatchesApmeOrgScope(
  entity: Entity,
  scope: ApmeOrgSyncScope,
): boolean {
  const org = scmOrganizationFromEntity(entity);
  const repoUrl = normalizeRepoUrlFromEntity(entity);
  let host: string | undefined;
  try {
    host = repoUrl ? new URL(repoUrl).hostname.toLowerCase() : undefined;
  } catch {
    return false;
  }
  const annotations = entity.metadata.annotations ?? {};
  const annotatedProvider = annotations["ansible.io/scm-provider"];
  const provider =
    annotatedProvider === "github" || annotatedProvider === "gitlab"
      ? annotatedProvider
      : host === "github.com"
        ? "github"
        : host === "gitlab.com"
          ? "gitlab"
          : undefined;
  if (
    org !== scope.organization ||
    host !== scope.host.toLowerCase() ||
    provider !== scope.provider
  ) {
    return false;
  }

  if (scope.labels?.length) {
    const tags = entity.metadata.tags ?? [];
    if (!scope.labels.every((label) => tags.includes(label))) {
      return false;
    }
  }

  return true;
}

/** Filters git-repository entities to those in opted-in org scopes for a sync block. */
export function selectEntitiesForApmeSync(
  entities: Entity[],
  syncConfig: ApmeGitContentsSyncConfig,
): Entity[] {
  return entities.filter((entity) => {
    return syncConfig.orgs.some((scope) =>
      entityMatchesApmeOrgScope(entity, scope),
    );
  });
}

/** Stable sort key for cursor-based batching. */
export function apmeSyncEntityKey(entity: Entity): string {
  return entity.metadata.uid ?? entity.metadata.name ?? "";
}

/** Returns the batch slice and next cursor offset for iterative bulk sync. */
export function sliceApmeSyncBatch(
  entities: Entity[],
  offset: number,
  maxPerRun: number,
): { batch: Entity[]; nextOffset: number; remaining: number } {
  const sorted = [...entities].sort((a, b) =>
    apmeSyncEntityKey(a).localeCompare(apmeSyncEntityKey(b)),
  );
  const batch = sorted.slice(offset, offset + maxPerRun);
  const nextOffset =
    offset + maxPerRun >= sorted.length ? 0 : offset + maxPerRun;
  const remaining =
    nextOffset === 0 ? 0 : Math.max(sorted.length - nextOffset, 0);

  return { batch, nextOffset, remaining };
}

/** Resolves scanOnRegister for an entity from its org scope. */
export function scanOnRegisterForEntity(
  entity: Entity,
  syncConfig: ApmeGitContentsSyncConfig,
): boolean {
  const scope = syncConfig.orgs.find((candidate) =>
    entityMatchesApmeOrgScope(entity, candidate),
  );
  return scope?.scanOnRegister ?? syncConfig.scanOnRegister;
}

/** Builds a gateway create-project request from a catalog entity. */
export function createProjectRequestFromEntity(
  entity: Entity,
): CreateProjectRequest | null {
  const repoUrl = normalizeRepoUrlFromEntity(entity);
  if (!repoUrl) {
    return null;
  }

  const spec = entity.spec as { repository_name?: string } | undefined;
  const name =
    entity.metadata.name ??
    spec?.repository_name ??
    repoUrl.split("/").findLast(Boolean) ??
    "repository";

  return {
    name: String(name),
    repo_url: repoUrl,
    branch: defaultBranchFromEntity(entity),
  };
}
