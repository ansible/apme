/*
 * Copyright Red Hat
 */

import {
  isApmeProjectConflictError,
  registerOrResolveApmeProject,
  resolveApmeProject,
} from "./registerOrResolveApmeProject";

describe("registerOrResolveApmeProject re-export", () => {
  it("re-exports helpers from apme-common", () => {
    expect(typeof isApmeProjectConflictError).toBe("function");
    expect(typeof resolveApmeProject).toBe("function");
    expect(typeof registerOrResolveApmeProject).toBe("function");
  });
});
