/*
 * Copyright Red Hat
 */

import { apmeApiRef } from "./api";

describe("api", () => {
  it("exports the APME API ref", () => {
    expect(apmeApiRef.id).toBe("plugin.apme.api");
  });
});
