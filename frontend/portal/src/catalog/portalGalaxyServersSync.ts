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

import { LoggerService, SchedulerService } from "@backstage/backend-plugin-api";
import { Config } from "@backstage/config";
import {
  buildPortalPahGalaxyServers,
  IApmeService,
  isApmeMockMode,
  syncPortalGalaxyServers,
} from "../common/index";

const GALAXY_SYNC_TASK_ID = "apme-portal-galaxy-servers-sync";

export interface RegisterPortalGalaxyServersSyncOptions {
  scheduler: SchedulerService;
  apmeService: IApmeService;
  rootConfig: Config;
  logger: LoggerService;
}

/** Syncs portal PAH galaxy servers once, then hourly. */
export async function registerPortalGalaxyServersSync(
  options: RegisterPortalGalaxyServersSyncOptions,
): Promise<void> {
  const { scheduler, apmeService, rootConfig, logger } = options;

  if (isApmeMockMode(rootConfig)) {
    logger.info("APME mockMode is enabled; skipping portal galaxy server sync");
    return;
  }

  const runSync = async () => {
    const desired = buildPortalPahGalaxyServers(rootConfig);
    const result = await syncPortalGalaxyServers(apmeService, desired, logger);
    logger.info(
      `Portal galaxy servers sync: desired=${result.desired}, created=${result.created}, updated=${result.updated}, unchanged=${result.unchanged}, deleted=${result.deleted}`,
    );
  };

  try {
    await runSync();
  } catch (error) {
    logger.warn(
      `Initial portal galaxy servers sync failed: ${(error as Error).message}`,
    );
  }

  void scheduler.scheduleTask({
    id: GALAXY_SYNC_TASK_ID,
    frequency: { hours: 1 },
    timeout: { minutes: 5 },
    fn: async () => {
      try {
        await runSync();
      } catch (error) {
        logger.warn(
          `Scheduled portal galaxy servers sync failed: ${
            (error as Error).message
          }`,
        );
      }
    },
  });

  logger.info(`Registered ${GALAXY_SYNC_TASK_ID} (hourly)`);
}
