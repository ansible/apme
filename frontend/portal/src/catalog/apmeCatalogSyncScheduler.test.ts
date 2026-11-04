import { ConfigReader } from "@backstage/config";
import type { SchedulerService } from "@backstage/backend-plugin-api";
import type { CatalogClient } from "@backstage/catalog-client";
import type { IApmeService } from "../common/index";
import { registerApmeCatalogSyncTasks } from "./apmeCatalogSyncScheduler";

describe("APME catalog sync scheduler recovery", () => {
  it("reports catalog read failure and recovery to the host", async () => {
    const scheduled: Array<{ fn: () => Promise<void> }> = [];
    const scheduler = {
      scheduleTask: jest.fn((task) => {
        scheduled.push(task);
        return Promise.resolve({} as never);
      }),
    } as unknown as SchedulerService;
    const catalogReadError = new Error("catalog unavailable");
    const getEntities = jest
      .fn()
      .mockRejectedValueOnce(catalogReadError)
      .mockResolvedValueOnce({ items: [] });
    const onSyncError = jest.fn();
    const onSyncRecovered = jest.fn();

    registerApmeCatalogSyncTasks({
      scheduler,
      catalogClient: { getEntities } as unknown as CatalogClient,
      auth: {
        getOwnServiceCredentials: async () => ({}),
        getPluginRequestToken: async () => ({ token: "catalog-token" }),
      } as never,
      apmeService: {} as IApmeService,
      rootConfig: new ConfigReader({
        ansible: { apme: { enabled: true } },
        catalog: {
          providers: {
            rhaap: {
              development: {
                sync: {
                  ansibleGitContents: {
                    enabled: true,
                    apme: { enabled: true },
                    providers: {
                      github: [
                        {
                          host: "github.com",
                          orgs: [{ name: "acme", apme: { enabled: true } }],
                        },
                      ],
                    },
                  },
                },
              },
            },
          },
        },
      }),
      logger: {
        info: jest.fn(),
        error: jest.fn(),
        child: () => ({ info: jest.fn(), error: jest.fn() }),
      } as never,
      onSyncError,
      onSyncRecovered,
    });

    expect(scheduled).toHaveLength(1);
    await expect(scheduled[0].fn()).rejects.toThrow(
      "Failed to read catalog entities: catalog unavailable",
    );
    expect(onSyncError).toHaveBeenCalledWith(
      "development",
      expect.objectContaining({
        message: expect.stringContaining("catalog unavailable"),
      }),
    );
    await expect(scheduled[0].fn()).resolves.toBeUndefined();
    expect(onSyncRecovered).toHaveBeenCalledWith("development");
  });
});
