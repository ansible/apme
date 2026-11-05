import { mockServices } from "@backstage/backend-test-utils";
import type { CatalogClient } from "@backstage/catalog-client";
import type { EntityProviderConnection } from "@backstage/plugin-catalog-node";
import { ConfigReader } from "@backstage/config";
import { AuthorizeResult } from "@backstage/plugin-permission-common";
import { PortalOperations } from "@ansible/portal-plugin-node";
import { ScmClientFactory } from "@ansible/backstage-rhaap-common";
import { registerRepositoryOperation } from "./registerOperation";
import { ManualGitRepositoryProvider } from "../providers/ManualGitRepositoryProvider";

jest.mock("@ansible/backstage-rhaap-common", () => ({
  ScmClientFactory: jest.fn(),
}));

describe("repository registration operation", () => {
  const applyMutation = jest.fn();
  const repositoryExists = jest.fn();
  const getEntityByRef = jest.fn();
  const getEntities = jest.fn();
  const permissions = mockServices.permissions.mock();
  const logger = mockServices.logger.mock();
  const input = {
    sourceControlProvider: "github",
    repositoryOwner: "test-rhaap-portal-3",
    repositoryName: "test-amazon-aws",
    repositoryUrl: "https://github.com/test-rhaap-portal-3/test-amazon-aws",
    defaultBranch: "main",
    token: "sensitive-scm-token",
  };
  const context = {
    credentials: {
      $$type: "@backstage/BackstageCredentials" as const,
      principal: { type: "user", userEntityRef: "user:acme/alice" },
    },
    userEntityRef: "user:acme/alice",
    userId: "alice",
    organizationId: "acme",
  };
  async function operations(
    catalogMappings?: Array<{ organizationId: string; namespaces: string[] }>,
  ) {
    const provider = new ManualGitRepositoryProvider();
    await provider.connect({
      applyMutation,
    } as unknown as EntityProviderConnection);
    const registry = new PortalOperations(
      logger,
      { resolve: async () => "acme" },
      { check: jest.fn() },
    );
    registerRepositoryOperation({
      operations: registry,
      provider,
      logger,
      auth: mockServices.auth(),
      rootConfig: new ConfigReader({
        integrations: { github: [{ host: "github.com" }] },
        ...(catalogMappings
          ? { portal: { organization: { catalogMappings } } }
          : {}),
      }),
      catalogClient: {
        getEntities,
        getEntityByRef,
      } as unknown as CatalogClient,
    });
    return registry;
  }
  async function execute(values = input, subject = context.userEntityRef) {
    return (await operations()).execute(
      "apme.repository.register",
      {
        subject: { entityRef: subject },
        input: values,
      },
      context,
      permissions,
    );
  }
  beforeEach(() => {
    jest.resetAllMocks();
    permissions.authorize.mockResolvedValue([
      { result: AuthorizeResult.ALLOW },
    ]);
    getEntities.mockResolvedValue({ items: [] });
    repositoryExists.mockResolvedValue(true);
    (ScmClientFactory as jest.Mock).mockImplementation(() => ({
      createClient: async () => ({ repositoryExists }),
    }));
  });
  it.each(["", ".git"])(
    "registers the reported repository with suffix %s",
    async (suffix) => {
      const result = await execute({
        ...input,
        repositoryUrl: input.repositoryUrl + suffix,
      });
      expect(result).toEqual({
        entityRef:
          "component:default/test-rhaap-portal-3-test-amazon-aws-github-manual",
        entityName: "test-rhaap-portal-3-test-amazon-aws-github-manual",
      });
      const entity = applyMutation.mock.calls[0][0].added[0].entity;
      expect(entity.spec.owner).toBe(context.userEntityRef);
      expect(entity.metadata.namespace).toBe("default");
      expect(entity.metadata.annotations).not.toHaveProperty(
        "ansible.io/portal-organization",
      );
      expect(
        entity.metadata.annotations["backstage.io/managed-by-location"],
      ).toBe("apme-manual:ManualGitRepositoryProvider");
      expect(
        entity.metadata.annotations["backstage.io/managed-by-origin-location"],
      ).toBe("apme-manual:ManualGitRepositoryProvider");
      expect(JSON.stringify(logger.info.mock.calls)).not.toContain(input.token);
    },
  );
  it("authorizes before SCM access or catalog mutation", async () => {
    permissions.authorize.mockResolvedValue([{ result: AuthorizeResult.DENY }]);
    await expect(execute()).rejects.toThrow("permission");
    expect(ScmClientFactory).not.toHaveBeenCalled();
    expect(applyMutation).not.toHaveBeenCalled();
  });
  it("does not let obsolete organization namespace mappings change repository identity", async () => {
    const registry = await operations([
      { organizationId: "acme", namespaces: ["private"] },
    ]);
    await registry.execute(
      "apme.repository.register",
      { subject: { entityRef: context.userEntityRef }, input },
      context,
      permissions,
    );
    const entity = applyMutation.mock.calls[0][0].added[0].entity;
    expect(entity.metadata.namespace).toBe("default");
    expect(entity.metadata.annotations).not.toHaveProperty(
      "ansible.io/portal-organization",
    );
    expect(getEntities.mock.calls[0][0].filter).not.toHaveProperty(
      "metadata.namespace",
    );
  });
  it("rejects a spoofed creation subject", async () => {
    await expect(execute(input, "user:other/person")).rejects.toThrow(
      "current user",
    );
    expect(repositoryExists).not.toHaveBeenCalled();
  });
  it("validates the schema before contacting SCM", async () => {
    await expect(execute({ ...input, repositoryOwner: "" })).rejects.toThrow(
      "schema",
    );
    expect(repositoryExists).not.toHaveBeenCalled();
  });
  it("rejects inaccessible repositories", async () => {
    repositoryExists.mockResolvedValue(false);
    await expect(execute()).rejects.toThrow("not accessible");
    expect(applyMutation).not.toHaveBeenCalled();
  });
  it("rejects duplicate content identity", async () => {
    getEntities.mockResolvedValue({ items: [{}] });
    await expect(execute()).rejects.toThrow("already registered");
    expect(applyMutation).not.toHaveBeenCalled();
  });
});
