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

import { AuthService, LoggerService } from "@backstage/backend-plugin-api";
import { CatalogClient } from "@backstage/catalog-client";
import { Entity } from "@backstage/catalog-model";
import {
  ApmeCatalogSyncSummary,
  ApmeGitContentsSyncConfig,
  createProjectRequestFromEntity,
  IApmeService,
  registerOrResolveApmeProject,
  scanOnRegisterForEntity,
  selectEntitiesForApmeSync,
  sliceApmeSyncBatch,
} from "../common/index";

const DEFAULT_CATALOG_TOKEN = { token: "service-token" };

export interface ApmeCatalogSyncTaskOptions {
  apmeService: IApmeService;
  catalogClient: CatalogClient;
  auth: AuthService;
  logger: LoggerService;
  syncConfig: ApmeGitContentsSyncConfig;
  offset: number;
  resolveScanVersion?: (projectId: string) => Promise<string>;
  /** Portal settings enableAi (defaults false when unset). */
  resolveEnableAi?: () => Promise<boolean>;
  shouldTrackEntity?: (entity: Entity) => boolean;
}

async function getCatalogEntities(
  catalogClient: CatalogClient,
  auth: AuthService,
): Promise<Entity[]> {
  const { token } = await auth.getPluginRequestToken({
    onBehalfOf: await auth.getOwnServiceCredentials(),
    targetPluginId: "catalog",
  });

  const response = await catalogClient.getEntities(
    {
      filter: [{ kind: "Component", "spec.type": "git-repository" }],
    },
    { token },
  );

  return response.items;
}

/** Registers (and optionally scans) catalog repos with the APME gateway for one sync block. */
export async function runApmeCatalogSyncBatch(
  options: ApmeCatalogSyncTaskOptions,
): Promise<ApmeCatalogSyncSummary> {
  const {
    apmeService,
    catalogClient,
    auth,
    logger,
    syncConfig,
    offset,
    resolveScanVersion,
    resolveEnableAi,
  } = options;

  const summary: ApmeCatalogSyncSummary = {
    registered: 0,
    scanned: 0,
    skipped: 0,
    errors: [],
  };

  let entities: Entity[];
  try {
    entities = await getCatalogEntities(catalogClient, auth);
  } catch (error) {
    const message = `Failed to read catalog entities: ${
      (error as Error).message
    }`;
    logger.error(message);
    throw new Error(message, { cause: error });
  }

  const eligible = selectEntitiesForApmeSync(entities, syncConfig);
  const { batch, nextOffset, remaining } = sliceApmeSyncBatch(
    eligible,
    offset,
    syncConfig.maxPerRun,
  );

  summary.remaining = remaining;
  summary.nextOffset = nextOffset;

  for (const entity of batch) {
    if (options.shouldTrackEntity?.(entity) === false) {
      summary.skipped += 1;
      continue;
    }
    const request = createProjectRequestFromEntity(entity);
    if (!request) {
      summary.skipped += 1;
      summary.errors.push(
        `Skipped ${entity.metadata.name ?? "entity"}: no repo URL`,
      );
      continue;
    }

    try {
      // prettier-ignore
      const existing = await apmeService.getProjectByRepoUrl(request.repo_url, request.branch); // NOSONAR - sequential catalog sync batch
      if (options.shouldTrackEntity?.(entity) === false) {
        summary.skipped += 1;
        continue;
      }
      const project =
        existing ?? (await registerOrResolveApmeProject(apmeService, request)); // NOSONAR - sequential catalog sync batch
      if (!existing) {
        summary.registered += 1;
      }

      if (scanOnRegisterForEntity(entity, syncConfig)) {
        const ansibleVersion = resolveScanVersion
          ? await resolveScanVersion(project.id) // NOSONAR - sequential catalog sync batch
          : undefined;
        const enableAi = resolveEnableAi
          ? await resolveEnableAi() // NOSONAR - sequential catalog sync batch
          : undefined;
        if (options.shouldTrackEntity?.(entity) === false) {
          summary.skipped += 1;
          continue;
        }
        // prettier-ignore
        await apmeService.triggerScan(project.id, { ansibleVersion, ...(enableAi !== undefined ? { enableAi } : {}) }); // NOSONAR - sequential catalog sync batch
        summary.scanned += 1;
      }
    } catch (error) {
      const message = `Failed to sync ${request.repo_url}: ${
        (error as Error).message
      }`;
      logger.warn(message);
      summary.errors.push(message);
    }
  }

  logger.info(
    `APME catalog sync (${syncConfig.env}): registered=${
      summary.registered
    }, scanned=${summary.scanned}, skipped=${summary.skipped}, remaining=${
      summary.remaining ?? 0
    }`,
  );

  return summary;
}

/** Test helper — bypasses auth when catalog client is mocked. */
export const __testing__ = {
  DEFAULT_CATALOG_TOKEN,
};
