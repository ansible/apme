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
  apmeRepositoryRegisterPermission,
  apmeScanningEntitlement,
} from "../common/operations";
import type { ManualGitRepositoryProvider } from "./providers/ManualGitRepositoryProvider";
import { normalizeRepoUrl } from "@ansible/backstage-rhaap-common/catalogEntity";

export type ProjectOperationAuthorizer = (
  projectId: string,
  credentials: BackstageCredentials,
  mutating: boolean,
) => Promise<void>;

export type RepositoryOperationAuthorizer = (
  repoUrl: string,
  branch: string,
  credentials: BackstageCredentials,
  operation: "read" | "register",
) => Promise<void>;

type AuthorizationOptions = {
  userInfo: UserInfoService;
  organizations: ActiveOrganizationResolver;
  entitlements: Pick<PortalEntitlements, "check">;
  permissions: PermissionsService;
  auth: AuthService;
  catalogClient: CatalogClient;
  apmeService: IApmeService;
  rootConfig: Config;
  manualProvider: ManualGitRepositoryProvider;
};

async function authorizePortalAccess(
  options: AuthorizationOptions,
  credentials: BackstageCredentials,
  operation?: "scan" | "register",
): Promise<void> {
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
  const permission =
    operation === "scan"
      ? apmeQualityScanPermission
      : operation === "register"
        ? apmeRepositoryRegisterPermission
        : undefined;
  if (permission) {
    const [decision] = await options.permissions.authorize(
      [{ permission }],
      { credentials },
    );
    if (decision?.result !== AuthorizeResult.ALLOW) {
      throw new NotAllowedError(
        operation === "scan"
          ? "APME scan permission denied"
          : "APME repository registration permission denied",
      );
    }
  }
}

async function authorizeCatalogRepository(
  options: AuthorizationOptions,
  credentials: BackstageCredentials,
  repoUrl: string,
  branch?: string,
): Promise<void> {
  const normalizedRepoUrl = normalizeRepoUrl(repoUrl);
  if (!normalizedRepoUrl) {
    throw new NotAllowedError("Repository has no authorized subject");
  }
  const variants = [
    normalizedRepoUrl,
    `${normalizedRepoUrl}.git`,
    `${normalizedRepoUrl}/`,
  ];
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
      normalizeRepoUrlFromEntity(entity) !== normalizedRepoUrl ||
      (branch && defaultBranchFromEntity(entity) !== branch) ||
      options.manualProvider.isTrackingStopped(entity)
    ) {
      continue;
    }
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
    "Repository is not accessible to the current user",
  );
}

/** Authorize before forwarding workflow traffic or resolving SCM credentials. */
export function createProjectOperationAuthorizer(
  options: AuthorizationOptions,
): ProjectOperationAuthorizer {
  return async (projectId, credentials, mutating) => {
    await authorizePortalAccess(
      options,
      credentials,
      mutating ? "scan" : undefined,
    );
    const project = await options.apmeService.getProject(projectId);
    if (!project.branch) {
      throw new NotAllowedError("Project has no authorized repository subject");
    }
    await authorizeCatalogRepository(
      options,
      credentials,
      project.repo_url,
      project.branch,
    );
  };
}

export function createRepositoryOperationAuthorizer(
  options: AuthorizationOptions,
): RepositoryOperationAuthorizer {
  return async (repoUrl, _branch, credentials, operation) => {
    await authorizePortalAccess(
      options,
      credentials,
      operation === "register" ? "register" : undefined,
    );
    await authorizeCatalogRepository(options, credentials, repoUrl);
  };
}
