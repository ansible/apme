import type { Entity } from "@backstage/catalog-model";
import {
  ANNOTATION_LOCATION,
  ANNOTATION_ORIGIN_LOCATION,
  stringifyEntityRef,
} from "@backstage/catalog-model";
import type {
  EntityProvider,
  EntityProviderConnection,
} from "@backstage/plugin-catalog-node";

/** Catalog persists the provider bucket; delta mutations never wipe unrelated registrations. */
export class ManualGitRepositoryProvider implements EntityProvider {
  private connection?: EntityProviderConnection;
  private readonly stopped = new Set<string>();

  getProviderName(): string {
    return "ManualGitRepositoryProvider";
  }
  async connect(connection: EntityProviderConnection): Promise<void> {
    await Promise.resolve();
    this.connection = connection;
  }

  isTrackingStopped(entity: Entity): boolean {
    return this.stopped.has(stringifyEntityRef(entity));
  }

  private validate(entity: Entity): EntityProviderConnection {
    if (!this.connection)
      throw new Error("Manual repository provider is not connected");
    if (
      entity.kind.toLowerCase() !== "component" ||
      entity.spec?.type !== "git-repository" ||
      entity.metadata.annotations?.["ansible.io/registration-method"] !==
        "manual"
    ) {
      throw new Error(
        "Only manually registered Git repositories are supported",
      );
    }
    return this.connection;
  }

  async registerRepository(entity: Entity): Promise<void> {
    const connection = this.validate(entity);
    // Provider ownership and catalog processing provenance are separate contracts.
    // Never inherit provenance from template input or pretend the Git URL is a
    // catalog descriptor: this provider, not catalog-info.yaml, supplies the entity.
    const location = `apme-manual:${this.getProviderName()}`;
    const registeredEntity: Entity = {
      ...entity,
      metadata: {
        ...entity.metadata,
        annotations: {
          ...entity.metadata.annotations,
          [ANNOTATION_LOCATION]: location,
          [ANNOTATION_ORIGIN_LOCATION]: location,
        },
      },
    };
    await connection.applyMutation({
      type: "delta",
      added: [
        { entity: registeredEntity, locationKey: this.getProviderName() },
      ],
      removed: [],
    });
    this.stopped.delete(stringifyEntityRef(entity));
  }

  async deregisterRepository(entity: Entity): Promise<void> {
    const connection = this.validate(entity);
    const ref = stringifyEntityRef(entity);
    const wasStopped = this.stopped.has(ref);
    this.stopped.add(ref);
    try {
      await connection.applyMutation({
        type: "delta",
        added: [],
        removed: [{ entity, locationKey: this.getProviderName() }],
      });
    } catch (error) {
      if (!wasStopped) this.stopped.delete(ref);
      throw error;
    }
  }
}
