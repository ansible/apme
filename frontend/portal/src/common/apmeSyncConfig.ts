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
  readSchedulerServiceTaskScheduleDefinitionFromConfig,
  SchedulerServiceTaskScheduleDefinition,
} from "@backstage/backend-plugin-api";
import { Config } from "@backstage/config";
import { isApmeEnabled } from "./config";

export interface ApmeOrgSyncScope {
  env: string;
  organization: string;
  scanOnRegister: boolean;
  labels?: string[];
}

export interface ApmeGitContentsSyncConfig {
  env: string;
  enabled: boolean;
  scanOnRegister: boolean;
  maxPerRun: number;
  labels?: string[];
  schedule?: SchedulerServiceTaskScheduleDefinition;
  orgs: ApmeOrgSyncScope[];
}

const SCM_PROVIDERS = ["github", "gitlab"] as const;
const DEFAULT_MAX_PER_RUN = 10;

function readScheduleFromConfig(
  config: Config,
): SchedulerServiceTaskScheduleDefinition | undefined {
  if (!config.has("schedule")) {
    return undefined;
  }
  return readSchedulerServiceTaskScheduleDefinitionFromConfig(
    config.getConfig("schedule"),
  );
}

function collectOrgScopes(
  env: string,
  gitContents: Config,
  blockScanOnRegister: boolean,
  labels?: string[],
): ApmeOrgSyncScope[] {
  if (!gitContents.has("providers")) {
    return [];
  }

  const providers = gitContents.getConfig("providers");
  const orgScopes: ApmeOrgSyncScope[] = [];

  for (const scmProvider of SCM_PROVIDERS) {
    if (!providers.has(scmProvider)) {
      continue;
    }
    for (const hostConfig of providers.getConfigArray(scmProvider)) {
      if (!hostConfig.has("orgs")) {
        continue;
      }
      for (const orgConfig of hostConfig.getConfigArray("orgs")) {
        const orgApme = orgConfig.getOptionalConfig("apme");
        if (orgApme?.getOptionalBoolean("enabled") !== true) {
          continue;
        }
        orgScopes.push({
          env,
          organization: orgConfig.getString("name"),
          scanOnRegister:
            orgApme?.getOptionalBoolean("scanOnRegister") ??
            blockScanOnRegister,
          labels,
        });
      }
    }
  }

  return orgScopes;
}

/**
 * Reads bulk sync scope from ansibleGitContents.apme blocks (ADR-009).
 * Returns empty when ansible.apme.enabled is false or no apme blocks are enabled.
 */
export function readApmeGitContentsSyncConfigs(
  config: Config,
): ApmeGitContentsSyncConfig[] {
  if (!isApmeEnabled(config)) {
    return [];
  }

  const rhaapProviders = config.getOptionalConfig("catalog.providers.rhaap");
  if (!rhaapProviders) {
    return [];
  }

  const blocks: ApmeGitContentsSyncConfig[] = [];

  for (const env of rhaapProviders.keys()) {
    const gitContents = rhaapProviders
      .getConfig(env)
      .getOptionalConfig("sync.ansibleGitContents");
    if (!gitContents || gitContents.getOptionalBoolean("enabled") === false) {
      continue;
    }

    const apmeBlock = gitContents.getOptionalConfig("apme");
    if (!apmeBlock?.getOptionalBoolean("enabled")) {
      continue;
    }

    const blockScanOnRegister =
      apmeBlock.getOptionalBoolean("scanOnRegister") ?? false;
    const maxPerRun =
      apmeBlock.getOptionalNumber("maxPerRun") ?? DEFAULT_MAX_PER_RUN;
    const labels = apmeBlock.getOptionalStringArray("labels");
    const schedule =
      readScheduleFromConfig(apmeBlock) ?? readScheduleFromConfig(gitContents);
    const orgScopes = collectOrgScopes(
      env,
      gitContents,
      blockScanOnRegister,
      labels,
    );

    if (orgScopes.length === 0) {
      continue;
    }

    blocks.push({
      env,
      enabled: true,
      scanOnRegister: blockScanOnRegister,
      maxPerRun,
      labels,
      schedule,
      orgs: orgScopes,
    });
  }

  return blocks;
}

export interface ApmeCatalogSyncSummary {
  registered: number;
  scanned: number;
  skipped: number;
  errors: string[];
  /** Entities remaining in backlog after this iterative batch. */
  remaining?: number;
  /** Cursor offset for the next iterative run. */
  nextOffset?: number;
}
