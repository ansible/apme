/*
 * Copyright Red Hat
 */

import { DEFAULT_APME_TARGET_ANSIBLE_CORE_VERSION } from "../../common/scanTargetDefaults";
import { resolveDefaultAnsibleVersionForScan } from "./resolveDefaultAnsibleVersionForScan";

describe("resolveDefaultAnsibleVersionForScan", () => {
  const getProjectScanTarget = jest.fn();
  const getPortalSettings = jest.fn();
  const apmeApi = { getProjectScanTarget, getPortalSettings };

  beforeEach(() => {
    jest.clearAllMocks();
  });

  it("uses effective project scan target when present", async () => {
    getProjectScanTarget.mockResolvedValue({
      effective: "2.18",
      source: "global",
      globalDefault: "2.18",
    });

    await expect(
      resolveDefaultAnsibleVersionForScan(apmeApi, "proj-1"),
    ).resolves.toBe("2.18");
    expect(getPortalSettings).not.toHaveBeenCalled();
  });

  it("falls back to portal settings when scan-target fails", async () => {
    getProjectScanTarget.mockRejectedValue(new Error("not found"));
    getPortalSettings.mockResolvedValue({
      enableAi: true,
      publishViaGateway: true,
      targetAnsibleCoreVersion: "2.17",
    });

    await expect(
      resolveDefaultAnsibleVersionForScan(apmeApi, "proj-1"),
    ).resolves.toBe("2.17");
  });

  it("falls back to hardcoded default when both APIs fail", async () => {
    getProjectScanTarget.mockRejectedValue(new Error("down"));
    getPortalSettings.mockRejectedValue(new Error("down"));

    await expect(
      resolveDefaultAnsibleVersionForScan(apmeApi, "proj-1"),
    ).resolves.toBe(DEFAULT_APME_TARGET_ANSIBLE_CORE_VERSION);
  });
});
