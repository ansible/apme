import type { CatalogClient } from "@backstage/catalog-client";
import type { Config } from "@backstage/config";
import type {
  AuthService,
  PermissionsService,
} from "@backstage/backend-plugin-api";
import { catalogEntityReadPermission } from "@backstage/plugin-catalog-common/alpha";
import { AuthorizeResult } from "@backstage/plugin-permission-common";
import { InputError, NotAllowedError, NotFoundError } from "@backstage/errors";
import type { PortalOperations } from "@ansible/portal-plugin-node";
import type { ManualGitRepositoryProvider } from "./providers/ManualGitRepositoryProvider";
import { apmeRepositoryDeregisterOperation } from "../common/operations";
import {
  apmeQualityScanOperation,
  type IApmeService,
  normalizeRepoUrlFromEntity,
  defaultBranchFromEntity,
} from "../common/index";

export function registerApmeOperations(options: {
  operations: PortalOperations;
  rootConfig: Config;
  catalogClient: CatalogClient;
  auth: AuthService;
  permissions: PermissionsService;
  apmeService: IApmeService;
  resolveScanVersion(projectId: string): Promise<string | undefined>;
  resolveEnableAi(): Promise<boolean>;
  manualProvider?: ManualGitRepositoryProvider;
}) {
  options.operations.register({
    pluginId: "apme",
    descriptor: apmeQualityScanOperation,
    async handler({ subject }, context) {
      const [decision] = await options.permissions.authorize(
        [
          {
            permission: catalogEntityReadPermission,
            resourceRef: subject.entityRef,
          },
        ],
        { credentials: context.credentials },
      );
      if (decision?.result !== AuthorizeResult.ALLOW) {
        throw new NotAllowedError("Repository access denied");
      }
      const { token } = await options.auth.getPluginRequestToken({
        onBehalfOf: context.credentials,
        targetPluginId: "catalog",
      });
      const entity = await options.catalogClient.getEntityByRef(
        subject.entityRef,
        { token },
      );
      if (!entity) throw new NotFoundError("Repository not found");
      if (
        entity.kind.toLowerCase() !== "component" ||
        entity.spec?.type !== "git-repository"
      ) {
        throw new InputError("Quality scan requires a Git repository");
      }
      const repoUrl = normalizeRepoUrlFromEntity(entity);
      if (options.manualProvider?.isTrackingStopped(entity)) {
        throw new NotFoundError("Repository is no longer tracked by Portal");
      }
      if (!repoUrl) throw new InputError("Repository has no source URL");
      const project = await options.apmeService.getProjectByRepoUrl(
        repoUrl,
        defaultBranchFromEntity(entity),
      );
      if (!project)
        throw new NotFoundError("Repository has no APME project yet");
      const ansibleVersion = await options.resolveScanVersion(project.id);
      const enableAi = await options.resolveEnableAi();
      if (options.manualProvider?.isTrackingStopped(entity)) {
        throw new NotFoundError("Repository is no longer tracked by Portal");
      }
      return options.apmeService.triggerScan(project.id, {
        ansibleVersion,
        enableAi,
        userIdentity: { userEntityRef: context.userEntityRef },
      });
    },
  });
  if (options.manualProvider) {
    const provider = options.manualProvider;
    options.operations.register({
      pluginId: "apme",
      descriptor: apmeRepositoryDeregisterOperation,
      async handler({ subject }, context) {
        const [decision] = await options.permissions.authorize(
          [
            {
              permission: catalogEntityReadPermission,
              resourceRef: subject.entityRef,
            },
          ],
          { credentials: context.credentials },
        );
        if (decision?.result !== AuthorizeResult.ALLOW)
          throw new NotAllowedError("Repository access denied");
        const { token } = await options.auth.getPluginRequestToken({
          onBehalfOf: context.credentials,
          targetPluginId: "catalog",
        });
        const entity = await options.catalogClient.getEntityByRef(
          subject.entityRef,
          { token },
        );
        if (!entity) throw new NotFoundError("Repository not found");
        if (
          entity.kind.toLowerCase() !== "component" ||
          entity.spec?.type !== "git-repository" ||
          entity.metadata.annotations?.["ansible.io/registration-method"] !==
            "manual"
        ) {
          throw new InputError(
            "Only manually registered Git repositories can be deregistered",
          );
        }
        await provider.deregisterRepository(entity);
        // Deliberately no gateway project deletion or schedule mutation.
        return { entityRef: subject.entityRef, subjectRemoved: true };
      },
    });
  }
}
