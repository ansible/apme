/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
import type { Entity } from "@backstage/catalog-model";
import type {
  EntityProvider,
  EntityProviderConnection,
} from "@backstage/plugin-catalog-node";
import registrationTemplate from "apme:registration-template";

/** Install the registration template with the optional guest, not host config. */
export class ApmeRegistrationTemplateProvider implements EntityProvider {
  getProviderName() {
    return "apme-registration-template";
  }
  async connect(connection: EntityProviderConnection): Promise<void> {
    const entity = registrationTemplate as Entity;
    await connection.applyMutation({
      type: "full",
      entities: [
        {
          entity: {
            ...entity,
            metadata: {
              ...entity.metadata,
              annotations: {
                ...entity.metadata.annotations,
                "backstage.io/managed-by-location":
                  "url:https://github.com/ansible/apme",
                "backstage.io/managed-by-origin-location":
                  "url:https://github.com/ansible/apme",
              },
            },
          },
          locationKey: this.getProviderName(),
        },
      ],
    });
  }
}
