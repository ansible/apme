/*
 * Copyright Red Hat
 */

import {
  buildQualitySettingsRuleHref,
  createQualitySettingsRuleHrefResolver,
} from "./qualitySettingsRuleHref";

describe("qualitySettingsRuleHref", () => {
  it("builds a quality-settings URL with encoded rule id", () => {
    expect(buildQualitySettingsRuleHref("L001")).toBe(
      "/self-service/repositories/quality-settings?rule=L001",
    );
    expect(buildQualitySettingsRuleHref("R108/test")).toBe(
      "/self-service/repositories/quality-settings?rule=R108%2Ftest",
    );
  });

  it("returns href only for rules present in the catalog", () => {
    const resolve = createQualitySettingsRuleHrefResolver(
      new Set(["L001", "M010"]),
    );
    expect(resolve("L001")).toBe(
      "/self-service/repositories/quality-settings?rule=L001",
    );
    expect(resolve("native:L001")).toBe(
      "/self-service/repositories/quality-settings?rule=L001",
    );
    expect(resolve("UNKNOWN")).toBeUndefined();
    expect(resolve("")).toBeUndefined();
  });
});
