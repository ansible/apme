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
import { Entity } from "@backstage/catalog-model";
import { Config } from "@backstage/config";
import {
  EntityProvider,
  EntityProviderConnection,
} from "@backstage/plugin-catalog-node";
import {
  defaultBranchFromEntity,
  IApmeService,
  isApmeMockMode,
  normalizeRepoUrlFromEntity,
} from "../../common/index";
import {
  buildLearnedCollectionEntity,
  findCanonicalCollectionEntity,
  indexCatalogCollectionsByFqcn,
} from "./learnedCollectionEntity";

const PROVIDER_NAME = "ApmeLearnedDepsEntityProvider";
const SYNC_TASK_ID = "apme-learned-deps-sync";
/** Bound concurrent APME lookups so large fleets finish within the task timeout. */
const LEARNED_DEPS_FETCH_CONCURRENCY = 8;

type RepoSyncOutcome =
  { kind: "entities"; entities: Entity[] } | { kind: "abort"; message: string };

/**
 * Run async work over items with a fixed worker pool.
 * Exported for unit tests.
 */
export async function mapPool<T, R>(
  items: readonly T[],
  concurrency: number,
  fn: (item: T, index: number) => Promise<R>,
): Promise<R[]> {
  if (items.length === 0) {
    return [];
  }
  const results = new Array<R>(items.length);
  let nextIndex = 0;
  const workerCount = Math.min(Math.max(1, concurrency), items.length);

  const workers = Array.from({ length: workerCount }, async () => {
    let index = nextIndex;
    nextIndex += 1;
    while (index < items.length) {
      results[index] = await fn(items[index], index); // NOSONAR - bounded worker pool concurrency
      index = nextIndex;
      nextIndex += 1;
    }
  });

  await Promise.all(workers);
  return results;
}

export interface ApmeLearnedDepsEntityProviderOptions {
  apmeService: IApmeService;
  catalogClient: CatalogClient;
  auth: AuthService;
  logger: LoggerService;
  rootConfig: Config;
  shouldTrackEntity?: (entity: Entity) => boolean;
}

/**
 * Publishes synthetic ansible-collection entities for APME learned deps,
 * linked to consuming git-repository entities via consumed-by-repository.
 */
export class ApmeLearnedDepsEntityProvider implements EntityProvider {
  private connection?: EntityProviderConnection;
  private readonly apmeService: IApmeService;
  private readonly catalogClient: CatalogClient;
  private readonly auth: AuthService;
  private readonly logger: LoggerService;
  private readonly rootConfig: Config;
  private readonly shouldTrackEntity?: (entity: Entity) => boolean;

  constructor(options: ApmeLearnedDepsEntityProviderOptions) {
    this.apmeService = options.apmeService;
    this.catalogClient = options.catalogClient;
    this.auth = options.auth;
    this.logger = options.logger.child({ provider: PROVIDER_NAME });
    this.rootConfig = options.rootConfig;
    this.shouldTrackEntity = options.shouldTrackEntity;
  }

  getProviderName(): string {
    return PROVIDER_NAME;
  }

  async connect(connection: EntityProviderConnection): Promise<void> {
    await Promise.resolve();
    this.connection = connection;
    this.logger.info(`${PROVIDER_NAME} connected`);
  }

  async schedule(scheduler: SchedulerService): Promise<void> {
    await Promise.resolve();
    if (isApmeMockMode(this.rootConfig)) {
      this.logger.info(
        "APME mockMode is enabled; skipping learned deps sync schedule",
      );
      return;
    }

    void scheduler.scheduleTask({
      id: SYNC_TASK_ID,
      frequency: { minutes: 30 },
      timeout: { minutes: 15 },
      fn: async () => {
        try {
          await this.runFullSync();
        } catch (error) {
          this.logger.warn(
            `Learned deps sync failed: ${(error as Error).message}`,
          );
        }
      },
    });

    this.logger.info(`Registered ${SYNC_TASK_ID} (every 30m)`);

    // Kick once after connect settles (connection may not be ready yet).
    setTimeout(() => {
      void this.runFullSync().catch((error) => {
        this.logger.warn(
          `Initial learned deps sync failed: ${(error as Error).message}`,
        );
      });
    }, 15_000);
  }

  async runFullSync(): Promise<void> {
    if (!this.connection) {
      this.logger.debug("Skipping learned deps sync; provider not connected");
      return;
    }

    if (isApmeMockMode(this.rootConfig)) {
      return;
    }

    const { token } = await this.auth.getPluginRequestToken({
      onBehalfOf: await this.auth.getOwnServiceCredentials(),
      targetPluginId: "catalog",
    });

    const [reposResponse, collectionsResponse] = await Promise.all([
      this.catalogClient.getEntities(
        { filter: [{ kind: "Component", "spec.type": "git-repository" }] },
        { token },
      ),
      this.catalogClient.getEntities(
        {
          filter: [{ kind: "Component", "spec.type": "ansible-collection" }],
        },
        { token },
      ),
    ]);

    const repos = reposResponse.items;
    const catalogIndex = indexCatalogCollectionsByFqcn(
      collectionsResponse.items,
    );

    const outcomes = await mapPool(
      repos,
      LEARNED_DEPS_FETCH_CONCURRENCY,
      async (repo): Promise<RepoSyncOutcome> => {
        if (this.shouldTrackEntity?.(repo) === false) {
          return { kind: "entities", entities: [] };
        }
        const repoUrl = normalizeRepoUrlFromEntity(repo);
        if (!repoUrl) {
          return { kind: "entities", entities: [] };
        }
        const branch = defaultBranchFromEntity(repo);

        let project;
        try {
          project = await this.apmeService.getProjectByRepoUrl(repoUrl, branch);
        } catch (error) {
          return {
            kind: "abort",
            message: `project lookup failed for ${repoUrl}: ${
              (error as Error).message
            }`,
          };
        }
        if (!project) {
          // Not registered in APME — skip (client maps 404 → null).
          return { kind: "entities", entities: [] };
        }

        let dependencies;
        try {
          dependencies = await this.apmeService.getProjectDependencies(
            project.id,
          );
        } catch (error) {
          return {
            kind: "abort",
            message: `dependencies failed for project ${project.id}: ${
              (error as Error).message
            }`,
          };
        }

        if (this.shouldTrackEntity?.(repo) === false) {
          return { kind: "entities", entities: [] };
        }
        const entities: Entity[] = [];
        for (const collection of dependencies.collections ?? []) {
          const canonical = findCanonicalCollectionEntity(
            catalogIndex,
            collection.fqcn,
            collection.version,
          );
          const entity = buildLearnedCollectionEntity({
            repoEntity: repo,
            projectId: project.id,
            collection,
            canonicalEntityName: canonical?.metadata?.name,
          });
          if (entity) {
            entities.push(entity);
          }
        }
        return { kind: "entities", entities };
      },
    );

    const abort = outcomes.find((outcome) => outcome.kind === "abort");
    if (abort?.kind === "abort") {
      // Do not full-replace with a partial set — that would drop previously
      // published learned deps for repos we failed to fetch.
      this.logger.warn(`Learned deps sync aborted: ${abort.message}`);
      return;
    }

    const entities = outcomes.flatMap((outcome) =>
      outcome.kind === "entities" ? outcome.entities : [],
    );

    await this.connection.applyMutation({
      type: "full",
      entities: entities.map((entity) => ({
        entity,
        locationKey: PROVIDER_NAME,
      })),
    });

    this.logger.info(
      `Learned deps sync applied ${entities.length} collection entit${
        entities.length === 1 ? "y" : "ies"
      } for ${repos.length} git-repositor${repos.length === 1 ? "y" : "ies"}`,
    );
  }
}
