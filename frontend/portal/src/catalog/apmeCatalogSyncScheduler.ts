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
  AuthService,
  LoggerService,
  SchedulerService,
} from "@backstage/backend-plugin-api";
import { CatalogClient } from "@backstage/catalog-client";
import { Config } from "@backstage/config";
import type { Entity } from "@backstage/catalog-model";
import {
  IApmeService,
  isApmeMockMode,
  readApmeGitContentsSyncConfigs,
} from "../common/index";
import { runApmeCatalogSyncBatch } from "./apmeCatalogSyncTask";

const DEFAULT_SYNC_SCHEDULE = {
  frequency: { minutes: 60 },
  timeout: { minutes: 30 },
};

export interface RegisterApmeCatalogSyncTasksOptions {
  scheduler: SchedulerService;
  catalogClient: CatalogClient;
  auth: AuthService;
  apmeService: IApmeService;
  rootConfig: Config;
  logger: LoggerService;
  resolveScanVersion?: (projectId: string) => Promise<string>;
  resolveEnableAi?: () => Promise<boolean>;
  /** Called when a sync task throws an unrecoverable error. */
  onSyncError?: (env: string, err: Error) => void;
  /** Called on the first successful sync run after a previous failure. */
  onSyncRecovered?: (env: string) => void;
  shouldTrackEntity?: (entity: Entity) => boolean;
}

/** Registers scheduled bulk-sync tasks for each enabled ansibleGitContents.apme block. */
export function registerApmeCatalogSyncTasks(
  options: RegisterApmeCatalogSyncTasksOptions,
): void {
  const {
    scheduler,
    catalogClient,
    auth,
    apmeService,
    rootConfig,
    logger,
    resolveScanVersion,
    resolveEnableAi,
    onSyncError,
    onSyncRecovered,
  } = options;

  if (isApmeMockMode(rootConfig)) {
    logger.info("APME mockMode is enabled; skipping catalog bulk sync tasks");
    return;
  }

  const syncConfigs = readApmeGitContentsSyncConfigs(rootConfig);
  if (syncConfigs.length === 0) {
    logger.info(
      "No APME bulk sync scopes configured; skipping scheduler tasks",
    );
    return;
  }

  const offsets = new Map<string, number>();
  // Track which envs are currently in a failed state to avoid duplicate DEGRADED pushes
  // and to detect recovery (first success after a failure).
  const failedEnvs = new Set<string>();

  for (const syncConfig of syncConfigs) {
    const taskId = `apme-catalog-sync-${syncConfig.env}`;
    const schedule = syncConfig.schedule ?? DEFAULT_SYNC_SCHEDULE;
    const taskLogger = logger.child({ task: taskId });
    const env = syncConfig.env;

    void scheduler.scheduleTask({
      id: taskId,
      frequency: schedule.frequency,
      timeout: schedule.timeout,
      fn: async () => {
        const offset = offsets.get(taskId) ?? 0;
        try {
          const summary = await runApmeCatalogSyncBatch({
            apmeService,
            catalogClient,
            auth,
            logger: taskLogger,
            syncConfig,
            offset,
            shouldTrackEntity: options.shouldTrackEntity,
            resolveScanVersion,
            resolveEnableAi,
          });
          offsets.set(taskId, summary.nextOffset ?? 0);
          // Notify recovery if this env was previously failing
          if (failedEnvs.has(env)) {
            failedEnvs.delete(env);
            onSyncRecovered?.(env);
          }
        } catch (err) {
          const error = err instanceof Error ? err : new Error(String(err));
          taskLogger.error(
            `APME catalog sync failed for env=${env}: ${error.message}`,
          );
          failedEnvs.add(env);
          onSyncError?.(env, error);
          // Re-throw so Backstage scheduler can log and track the failure
          throw error;
        }
      },
    });

    taskLogger.info(
      `Registered APME catalog bulk sync for env=${syncConfig.env} (maxPerRun=${
        syncConfig.maxPerRun
      }, orgs=${syncConfig.orgs.map((o) => o.organization).join(", ")})`,
    );
  }
}
