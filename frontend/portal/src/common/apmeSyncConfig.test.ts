/*
 * Copyright Red Hat
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

import { readApmeGitContentsSyncConfigs } from "./apmeSyncConfig";

function mockApmeEnabledConfig(enabled: boolean) {
  return {
    getOptionalConfig: jest.fn().mockImplementation((key: string) => {
      if (key === "ansible.apme") {
        return {
          getOptionalBoolean: jest
            .fn()
            .mockImplementation((k: string) =>
              k === "enabled" ? enabled : undefined,
            ),
          getString: jest.fn().mockReturnValue("http://localhost:8080"),
          getOptionalString: jest.fn().mockReturnValue(undefined),
        };
      }
      if (key === "catalog.providers.rhaap") {
        return undefined;
      }
      return undefined;
    }),
    getOptionalBoolean: jest
      .fn()
      .mockImplementation((key: string) =>
        key === "ansible.apme.enabled" ? enabled : undefined,
      ),
  };
}

function mockOrgConfig(
  name: string,
  apme?: { enabled?: boolean; scanOnRegister?: boolean },
) {
  return {
    getString: jest.fn().mockReturnValue(name),
    getOptionalConfig: jest.fn().mockImplementation((key: string) => {
      if (key !== "apme" || !apme) {
        return undefined;
      }
      return {
        getOptionalBoolean: jest.fn().mockImplementation((k: string) => {
          if (k === "enabled") {
            return apme.enabled;
          }
          if (k === "scanOnRegister") {
            return apme.scanOnRegister;
          }
          return undefined;
        }),
      };
    }),
  };
}

function mockGitContentsBlock(options: {
  apmeEnabled?: boolean;
  gitContentsEnabled?: boolean;
  blockScanOnRegister?: boolean;
  labels?: string[];
  orgs?: ReturnType<typeof mockOrgConfig>[];
  hasSchedule?: boolean;
}) {
  const {
    apmeEnabled = true,
    gitContentsEnabled,
    blockScanOnRegister,
    labels,
    orgs = [mockOrgConfig("my-org", { enabled: true })],
    hasSchedule = false,
  } = options;

  const apmeBlock = {
    getOptionalBoolean: jest.fn().mockImplementation((key: string) => {
      if (key === "enabled") {
        return apmeEnabled;
      }
      if (key === "scanOnRegister") {
        return blockScanOnRegister;
      }
      return undefined;
    }),
    getOptionalNumber: jest.fn().mockReturnValue(undefined),
    getOptionalStringArray: jest.fn().mockReturnValue(labels),
    has: jest
      .fn()
      .mockImplementation((key: string) => key === "schedule" && hasSchedule),
    getConfig: jest.fn(),
  };

  const hostConfig = {
    getString: jest.fn().mockReturnValue("github.com"),
    has: jest.fn().mockImplementation((key: string) => key === "orgs"),
    getConfigArray: jest.fn().mockReturnValue(orgs),
  };

  const providers = {
    has: jest.fn().mockImplementation((key: string) => key === "github"),
    getConfigArray: jest.fn().mockReturnValue([hostConfig]),
  };

  return {
    getOptionalBoolean: jest.fn().mockImplementation((key: string) => {
      if (key === "enabled") {
        return gitContentsEnabled;
      }
      return undefined;
    }),
    getOptionalConfig: jest.fn().mockImplementation((key: string) => {
      if (key === "apme") {
        return apmeBlock;
      }
      return undefined;
    }),
    has: jest.fn().mockImplementation((key: string) => key === "providers"),
    getConfig: jest.fn().mockImplementation((key: string) => {
      if (key === "providers") {
        return providers;
      }
      throw new Error(`unexpected getConfig key: ${key}`);
    }),
  };
}

function mockRhaapConfig(
  envBlocks: Record<string, ReturnType<typeof mockGitContentsBlock>>,
) {
  return {
    keys: jest.fn().mockReturnValue(Object.keys(envBlocks)),
    getConfig: jest.fn().mockImplementation((env: string) => ({
      getOptionalConfig: jest.fn().mockImplementation((key: string) => {
        if (key === "sync.ansibleGitContents") {
          return envBlocks[env];
        }
        return undefined;
      }),
    })),
  };
}

function mockConfigWithRhaap(
  rhaap: ReturnType<typeof mockRhaapConfig>,
  apmeEnabled = true,
) {
  return {
    getOptionalConfig: jest.fn().mockImplementation((key: string) => {
      if (key === "ansible.apme") {
        return {
          getOptionalBoolean: jest
            .fn()
            .mockImplementation((k: string) =>
              k === "enabled" ? apmeEnabled : undefined,
            ),
          getString: jest.fn().mockReturnValue("http://localhost:8080"),
          getOptionalString: jest.fn().mockReturnValue(undefined),
        };
      }
      if (key === "catalog.providers.rhaap") {
        return rhaap;
      }
      return undefined;
    }),
    getOptionalBoolean: jest
      .fn()
      .mockImplementation((key: string) =>
        key === "ansible.apme.enabled" ? apmeEnabled : undefined,
      ),
  };
}

describe("readApmeGitContentsSyncConfigs", () => {
  it("returns empty when ansible.apme.enabled is false", () => {
    expect(
      readApmeGitContentsSyncConfigs(mockApmeEnabledConfig(false) as any),
    ).toEqual([]);
  });

  it("returns empty when no rhaap providers exist", () => {
    const mockConfig = mockApmeEnabledConfig(true);
    expect(readApmeGitContentsSyncConfigs(mockConfig as any)).toEqual([]);
  });

  it("skips envs without enabled apme block", () => {
    const gitContents = mockGitContentsBlock({ apmeEnabled: false });
    const rhaap = mockRhaapConfig({ prod: gitContents });
    expect(
      readApmeGitContentsSyncConfigs(mockConfigWithRhaap(rhaap) as any),
    ).toEqual([]);
  });

  it("reads org scopes only when org.apme.enabled is explicitly true", () => {
    const gitContents = mockGitContentsBlock({
      blockScanOnRegister: false,
      labels: ["ansible"],
      orgs: [
        mockOrgConfig("org-a", { enabled: true }),
        mockOrgConfig("org-b", { enabled: true, scanOnRegister: true }),
        mockOrgConfig("org-c"),
        mockOrgConfig("org-d", { enabled: false }),
      ],
    });
    const rhaap = mockRhaapConfig({ stage: gitContents });

    expect(
      readApmeGitContentsSyncConfigs(mockConfigWithRhaap(rhaap) as any),
    ).toEqual([
      {
        env: "stage",
        enabled: true,
        scanOnRegister: false,
        maxPerRun: 10,
        labels: ["ansible"],
        schedule: undefined,
        orgs: [
          {
            env: "stage",
            provider: "github",
            host: "github.com",
            organization: "org-a",
            scanOnRegister: false,
            labels: ["ansible"],
          },
          {
            env: "stage",
            provider: "github",
            host: "github.com",
            organization: "org-b",
            scanOnRegister: true,
            labels: ["ansible"],
          },
        ],
      },
    ]);
  });

  it("defaults scanOnRegister to false and maxPerRun to 10", () => {
    const gitContents = mockGitContentsBlock({
      orgs: [mockOrgConfig("pilot-org", { enabled: true })],
    });
    const rhaap = mockRhaapConfig({ prod: gitContents });

    const [block] = readApmeGitContentsSyncConfigs(
      mockConfigWithRhaap(rhaap) as any,
    );
    expect(block.scanOnRegister).toBe(false);
    expect(block.maxPerRun).toBe(10);
  });

  it("skips gitContents blocks with enabled false", () => {
    const gitContents = mockGitContentsBlock({ gitContentsEnabled: false });
    const rhaap = mockRhaapConfig({ prod: gitContents });

    expect(
      readApmeGitContentsSyncConfigs(mockConfigWithRhaap(rhaap) as any),
    ).toEqual([]);
  });
});
