import type { CatalogClient } from "@backstage/catalog-client";
import { stringifyEntityRef } from "@backstage/catalog-model";
import type {
  AuthService,
  BackstageCredentials,
  PermissionsService,
  UserInfoService,
} from "@backstage/backend-plugin-api";
import type { Config } from "@backstage/config";
import { NotAllowedError } from "@backstage/errors";
import { AuthorizeResult } from "@backstage/plugin-permission-common";
import { catalogEntityReadPermission } from "@backstage/plugin-catalog-common/alpha";
import {
  resolvePortalContext,
  type ActiveOrganizationResolver,
  type PortalEntitlements,
} from "@ansible/portal-plugin-node";
import {
  normalizeRepoUrlFromEntity,
  defaultBranchFromEntity,
  type IApmeService,
} from "../common/index";
import {
  apmeQualityScanPermission,
  apmeScanningEntitlement,
} from "../common/operations";
import type { ManualGitRepositoryProvider } from "./providers/ManualGitRepositoryProvider";
import { normalizeRepoUrl } from "@ansible/backstage-rhaap-common/catalogEntity";

export type ProjectOperationAuthorizer = (
  projectId: string,
  credentials: BackstageCredentials,
  mutating: boolean,
) => Promise<void>;

/** Authorize before forwarding workflow traffic or resolving SCM credentials. */
export function createProjectOperationAuthorizer(options: {
  userInfo: UserInfoService;
  organizations: ActiveOrganizationResolver;
  entitlements: Pick<PortalEntitlements, "check">;
  permissions: PermissionsService;
  auth: AuthService;
  catalogClient: CatalogClient;
  apmeService: IApmeService;
  rootConfig: Config;
  manualProvider: ManualGitRepositoryProvider;
}): ProjectOperationAuthorizer {
  return async (projectId, credentials, mutating) => {
    const context = await resolvePortalContext({ ...options, credentials });
    const entitlement = await options.entitlements.check(
      apmeScanningEntitlement.id,
      credentials,
      options.permissions,
    );
    if (
      !entitlement.allowed ||
      ![
        ...(entitlement.organizationIds ?? []),
        ...(entitlement.organizationId ? [entitlement.organizationId] : []),
      ].some((id) =>
        (context.organizationIds ?? [context.organizationId]).includes(id),
      )
    ) {
      throw new NotAllowedError("APME scanning entitlement denied");
    }
    if (mutating) {
      const [decision] = await options.permissions.authorize(
        [{ permission: apmeQualityScanPermission }],
        { credentials },
      );
      if (decision?.result !== AuthorizeResult.ALLOW) {
        throw new NotAllowedError("APME scan permission denied");
      }
    }
    const project = await options.apmeService.getProject(projectId);
    const projectUrl = normalizeRepoUrl(project.repo_url);
    if (!projectUrl || !project.branch)
      throw new NotAllowedError("Project has no authorized repository subject");
    const variants = [projectUrl, `${projectUrl}.git`, `${projectUrl}/`];
    const { token } = await options.auth.getPluginRequestToken({
      onBehalfOf: credentials,
      targetPluginId: "catalog",
    });
    const { items } = await options.catalogClient.getEntities(
      {
        filter: [
          {
            kind: "Component",
            "spec.type": "git-repository",
            "metadata.annotations.backstage.io/view-url": variants,
          },
          {
            kind: "Component",
            "spec.type": "git-repository",
            "metadata.annotations.backstage.io/source-location": variants.map(
              (url) => `url:${url}`,
            ),
          },
        ],
      },
      { token },
    );
    for (const entity of items) {
      if (
        entity.kind.toLowerCase() !== "component" ||
        entity.spec?.type !== "git-repository" ||
        normalizeRepoUrlFromEntity(entity) !== projectUrl ||
        defaultBranchFromEntity(entity) !== project.branch ||
        options.manualProvider.isTrackingStopped(entity)
      )
        continue;
      const [decision] = await options.permissions.authorize(
        [
          {
            permission: catalogEntityReadPermission,
            resourceRef: stringifyEntityRef(entity),
          },
        ],
        { credentials },
      );
      if (decision?.result === AuthorizeResult.ALLOW) return;
    }
    throw new NotAllowedError(
      "Project repository is not accessible to the current user",
    );
  };
}
