import { mockServices } from "@backstage/backend-test-utils";
import type { CatalogClient } from "@backstage/catalog-client";
import type { IApmeService } from "../common/index";
import { runApmeCatalogSyncBatch } from "./apmeCatalogSyncTask";

describe("catalog sync after deregistration", () => {
  const entity = {
    apiVersion: "backstage.io/v1alpha1",
    kind: "Component",
    metadata: {
      name: "repo",
      annotations: {
        "backstage.io/source-location": "url:https://github.com/acme/repo",
        "ansible.io/scm-organization": "acme",
      },
    },
    spec: { type: "git-repository", repository_default_branch: "main" },
  };
  const getProjectByRepoUrl = jest.fn();
  const getProjects = jest.fn();
  const triggerScan = jest.fn();
  const createProject = jest.fn();
  let stopped: boolean;
  const run = () =>
    runApmeCatalogSyncBatch({
      auth: mockServices.auth(),
      logger: mockServices.logger.mock(),
      catalogClient: {
        getEntities: async () => ({ items: [entity] }),
      } as unknown as CatalogClient,
      apmeService: {
        getProjectByRepoUrl,
        getProjects,
        triggerScan,
        createProject,
      } as unknown as IApmeService,
      offset: 0,
      syncConfig: {
        env: "development",
        enabled: true,
        scanOnRegister: true,
        maxPerRun: 10,
        orgs: [
          {
            env: "development",
            provider: "github",
            host: "github.com",
            organization: "acme",
            scanOnRegister: true,
          },
        ],
      },
      shouldTrackEntity: () => !stopped,
    });
  beforeEach(() => {
    jest.resetAllMocks();
    stopped = false;
    getProjectByRepoUrl.mockResolvedValue({ id: "project" });
    getProjects.mockResolvedValue([]);
  });
  it("skips stale catalog snapshots after removal", async () => {
    stopped = true;
    expect(await run()).toMatchObject({
      skipped: 1,
      scanned: 0,
      registered: 0,
    });
    expect(getProjectByRepoUrl).not.toHaveBeenCalled();
    expect(triggerScan).not.toHaveBeenCalled();
  });
  it("does not create a project if removal happens during lookup", async () => {
    getProjectByRepoUrl.mockImplementation(async () => {
      stopped = true;
      return null;
    });
    expect(await run()).toMatchObject({
      skipped: 1,
      scanned: 0,
      registered: 0,
    });
    expect(createProject).not.toHaveBeenCalled();
    expect(triggerScan).not.toHaveBeenCalled();
  });
  it("does not dispatch a scan after removal during lookup", async () => {
    getProjectByRepoUrl.mockImplementation(async () => {
      stopped = true;
      return { id: "project" };
    });
    expect(await run()).toMatchObject({ skipped: 1, scanned: 0 });
    expect(triggerScan).not.toHaveBeenCalled();
  });

  it("registers and scans when no project exists yet", async () => {
    getProjectByRepoUrl.mockResolvedValue(null);
    createProject.mockResolvedValue({ id: "new-project" });
    expect(await run()).toMatchObject({
      registered: 1,
      scanned: 1,
      skipped: 0,
    });
    expect(createProject).toHaveBeenCalled();
    expect(triggerScan).toHaveBeenCalledWith("new-project", expect.any(Object));
  });

  it("throws when the catalog cannot be read so the scheduler records failure", async () => {
    const catalogError = new Error("catalog unavailable");
    const logger = mockServices.logger.mock();
    await expect(
      runApmeCatalogSyncBatch({
        auth: mockServices.auth(),
        logger,
        catalogClient: {
          getEntities: async () => {
            throw catalogError;
          },
        } as unknown as CatalogClient,
        apmeService: {} as IApmeService,
        offset: 0,
        syncConfig: {
          env: "development",
          enabled: true,
          scanOnRegister: false,
          maxPerRun: 10,
          orgs: [],
        },
      }),
    ).rejects.toThrow("Failed to read catalog entities: catalog unavailable");
    expect(logger.error).toHaveBeenCalledWith(
      "Failed to read catalog entities: catalog unavailable",
    );
  });
});
