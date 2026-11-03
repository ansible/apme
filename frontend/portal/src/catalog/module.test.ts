import { catalogModuleApme } from "./module";
import { registerApmeOperations } from "./operations";
import { createRouter } from "./router";
import { registerApmeCatalogSyncTasks } from "./apmeCatalogSyncScheduler";
import { registerPortalGalaxyServersSync } from "./portalGalaxyServersSync";
import { ApmeLearnedDepsEntityProvider } from "./providers/ApmeLearnedDepsEntityProvider";
import { isApmeEnabled, getApmeConfig } from "../common/index";
import fs from "fs/promises";
import os from "os";
import path from "path";

jest.mock("@backstage/backend-plugin-api", () => ({
  ...jest.requireActual("@backstage/backend-plugin-api"),
  coreServices: {
    logger: "logger",
    rootConfig: "rootConfig",
    httpRouter: "httpRouter",
    httpAuth: "httpAuth",
    scheduler: "scheduler",
    discovery: "discovery",
    auth: "auth",
    permissionsRegistry: "permissionsRegistry",
    permissions: "permissions",
  },
  createBackendModule: (opts: unknown) => opts,
}));

jest.mock("../common/index", () => ({
  ...jest.requireActual("../common/index"),
  apmeServiceRef: "apmeServiceRef",
  isApmeEnabled: jest.fn(),
  getApmeConfig: jest.fn(),
  resolveScanTargetVersion: jest.fn().mockResolvedValue("2.18"),
  ApmeClient: class ApmeClient {},
}));

jest.mock("@backstage/plugin-catalog-node", () => ({
  ...jest.requireActual("@backstage/plugin-catalog-node"),
  catalogProcessingExtensionPoint: "catalogProcessingExtensionPoint",
}));

jest.mock("./router", () => ({
  createRouter: jest.fn(),
}));

jest.mock("./operations", () => ({ registerApmeOperations: jest.fn() }));
jest.mock("./manualRepositories/registerOperation", () => ({
  registerRepositoryOperation: jest.fn(),
}));

jest.mock("./apmeCatalogSyncScheduler", () => ({
  registerApmeCatalogSyncTasks: jest.fn(),
}));

jest.mock("./portalGalaxyServersSync", () => ({
  registerPortalGalaxyServersSync: jest.fn(),
}));

jest.mock("./providers/ApmeLearnedDepsEntityProvider", () => ({
  ApmeLearnedDepsEntityProvider: jest.fn(),
}));

describe("catalogModuleApme", () => {
  const mockCreateRouter = createRouter as jest.MockedFunction<
    typeof createRouter
  >;
  const mockRegisterCatalogSync =
    registerApmeCatalogSyncTasks as jest.MockedFunction<
      typeof registerApmeCatalogSyncTasks
    >;
  const mockRegisterGalaxySync =
    registerPortalGalaxyServersSync as jest.MockedFunction<
      typeof registerPortalGalaxyServersSync
    >;
  const mockIsApmeEnabled = isApmeEnabled as jest.MockedFunction<
    typeof isApmeEnabled
  >;
  const mockGetApmeConfig = getApmeConfig as jest.MockedFunction<
    typeof getApmeConfig
  >;
  const mockLearnedDepsProvider =
    ApmeLearnedDepsEntityProvider as unknown as jest.MockedFunction<any>;

  beforeEach(() => {
    jest.clearAllMocks();
    mockIsApmeEnabled.mockReturnValue(true);
    mockGetApmeConfig.mockReturnValue({
      portalSettingsPath: "/tmp/apme-settings.json",
      targetAnsibleCoreVersion: "2.18",
    } as any);
    mockCreateRouter.mockResolvedValue({} as any);
    mockRegisterGalaxySync.mockResolvedValue(undefined as any);
    mockLearnedDepsProvider.mockImplementation(() => ({
      schedule: jest.fn().mockResolvedValue(undefined),
    }));
  });

  function getInit() {
    const registerInit = jest.fn();
    (catalogModuleApme as any).register({ registerInit });
    return registerInit.mock.calls[0][0].init;
  }

  it("skips initialization when APME is disabled", async () => {
    mockIsApmeEnabled.mockReturnValue(false);
    const init = getInit();

    const logger = { info: jest.fn() };
    const permissionsRegistry = {
      addResourceType: jest.fn(),
      addPermissions: jest.fn(),
    };

    await init({
      logger,
      rootConfig: {},
      apmeService: {},
      httpRouter: { use: jest.fn() },
      httpAuth: {},
      scheduler: {},
      discovery: {},
      auth: {},
      catalogProcessing: { addEntityProvider: jest.fn() },
      permissionsRegistry,
      permissions: {},
    });

    expect(logger.info).toHaveBeenCalledWith(
      "APME is disabled; skipping catalog module registration",
    );
    expect(permissionsRegistry.addResourceType).not.toHaveBeenCalled();
    expect(mockCreateRouter).not.toHaveBeenCalled();
  });

  it("registers settings resource permissions and initializes services when enabled", async () => {
    const init = getInit();

    const logger = { info: jest.fn() };
    const httpRouter = { use: jest.fn() };
    const permissionsRegistry = {
      addResourceType: jest.fn(),
      addPermissions: jest.fn(),
    };
    const catalogProcessing = { addEntityProvider: jest.fn() };
    const scheduler = {};
    const apmeService = {};
    const auth = {};
    const discovery = {};
    const permissions = {};
    const httpAuth = {};
    const rootConfig = {};

    await init({
      logger,
      rootConfig,
      apmeService,
      httpRouter,
      httpAuth,
      scheduler,
      discovery,
      auth,
      catalogProcessing,
      permissionsRegistry,
      permissions,
      entitlements: { register: jest.fn() },
      operations: { register: jest.fn() },
    });

    expect(permissionsRegistry.addResourceType).toHaveBeenCalledTimes(1);
    const resourceType = permissionsRegistry.addResourceType.mock.calls[0][0];
    await expect(
      resourceType.getResources(["apme", "invalid"]),
    ).resolves.toEqual([{ capability: "apme" }, undefined]);

    expect(mockCreateRouter).toHaveBeenCalledWith(
      expect.objectContaining({
        apmeService,
        logger,
        httpAuth,
        rootConfig,
        permissions,
      }),
    );
    expect(httpRouter.use).toHaveBeenCalledTimes(2);
    expect(permissionsRegistry.addPermissions).toHaveBeenCalledTimes(3);
    expect(registerApmeOperations).toHaveBeenCalledWith(
      expect.objectContaining({ apmeService, permissions, auth }),
    );
    expect(mockRegisterCatalogSync).toHaveBeenCalledTimes(1);
    expect(mockRegisterGalaxySync).toHaveBeenCalledTimes(1);
    expect(catalogProcessing.addEntityProvider).toHaveBeenCalledTimes(3);
    expect(mockLearnedDepsProvider).toHaveBeenCalledTimes(1);
  });

  it("resolveEnableAi defaults to false when portal settings unset", async () => {
    const settingsPath = path.join(
      os.tmpdir(),
      `apme-portal-settings-${Date.now()}-${Math.random()}.json`,
    );
    mockGetApmeConfig.mockReturnValue({
      portalSettingsPath: settingsPath,
      targetAnsibleCoreVersion: "2.18",
    } as any);

    const init = getInit();
    await init({
      logger: { info: jest.fn() },
      rootConfig: {},
      apmeService: {},
      httpRouter: { use: jest.fn() },
      httpAuth: {},
      scheduler: {},
      discovery: {},
      auth: {},
      catalogProcessing: { addEntityProvider: jest.fn() },
      permissionsRegistry: {
        addResourceType: jest.fn(),
        addPermissions: jest.fn(),
      },
      permissions: {},
      entitlements: { register: jest.fn() },
      operations: { register: jest.fn() },
    });

    const { resolveEnableAi } = mockRegisterCatalogSync.mock.calls[0][0];
    await expect(resolveEnableAi!()).resolves.toBe(false);

    await fs.rm(settingsPath, { force: true });
  });
});
