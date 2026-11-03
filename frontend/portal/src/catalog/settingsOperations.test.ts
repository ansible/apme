import { mkdtemp, rm } from "node:fs/promises";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { ConfigReader } from "@backstage/config";
import { mockServices } from "@backstage/backend-test-utils";
import { AuthorizeResult } from "@backstage/plugin-permission-common";
import { PortalOperations } from "@ansible/portal-plugin-node";
import { registerSettingsOperations } from "./settingsOperations";
import { ApmePortalSettingsStore } from "./apmePortalSettingsStore";

describe("Portal settings operations", () => {
  let directory: string;
  let store: ApmePortalSettingsStore;
  let operations: PortalOperations;
  const permissions = mockServices.permissions.mock();
  const context = {
    credentials: {
      $$type: "@backstage/BackstageCredentials" as const,
      principal: { type: "user" as const, userEntityRef: "user:acme/alice" },
    },
    userId: "alice",
    userEntityRef: "user:acme/alice",
    organizationId: "acme",
  };
  beforeEach(async () => {
    directory = await mkdtemp(join(tmpdir(), "apme-settings-operation-"));
    store = new ApmePortalSettingsStore(join(directory, "settings.json"));
    permissions.authorize.mockResolvedValue([
      { result: AuthorizeResult.ALLOW },
    ]);
    operations = new PortalOperations(
      mockServices.logger.mock(),
      { resolve: async () => "acme" },
      { check: jest.fn() },
    );
    registerSettingsOperations({
      operations,
      store,
      rootConfig: new ConfigReader({
        ansible: {
          apme: {
            enabled: true,
            baseUrl: "http://127.0.0.1:8080",
            targetAnsibleCoreVersion: "2.18",
          },
        },
      }),
    });
  });
  afterEach(async () => {
    await rm(directory, { recursive: true, force: true });
  });
  async function execute(
    id: string,
    input: Record<string, unknown> = {},
    subject = context.userEntityRef,
  ) {
    return operations.execute(
      id,
      { subject: { entityRef: subject }, input },
      context,
      permissions,
    );
  }
  it("persists authorized updates and returns the same resolved settings on read", async () => {
    const updated = await execute("apme.settings.save", {
      enableAi: true,
      targetAnsibleCoreVersion: "2.19",
      defaultAiModelId: "provider/model",
    });
    expect(updated).toMatchObject({
      enableAi: true,
      targetAnsibleCoreVersion: "2.19",
      defaultAiModelId: "provider/model",
    });
    expect(await execute("apme.settings.get")).toEqual(updated);
    expect((await store.read()).global?.enableAi).toBe(true);
    expect(permissions.authorize).toHaveBeenCalledWith(
      expect.arrayContaining([
        expect.objectContaining({ resourceRef: "apme" }),
      ]),
      expect.anything(),
    );
  });
  it("denies writes before persisting when the host rejects settings permission", async () => {
    permissions.authorize.mockResolvedValue([{ result: AuthorizeResult.DENY }]);
    await expect(
      execute("apme.settings.save", { enableAi: true }),
    ).rejects.toThrow("permission");
    expect(await store.read()).toEqual({});
  });
  it.each([
    { enableAi: "true" },
    { unknown: true },
    {},
    { gatewayBaseUrl: 123 },
  ])("rejects invalid input %j before persistence", async (input) => {
    await expect(execute("apme.settings.save", input)).rejects.toThrow(
      "schema",
    );
    expect(await store.read()).toEqual({});
  });
  it("uses the same semantic validation as the HTTP settings route", async () => {
    await expect(
      execute("apme.settings.save", { targetAnsibleCoreVersion: "99.99" }),
    ).rejects.toThrow("Unsupported");
    expect(await store.read()).toEqual({});
  });
  it("rejects a caller-selected identity", async () => {
    await expect(
      execute("apme.settings.save", { enableAi: true }, "user:acme/bob"),
    ).rejects.toThrow("current user");
    expect(await store.read()).toEqual({});
  });
});
