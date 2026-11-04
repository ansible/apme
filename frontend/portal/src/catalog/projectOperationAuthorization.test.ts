import { ConfigReader } from "@backstage/config";
import { mockServices } from "@backstage/backend-test-utils";
import type { CatalogClient } from "@backstage/catalog-client";
import { AuthorizeResult } from "@backstage/plugin-permission-common";
import type { IApmeService } from "../common/index";
import {
  createProjectOperationAuthorizer,
  createRepositoryOperationAuthorizer,
} from "./projectOperationAuthorization";
import { ManualGitRepositoryProvider } from "./providers/ManualGitRepositoryProvider";

describe("interactive APME project authorization", () => {
  const getProject = jest.fn();
  const getEntities = jest.fn();
  const userInfo = mockServices.userInfo.mock();
  const organizations = { resolve: jest.fn() };
  const entitlements = { check: jest.fn() };
  const permissions = mockServices.permissions.mock();
  const credentials = {
    $$type: "@backstage/BackstageCredentials" as const,
    principal: { type: "user", userEntityRef: "user:default/alice" },
  };
  const entity = {
    apiVersion: "backstage.io/v1alpha1",
    kind: "Component",
    metadata: {
      name: "repo",
      namespace: "engineering",
      annotations: {
        "backstage.io/source-location": "url:https://github.com/acme/repo",
      },
    },
    spec: { type: "git-repository", repository_default_branch: "main" },
  };
  function authorize(
    config = new ConfigReader({}),
    manualProvider = new ManualGitRepositoryProvider(),
  ) {
    return createProjectOperationAuthorizer({
      userInfo,
      organizations,
      entitlements,
      permissions,
      auth: mockServices.auth(),
      catalogClient: { getEntities } as unknown as CatalogClient,
      apmeService: { getProject } as unknown as IApmeService,
      rootConfig: config,
      manualProvider,
    });
  }
  function authorizeRepository(
    config = new ConfigReader({}),
    manualProvider = new ManualGitRepositoryProvider(),
  ) {
    return createRepositoryOperationAuthorizer({
      userInfo,
      organizations,
      entitlements,
      permissions,
      auth: mockServices.auth(),
      catalogClient: { getEntities } as unknown as CatalogClient,
      apmeService: { getProject } as unknown as IApmeService,
      rootConfig: config,
      manualProvider,
    });
  }
  beforeEach(() => {
    jest.resetAllMocks();
    organizations.resolve.mockResolvedValue("engineering");
    userInfo.getUserInfo.mockResolvedValue({
      userEntityRef: "user:default/alice",
      ownershipEntityRefs: [],
    });
    entitlements.check.mockResolvedValue({
      allowed: true,
      organizationId: "engineering",
    });
    permissions.authorize.mockResolvedValue([
      { result: AuthorizeResult.ALLOW },
    ]);
    getProject.mockResolvedValue({
      id: "project-1",
      repo_url: "https://github.com/acme/repo",
      branch: "main",
    });
    getEntities.mockResolvedValue({ items: [entity] });
  });
  it("requires entitlement, mutation permission and catalog resource access", async () => {
    await expect(
      authorize()("project-1", credentials, true),
    ).resolves.toBeUndefined();
    expect(entitlements.check).toHaveBeenCalledWith(
      "apme.scanning",
      credentials,
      permissions,
    );
    expect(permissions.authorize).toHaveBeenCalledWith(
      [
        {
          permission: expect.objectContaining({
            name: "ansible.apme.quality.scan",
          }),
        },
      ],
      { credentials },
    );
    expect(permissions.authorize).toHaveBeenCalledWith(
      [
        {
          permission: expect.objectContaining({ name: "catalog.entity.read" }),
          resourceRef: "component:engineering/repo",
        },
      ],
      { credentials },
    );
  });
  it("does not require scan-create permission for reading operation state/events", async () => {
    await authorize()("project-1", credentials, false);
    expect(permissions.authorize).toHaveBeenCalledTimes(1);
    expect(permissions.authorize.mock.calls[0][0][0].permission.name).toBe(
      "catalog.entity.read",
    );
  });
  it("authorizes non-default branches at the repository level", async () => {
    getProject.mockResolvedValueOnce({
      id: "project-1",
      repo_url: "https://github.com/acme/repo",
      branch: "release/1.x",
    });

    await expect(
      authorize()("project-1", credentials, false),
    ).resolves.toBeUndefined();
    expect(permissions.authorize).toHaveBeenCalledWith(
      [
        {
          permission: expect.objectContaining({ name: "catalog.entity.read" }),
          resourceRef: "component:engineering/repo",
        },
      ],
      { credentials },
    );
  });
  it.each([{ allowed: false }, { allowed: true, organizationId: "other" }])(
    "denies entitlement %j before contacting the gateway",
    async (decision) => {
      entitlements.check.mockResolvedValue(decision);
      await expect(authorize()("project-1", credentials, true)).rejects.toThrow(
        "entitlement denied",
      );
      expect(getProject).not.toHaveBeenCalled();
      expect(getEntities).not.toHaveBeenCalled();
    },
  );
  it("denies scan-create permission before looking up the project", async () => {
    permissions.authorize.mockResolvedValue([{ result: AuthorizeResult.DENY }]);
    await expect(authorize()("project-1", credentials, true)).rejects.toThrow(
      "scan permission denied",
    );
    expect(getProject).not.toHaveBeenCalled();
  });
  it("denies missing or ambiguous organization", async () => {
    organizations.resolve.mockResolvedValue(undefined);
    await expect(authorize()("project-1", credentials, true)).rejects.toThrow(
      "missing or ambiguous",
    );
    expect(entitlements.check).not.toHaveBeenCalled();
  });
  it("denies catalog resource permission even when scanning is otherwise allowed", async () => {
    permissions.authorize
      .mockResolvedValueOnce([{ result: AuthorizeResult.ALLOW }])
      .mockResolvedValueOnce([{ result: AuthorizeResult.DENY }]);
    await expect(authorize()("project-1", credentials, true)).rejects.toThrow(
      "not accessible",
    );
  });
  it.each([
    {
      ...entity,
      metadata: {
        ...entity.metadata,
        annotations: {
          "backstage.io/source-location": "url:https://github.com/other/repo",
        },
      },
    },
    { ...entity, spec: { type: "collection" } },
  ])(
    "rejects catalog subjects that do not authorize this project",
    async (candidate) => {
      getEntities.mockResolvedValue({ items: [candidate] });
      await expect(authorize()("project-1", credentials, true)).rejects.toThrow(
        "not accessible",
      );
    },
  );
  it("permits shared default-namespace repositories without an AAP owner or mapping", async () => {
    getEntities.mockResolvedValue({
      items: [
        {
          ...entity,
          metadata: {
            ...entity.metadata,
            namespace: "default",
            annotations: {
              ...entity.metadata.annotations,
              "ansible.io/portal-organization": "legacy-other",
            },
          },
        },
      ],
    });
    await expect(
      authorize()("project-1", credentials, true),
    ).resolves.toBeUndefined();
    expect(getEntities.mock.calls[0][0].filter[0]).not.toHaveProperty(
      "metadata.namespace",
    );
  });
  it("accepts any granted membership for a shared repository while preserving resource checks", async () => {
    organizations.resolve.mockResolvedValue(undefined);
    const resolveAll = jest.fn().mockResolvedValue(["engineering", "finance"]);
    const check = jest
      .fn()
      .mockResolvedValue({ allowed: true, organizationId: "finance" });
    const authorizeMulti = createProjectOperationAuthorizer({
      userInfo,
      organizations: { ...organizations, resolveAll },
      entitlements: { check },
      permissions,
      auth: mockServices.auth(),
      catalogClient: { getEntities } as unknown as CatalogClient,
      apmeService: { getProject } as unknown as IApmeService,
      rootConfig: new ConfigReader({}),
      manualProvider: new ManualGitRepositoryProvider(),
    });
    await expect(
      authorizeMulti("project-1", credentials, true),
    ).resolves.toBeUndefined();
    expect(permissions.authorize).toHaveBeenCalledTimes(2);
  });
  it("does not authorize a catalog subject after manual tracking was stopped", async () => {
    const manual = {
      ...entity,
      metadata: {
        ...entity.metadata,
        annotations: {
          ...entity.metadata.annotations,
          "ansible.io/registration-method": "manual",
        },
      },
    };
    const provider = new ManualGitRepositoryProvider();
    await provider.connect({ applyMutation: jest.fn(), refresh: jest.fn() });
    await provider.deregisterRepository(manual);
    getEntities.mockResolvedValue({ items: [manual] });
    await expect(
      authorize(new ConfigReader({}), provider)("project-1", credentials, true),
    ).rejects.toThrow("not accessible");
  });
  it("denies projects with no matching catalog subject", async () => {
    getEntities.mockResolvedValue({ items: [] });
    await expect(authorize()("project-1", credentials, true)).rejects.toThrow(
      "not accessible",
    );
  });

  it("requires registration permission and catalog access for a new project", async () => {
    await expect(
      authorizeRepository()(
        "https://github.com/acme/repo",
        "main",
        credentials,
        "register",
      ),
    ).resolves.toBeUndefined();
    expect(permissions.authorize).toHaveBeenNthCalledWith(
      1,
      [
        {
          permission: expect.objectContaining({
            name: "ansible.apme.repository.register",
          }),
        },
      ],
      { credentials },
    );
    expect(permissions.authorize).toHaveBeenNthCalledWith(
      2,
      [
        {
          permission: expect.objectContaining({ name: "catalog.entity.read" }),
          resourceRef: "component:engineering/repo",
        },
      ],
      { credentials },
    );
  });

  it("requires catalog access before exposing branch metadata", async () => {
    permissions.authorize.mockResolvedValueOnce([
      { result: AuthorizeResult.DENY },
    ]);
    await expect(
      authorizeRepository()(
        "https://github.com/acme/repo",
        "feature",
        credentials,
        "read",
      ),
    ).rejects.toThrow("not accessible");
    expect(getEntities).toHaveBeenCalled();
    expect(permissions.authorize).toHaveBeenCalledTimes(1);
    expect(permissions.authorize.mock.calls[0][0][0].permission.name).toBe(
      "catalog.entity.read",
    );
  });

  it("denies project registration before catalog or SCM access when permission is absent", async () => {
    permissions.authorize.mockResolvedValueOnce([
      { result: AuthorizeResult.DENY },
    ]);
    await expect(
      authorizeRepository()(
        "https://github.com/acme/repo",
        "main",
        credentials,
        "register",
      ),
    ).rejects.toThrow("registration permission denied");
    expect(getEntities).not.toHaveBeenCalled();
  });
});
