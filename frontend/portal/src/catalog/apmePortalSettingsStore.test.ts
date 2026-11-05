/*
 * Copyright Red Hat
 */

import fs from "fs/promises";
import os from "os";
import path from "path";
import { ApmePortalSettingsStore } from "./apmePortalSettingsStore";

describe("ApmePortalSettingsStore", () => {
  let settingsPath: string;

  beforeEach(() => {
    settingsPath = path.join(
      os.tmpdir(),
      `apme-portal-settings-${Date.now()}-${Math.random()}.json`,
    );
  });

  afterEach(async () => {
    await fs.rm(settingsPath, { force: true });
  });

  it("returns empty settings when file is missing", async () => {
    const store = new ApmePortalSettingsStore(settingsPath);
    await expect(store.read()).resolves.toEqual({});
  });

  it("returns empty settings when file is empty", async () => {
    await fs.writeFile(settingsPath, "");
    const store = new ApmePortalSettingsStore(settingsPath);
    await expect(store.read()).resolves.toEqual({});
  });

  it("returns empty settings when file has invalid JSON", async () => {
    await fs.writeFile(settingsPath, "{");
    const store = new ApmePortalSettingsStore(settingsPath);
    await expect(store.read()).resolves.toEqual({});
  });

  it("round-trips written settings", async () => {
    const store = new ApmePortalSettingsStore(settingsPath);
    await store.updateGlobal("2.16");
    await expect(store.read()).resolves.toEqual({
      global: { targetAnsibleCoreVersion: "2.16" },
    });
  });

  it("persists enableAi via updateGlobalSettings", async () => {
    const store = new ApmePortalSettingsStore(settingsPath);
    await store.updateGlobalSettings({ enableAi: false });
    await expect(store.read()).resolves.toEqual({
      global: { enableAi: false },
    });
  });

  it("coerces invalid persisted enableAi to false on read", async () => {
    await fs.writeFile(
      settingsPath,
      JSON.stringify({ global: { enableAi: "false" } }),
    );
    const store = new ApmePortalSettingsStore(settingsPath);
    await expect(store.read()).resolves.toEqual({
      global: { enableAi: false },
    });
  });

  it("retains boolean enableAi on read", async () => {
    await fs.writeFile(
      settingsPath,
      JSON.stringify({ global: { enableAi: true } }),
    );
    const store = new ApmePortalSettingsStore(settingsPath);
    await expect(store.read()).resolves.toEqual({
      global: { enableAi: true },
    });
  });

  it("round-trips a Gateway URL override", async () => {
    const store = new ApmePortalSettingsStore(settingsPath);
    await store.updateGlobalSettings({
      gatewayBaseUrl: "http://host.containers.internal:8080",
    });
    await expect(store.read()).resolves.toEqual({
      global: { gatewayBaseUrl: "http://host.containers.internal:8080" },
    });
    await store.updateGlobalSettings({ gatewayBaseUrl: null });
    await expect(store.read()).resolves.toEqual({
      global: {},
    });
  });

  it("creates project and activity maps when none are persisted yet", async () => {
    const store = new ApmePortalSettingsStore(settingsPath);
    await store.updateProjectTarget("proj-1", "2.18");
    await store.updateActivityOutcome("scan-1", {
      branch_name: "review/fix",
      pr_url: "https://github.com/acme/apme/pull/7",
      commit_sha: "abc1234",
    });
    await expect(store.read()).resolves.toEqual({
      projects: { "proj-1": { targetAnsibleCoreVersion: "2.18" } },
      activities: {
        "scan-1": {
          branch_name: "review/fix",
          pr_url: "https://github.com/acme/apme/pull/7",
          commit_sha: "abc1234",
        },
      },
    });

    await store.updateProjectTarget("proj-1", null);
    await store.updateActivityOutcome("scan-1", {
      branch_name: "review/fix-2",
    });
    await expect(store.read()).resolves.toEqual({
      projects: {},
      activities: {
        "scan-1": {
          branch_name: "review/fix-2",
          pr_url: "https://github.com/acme/apme/pull/7",
          commit_sha: "abc1234",
        },
      },
    });
  });
});
