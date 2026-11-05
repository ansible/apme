import { createTemplateAction } from "@backstage/plugin-scaffolder-node";
import type {
  AuthService,
  PermissionsService,
  UserInfoService,
} from "@backstage/backend-plugin-api";
import { NotAllowedError } from "@backstage/errors";
import {
  resolvePortalContext,
  type ActiveOrganizationResolver,
  type PortalOperations,
} from "@ansible/portal-plugin-node";
import { apmeRepositoryRegisterOperation } from "../../common/operations";

export function registerGitRepositoryAction(options: {
  auth: AuthService;
  userInfo: UserInfoService;
  permissions: PermissionsService;
  operations: PortalOperations;
  organizations: ActiveOrganizationResolver;
}) {
  return createTemplateAction({
    id: "ansible:register:git-repository",
    description:
      "Registers an existing repository through the authorized APME operation",
    schema: {
      input: {
        sourceControlProvider: (z) =>
          z.string({ description: "SCM provider (e.g. github, gitlab)" }),
        repositoryOwner: (z) => z.string(),
        repositoryName: (z) => z.string(),
        repositoryUrl: (z) =>
          z.string({ description: "Fully-qualified URL to the repository" }),
        defaultBranch: (z) => z.string(),
        owner: (z) =>
          z
            .string()
            .optional()
            .describe(
              "Legacy input retained for compatibility. Ownership is derived from the initiating user.",
            ),
        description: (z) => z.string().optional(),
        token: (z) =>
          z
            .string()
            .optional()
            .describe(
              "Optional OAuth token for SCM authentication. If not provided, the integration token from app-config will be used.",
            ),
      },
      output: {
        entityRef: (z) => z.string().optional(),
        entityName: (z) => z.string().optional(),
      },
    },
    async handler(ctx) {
      const credentials = await ctx.getInitiatorCredentials();
      if (!options.auth.isPrincipal(credentials, "user")) {
        throw new NotAllowedError("Repository registration requires a user");
      }
      const context = await resolvePortalContext({ ...options, credentials });
      // Legacy owner input is accepted for template compatibility, never as authority.
      const { owner: _owner, ...input } = ctx.input;
      const result = (await options.operations.execute(
        apmeRepositoryRegisterOperation.id,
        {
          subject: { entityRef: context.userEntityRef },
          input: {
            ...input,
            sourceControlProvider: input.sourceControlProvider.toLowerCase(),
          },
        },
        {
          credentials,
          ...context,
        },
        options.permissions,
      )) as { entityRef: string; entityName: string };
      ctx.output("entityRef", result.entityRef);
      ctx.output("entityName", result.entityName);
      ctx.logger.info(
        "[ansible:register:git-repository] Repository registered",
      );
    },
  });
}
