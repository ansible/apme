import type { Config } from "@backstage/config";
import type { AuthService, LoggerService } from "@backstage/backend-plugin-api";
import type { CatalogClient } from "@backstage/catalog-client";
import { stringifyEntityRef, type Entity } from "@backstage/catalog-model";
import { ConflictError, InputError, NotAllowedError } from "@backstage/errors";
import { ScmClientFactory } from "@ansible/backstage-rhaap-common";
import {
  apmeRepositoryRegisterOperation,
  type RegisterRepositoryInput,
} from "../../common/operations";
import type { PortalOperations } from "@ansible/portal-plugin-node";
import type { ManualGitRepositoryProvider } from "../providers/ManualGitRepositoryProvider";

export function registerRepositoryOperation(options: {
  operations: PortalOperations;
  rootConfig: Config;
  logger: LoggerService;
  auth: AuthService;
  catalogClient: CatalogClient;
  provider: ManualGitRepositoryProvider;
}): void {
  options.operations.register({
    pluginId: "apme",
    descriptor: apmeRepositoryRegisterOperation,
    async handler({ subject, input }, context) {
      // Creation has no entity yet: the current subject is the authenticated user.
      if (subject.entityRef !== context.userEntityRef) {
        throw new NotAllowedError(
          "Registration subject must be the current user",
        );
      }
      const values = input as unknown as RegisterRepositoryInput;
      const {
        sourceControlProvider: scm,
        repositoryOwner: owner,
        repositoryName: name,
      } = values;
      let url: URL;
      try {
        url = new URL(values.repositoryUrl);
      } catch {
        throw new InputError("Invalid repository URL");
      }
      const configuredHost = options.rootConfig
        .getOptionalConfigArray(`integrations.${scm}`)
        ?.some((integration) => integration.getString("host") === url.hostname);
      if (
        url.protocol !== "https:" ||
        !configuredHost ||
        url.port ||
        url.username ||
        url.password ||
        url.search ||
        url.hash ||
        url.pathname.replace(/\/$/, "").replace(/\.git$/i, "") !==
          `/${owner}/${name}`
      ) {
        throw new InputError("Repository URL does not match SCM metadata");
      }
      // Validate access on every transport, not just when invoked by Scaffolder.
      const client = await new ScmClientFactory({
        rootConfig: options.rootConfig,
        logger: options.logger,
      }).createClient({
        scmProvider: scm,
        host: url.hostname,
        organization: owner,
        repository: name,
        token: values.token,
      });
      if (!(await client.repositoryExists(owner, name))) {
        throw new InputError("Repository does not exist or is not accessible");
      }
      const entityName = `${owner}-${name}-${scm}-manual`
        .toLowerCase()
        .replaceAll(/[^a-z0-9-]/g, "-")
        .replaceAll(/-+/g, "-")
        .replaceAll(/(^-)|(-$)/g, "")
        .substring(0, 63);
      if (!entityName) throw new InputError("Invalid repository name");
      const entity: Entity = {
        apiVersion: "backstage.io/v1alpha1",
        kind: "Component",
        metadata: {
          name: entityName,
          namespace: "default",
          title: name,
          description: values.description || `Git repository: ${owner}/${name}`,
          tags: ["git-repository", scm, "manually-registered"],
          annotations: {
            "backstage.io/source-location": `url:${url.href}`,
            "backstage.io/view-url": url.href,
            "ansible.io/scm-provider": scm,
            "ansible.io/scm-organization": owner,
            "ansible.io/scm-repository": name,
            "ansible.io/registration-method": "manual",
          },
        },
        spec: {
          type: "git-repository",
          lifecycle: "production",
          owner: context.userEntityRef,
          repository_name: name,
          repository_default_branch: values.defaultBranch,
          repository_collection_count: 0,
          repository_ee_count: 0,
        },
      };
      const { token } = await options.auth.getPluginRequestToken({
        onBehalfOf: context.credentials,
        targetPluginId: "catalog",
      });
      const existing = await options.catalogClient.getEntities(
        {
          filter: {
            kind: "Component",
            "metadata.annotations.ansible.io/scm-provider": scm,
            "metadata.annotations.ansible.io/scm-organization": owner,
            "metadata.annotations.ansible.io/scm-repository": name,
          },
        },
        { token },
      );
      const entityRef = stringifyEntityRef(entity);
      const sameName = await options.catalogClient.getEntityByRef(entityRef, {
        token,
      });
      if (existing.items.length || sameName)
        throw new ConflictError("Repository is already registered");
      await options.provider.registerRepository(entity);
      return { entityRef, entityName };
    },
  });
}
