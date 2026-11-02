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
  coreServices,
  createBackendModule,
} from "@backstage/backend-plugin-api";
import {
  createPortalPlugin,
  portalOperationsServiceRef,
  portalEntitlementsServiceRef,
  portalHealthServiceRef,
  portalActiveOrganizationServiceRef,
} from "@ansible/portal-plugin-node";
import { registerApmeOperations } from "./operations";
import { registerSettingsOperations } from "./settingsOperations";
import { createProjectOperationAuthorizer } from "./projectOperationAuthorization";
import { ManualGitRepositoryProvider } from "./providers/ManualGitRepositoryProvider";
import { ApmeRegistrationTemplateProvider } from "./providers/ApmeRegistrationTemplateProvider";
import { createManualRepositoriesRouter } from "./manualRepositories/router";
import { registerRepositoryOperation } from "./manualRepositories/registerOperation";
import {
  apmeRepositoryRegisterPermission,
  apmeRepositoryDeregisterPermission,
  apmeScanningEntitlement,
  apmeScanningPermission,
} from "../common/operations";
import { CatalogClient } from "@backstage/catalog-client";
import {
  apmeServiceRef,
  isApmeEnabled,
  getApmeConfig,
  resolveScanTargetVersion,
  ApmeClient,
  apmeQualityScanPermission,
} from "../common/index";
import { catalogProcessingExtensionPoint } from "@backstage/plugin-catalog-node";

import {
  ANSIBLE_SETTINGS_CAPABILITIES,
  ansibleSettingsEditPermission,
  ansibleSettingsViewPermission,
  type AnsibleSettingsCapability,
} from "@ansible/backstage-rhaap-common/permissions";
import {
  ansibleSettingsResourceRef,
  settingsPermissionRules,
} from "@ansible/backstage-rhaap-common/permissionRules";
import { createRouter } from "./router";
import { registerApmeCatalogSyncTasks } from "./apmeCatalogSyncScheduler";
import { ApmePortalSettingsStore } from "./apmePortalSettingsStore";
import { registerPortalGalaxyServersSync } from "./portalGalaxyServersSync";
import { ApmeLearnedDepsEntityProvider } from "./providers/ApmeLearnedDepsEntityProvider";

function isAnsibleSettingsCapability(
  ref: string,
): ref is AnsibleSettingsCapability {
  return (ANSIBLE_SETTINGS_CAPABILITIES as readonly string[]).includes(ref);
}

export const catalogModuleApme = createBackendModule({
  pluginId: "catalog",
  moduleId: "apme",
  register(reg) {
    reg.registerInit({
      deps: {
        logger: coreServices.logger,
        rootConfig: coreServices.rootConfig,
        apmeService: apmeServiceRef,
        httpRouter: coreServices.httpRouter,
        httpAuth: coreServices.httpAuth,
        userInfo: coreServices.userInfo,
        scheduler: coreServices.scheduler,
        discovery: coreServices.discovery,
        auth: coreServices.auth,
        catalogProcessing: catalogProcessingExtensionPoint,
        permissionsRegistry: coreServices.permissionsRegistry,
        permissions: coreServices.permissions,
        operations: portalOperationsServiceRef,
        entitlements: portalEntitlementsServiceRef,
        health: portalHealthServiceRef,
        organizations: portalActiveOrganizationServiceRef,
      },
      async init({
        logger,
        rootConfig,
        apmeService,
        httpRouter,
        httpAuth,
        userInfo,
        scheduler,
        discovery,
        auth,
        catalogProcessing,
        permissionsRegistry,
        permissions,
        operations,
        entitlements,
        health,
        organizations,
      }) {
        const apmePortalPlugin = createPortalPlugin({
          pluginId: "apme",
          health,
        });

        if (!isApmeEnabled(rootConfig)) {
          logger.info("APME is disabled; skipping catalog module registration");
          apmePortalPlugin.pushHealthStatus({
            state: "UNKNOWN",
            message: "APME is disabled in config (apme.enabled: false).",
          });
          return;
        }

        permissionsRegistry.addResourceType({
          resourceRef: ansibleSettingsResourceRef,
          permissions: [
            ansibleSettingsEditPermission,
            ansibleSettingsViewPermission,
          ],
          rules: settingsPermissionRules,
          getResources: async (resourceRefs) => {
            await Promise.resolve();
            return resourceRefs.map((ref) =>
              isAnsibleSettingsCapability(ref)
                ? { capability: ref }
                : undefined,
            );
          },
        });

        logger.info("Initializing APME catalog module");
        entitlements.register("apme", [apmeScanningEntitlement]);
        permissionsRegistry.addPermissions([apmeScanningPermission]);
        permissionsRegistry.addPermissions([apmeQualityScanPermission]);
        permissionsRegistry.addPermissions([
          apmeRepositoryRegisterPermission,
          apmeRepositoryDeregisterPermission,
        ]);

        const configSnapshot = getApmeConfig(rootConfig);
        const portalSettingsStore = new ApmePortalSettingsStore(
          configSnapshot.portalSettingsPath,
        );
        registerSettingsOperations({
          operations,
          rootConfig,
          store: portalSettingsStore,
        });
        logger.info(
          `APME portal settings store at ${portalSettingsStore.path}`,
        );

        const maybeClient = apmeService as ApmeClient;
        if (typeof maybeClient.setResolveBaseUrl === "function") {
          maybeClient.setResolveBaseUrl(async () => {
            const store = await portalSettingsStore.read();
            return store.global?.gatewayBaseUrl;
          });
        }

        const resolveScanVersion = async (projectId: string) => {
          const store = await portalSettingsStore.read();
          return resolveScanTargetVersion({
            projectId,
            store,
            configTargetAnsibleCoreVersion:
              configSnapshot.targetAnsibleCoreVersion,
          });
        };

        /** Portal Quality settings AI gate; false when unset or invalid. */
        const resolveEnableAi = async () => {
          const store = await portalSettingsStore.read();
          return store.global?.enableAi ?? false;
        };

        const catalogClient = new CatalogClient({ discoveryApi: discovery });
        const manualProvider = new ManualGitRepositoryProvider();
        const router = await createRouter({
          apmeService,
          logger,
          httpAuth,
          rootConfig,
          portalSettingsStore,
          permissions,
          authorizeProjectOperation: createProjectOperationAuthorizer({
            userInfo,
            organizations,
            entitlements,
            permissions,
            auth,
            catalogClient,
            apmeService,
            rootConfig,
            manualProvider,
          }),
        });

        // Mounted on the catalog plugin stack — never apply global body parsers
        // in createRouter (see jsonBody.ts / router regression test).
        httpRouter.use(router);
        logger.info("APME routes registered at /api/catalog/apme/*");

        apmePortalPlugin.pushHealthStatus({
          state: "READY",
          message: "APME initialized. Routes registered, schedulers starting.",
        });

        catalogProcessing.addEntityProvider(manualProvider);
        catalogProcessing.addEntityProvider(
          new ApmeRegistrationTemplateProvider(),
        );
        registerRepositoryOperation({
          operations,
          rootConfig,
          logger,
          auth,
          catalogClient,
          provider: manualProvider,
        });
        httpRouter.use(
          createManualRepositoriesRouter({
            httpAuth,
            userInfo,
            permissions,
            operations,
            organizations,
          }),
        );
        registerApmeOperations({
          rootConfig,
          manualProvider,
          operations,
          catalogClient,
          auth,
          permissions,
          apmeService,
          resolveScanVersion,
          resolveEnableAi,
        });
        registerApmeCatalogSyncTasks({
          shouldTrackEntity: (entity) =>
            !manualProvider.isTrackingStopped(entity),
          scheduler,
          catalogClient,
          auth,
          apmeService,
          rootConfig,
          logger,
          resolveScanVersion,
          resolveEnableAi,
          onSyncError: (env, err) => {
            apmePortalPlugin.pushHealthStatus({
              state: "DEGRADED",
              message: `Catalog sync failed for env=${env}: ${err.message}`,
            });
          },
          onSyncRecovered: (env) => {
            apmePortalPlugin.pushHealthStatus({
              state: "READY",
              message: `Catalog sync recovered for env=${env}.`,
            });
          },
        });

        await registerPortalGalaxyServersSync({
          scheduler,
          apmeService,
          rootConfig,
          logger,
        });

        const learnedDepsProvider = new ApmeLearnedDepsEntityProvider({
          shouldTrackEntity: (entity) =>
            !manualProvider.isTrackingStopped(entity),
          apmeService,
          catalogClient,
          auth,
          logger,
          rootConfig,
        });
        catalogProcessing.addEntityProvider(learnedDepsProvider);
        await learnedDepsProvider.schedule(scheduler);
      },
    });
  },
});
