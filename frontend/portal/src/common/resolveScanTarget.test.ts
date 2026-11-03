/*
 * Copyright Red Hat
 */

import {
  resolveScanTarget,
  resolveScanTargetVersion,
} from "./resolveScanTarget";

describe("resolveScanTarget", () => {
  it("prefers per-project override over global default", () => {
    const result = resolveScanTarget({
      projectId: "proj-a",
      store: {
        global: { targetAnsibleCoreVersion: "2.17" },
        projects: { "proj-a": { targetAnsibleCoreVersion: "2.16" } },
      },
    });

    expect(result).toEqual({
      effective: "2.16",
      source: "project",
      globalDefault: "2.17",
      projectOverride: "2.16",
    });
  });

  it("uses persisted global default when no project override", () => {
    const result = resolveScanTarget({
      projectId: "proj-b",
      store: {
        global: { targetAnsibleCoreVersion: "2.17" },
      },
    });

    expect(result.effective).toBe("2.17");
    expect(result.source).toBe("global");
    expect(result.projectOverride).toBeUndefined();
  });

  it("falls back to app-config then hardcoded default", () => {
    expect(
      resolveScanTarget({
        configTargetAnsibleCoreVersion: "2.18",
      }).effective,
    ).toBe("2.18");

    expect(resolveScanTarget({}).effective).toBe("2.16");
  });

  it("normalizes patch versions", () => {
    expect(
      resolveScanTargetVersion({
        store: { global: { targetAnsibleCoreVersion: "2.17.0" } },
      }),
    ).toBe("2.17");
  });
});
