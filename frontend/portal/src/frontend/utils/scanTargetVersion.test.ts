/*
 * Copyright Red Hat
 */

import { formatAnsibleCoreVersionLabel } from "./scanTargetVersion";

describe("formatAnsibleCoreVersionLabel", () => {
  it("formats bare version strings", () => {
    expect(formatAnsibleCoreVersionLabel("2.19")).toBe("ansible-core 2.19");
    expect(formatAnsibleCoreVersionLabel("2.19.0")).toBe("ansible-core 2.19.0");
  });

  it("passes through values that already include ansible-core", () => {
    expect(formatAnsibleCoreVersionLabel("ansible-core 2.20")).toBe(
      "ansible-core 2.20",
    );
  });

  it("returns null for empty values", () => {
    expect(formatAnsibleCoreVersionLabel("")).toBeNull();
    expect(formatAnsibleCoreVersionLabel(undefined)).toBeNull();
  });
});
