import { startTestBackend } from "@backstage/backend-test-utils";
import { createBackendModule } from "@backstage/backend-plugin-api";
import catalogPlugin from "@backstage/plugin-catalog-backend";
import { catalogProcessingExtensionPoint } from "@backstage/plugin-catalog-node";
import type { EntityProviderConnection } from "@backstage/plugin-catalog-node";
import type { Entity } from "@backstage/catalog-model";
import request from "supertest";
import { ManualGitRepositoryProvider } from "./ManualGitRepositoryProvider";

describe("manual repository catalog processing", () => {
  it("repairs an unprocessed registration and publishes provider-owned provenance", async () => {
    const provider = new ManualGitRepositoryProvider();
    let connection: EntityProviderConnection | undefined;
    const module = createBackendModule({
      pluginId: "catalog",
      moduleId: "manual-repository-test",
      register(reg) {
        reg.registerInit({
          deps: { processing: catalogProcessingExtensionPoint },
          async init({ processing }) {
            processing.addEntityProvider({
              getProviderName: () => provider.getProviderName(),
              async connect(value) {
                connection = value;
                await provider.connect(value);
              },
            });
          },
        });
      },
    });
    const backend = await startTestBackend({
      features: [catalogPlugin, module],
    });
    const entity: Entity = {
      apiVersion: "backstage.io/v1alpha1",
      kind: "Component",
      metadata: {
        name: "manual-repo",
        namespace: "default",
        annotations: {
          "ansible.io/registration-method": "manual",
          "backstage.io/source-location": "url:https://github.com/acme/repo",
          "backstage.io/view-url": "https://github.com/acme/repo",
        },
      },
      spec: {
        type: "git-repository",
        lifecycle: "production",
        owner: "user:default/alice",
      },
    };
    try {
      // Simulate the old persisted provider submission, then update that same
      // provider entry. No full mutation, deletion, or alternate bucket is used.
      expect(connection).toBeDefined();
      await connection!.applyMutation({
        type: "delta",
        added: [{ entity, locationKey: provider.getProviderName() }],
        removed: [],
      });
      await provider.registerRepository(entity);
      let published: Entity | undefined;
      const deadline = Date.now() + 10_000;
      while (Date.now() < deadline) {
        const response = await request(backend.server).get(
          "/api/catalog/entities/by-name/component/default/manual-repo",
        );
        if (response.status === 200) {
          published = response.body;
          break;
        }
        expect(response.status).toBe(404);
        await new Promise((resolve) => setTimeout(resolve, 100));
      }
      expect(published).toBeDefined();
      expect(published?.metadata.annotations).toMatchObject({
        "backstage.io/managed-by-location":
          "apme-manual:ManualGitRepositoryProvider",
        "backstage.io/managed-by-origin-location":
          "apme-manual:ManualGitRepositoryProvider",
        "backstage.io/source-location": "url:https://github.com/acme/repo",
        "backstage.io/view-url": "https://github.com/acme/repo",
      });
    } finally {
      await backend.stop();
    }
  }, 20_000);
});
