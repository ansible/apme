import { mockServices } from "@backstage/backend-test-utils";
import type { PortalOperations } from "@ansible/portal-plugin-node";
import { registerGitRepositoryAction } from "./registerGitRepository";

describe("registerGitRepository action adapter", () => {
  const execute = jest.fn();
  const auth = mockServices.auth.mock();
  const userInfo = mockServices.userInfo.mock();
  const permissions = mockServices.permissions.mock();
  const organizations = { resolve: jest.fn() };
  const credentials = {
    $$type: "@backstage/BackstageCredentials",
    principal: { type: "user", userEntityRef: "user:acme/alice" },
  };
  const action = () =>
    registerGitRepositoryAction({
      auth,
      userInfo,
      permissions,
      organizations,
      operations: { execute } as unknown as PortalOperations,
    });
  const ctx = () =>
    ({
      input: {
        sourceControlProvider: "github",
        repositoryOwner: "test-rhaap-portal-3",
        repositoryName: "test-amazon-aws",
        repositoryUrl: "https://github.com/test-rhaap-portal-3/test-amazon-aws",
        defaultBranch: "main",
        owner: "user:spoofed/person",
        token: "secret",
      },
      logger: mockServices.logger.mock(),
      output: jest.fn(),
      getInitiatorCredentials: jest.fn().mockResolvedValue(credentials),
    }) as any;
  beforeEach(() => {
    jest.resetAllMocks();
    organizations.resolve.mockResolvedValue("acme");
    auth.isPrincipal.mockReturnValue(true);
    userInfo.getUserInfo.mockResolvedValue({
      userEntityRef: "user:acme/alice",
      ownershipEntityRefs: [],
    });
    execute.mockResolvedValue({
      entityRef: "component:acme/repo",
      entityName: "repo",
    });
  });
  it("uses the initiator and typed operation; never forwards client ownership", async () => {
    const context = ctx();
    await action().handler(context);
    expect(execute).toHaveBeenCalledWith(
      "apme.repository.register",
      {
        subject: { entityRef: "user:acme/alice" },
        input: {
          sourceControlProvider: "github",
          repositoryOwner: "test-rhaap-portal-3",
          repositoryName: "test-amazon-aws",
          repositoryUrl:
            "https://github.com/test-rhaap-portal-3/test-amazon-aws",
          defaultBranch: "main",
          token: "secret",
        },
      },
      {
        credentials,
        userEntityRef: "user:acme/alice",
        userId: "alice",
        organizationId: "acme",
      },
      permissions,
    );
    expect(context.output).toHaveBeenCalledWith(
      "entityRef",
      "component:acme/repo",
    );
    expect(context.output).toHaveBeenCalledWith("entityName", "repo");
    expect(auth.getOwnServiceCredentials).not.toHaveBeenCalled();
  });
  it("rejects service initiators before invoking the operation", async () => {
    auth.isPrincipal.mockReturnValue(false);
    await expect(action().handler(ctx())).rejects.toThrow("requires a user");
    expect(execute).not.toHaveBeenCalled();
  });
  it("uses the trusted organization for a default-namespace initiator", async () => {
    userInfo.getUserInfo.mockResolvedValue({
      userEntityRef: "user:default/alice",
      ownershipEntityRefs: [],
    });
    await action().handler(ctx());
    expect(execute).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({ subject: { entityRef: "user:default/alice" } }),
      expect.objectContaining({
        userEntityRef: "user:default/alice",
        organizationId: "acme",
      }),
      permissions,
    );
  });
  it("does not dispatch registration when organization resolution fails", async () => {
    organizations.resolve.mockResolvedValue(undefined);
    await expect(action().handler(ctx())).rejects.toThrow(
      "missing or ambiguous",
    );
    expect(execute).not.toHaveBeenCalled();
  });
  it("propagates operation failures without emitting success outputs", async () => {
    execute.mockRejectedValue(new Error("Repository access denied"));
    const context = ctx();
    await expect(action().handler(context)).rejects.toThrow(
      "Repository access denied",
    );
    expect(context.output).not.toHaveBeenCalled();
  });
});
