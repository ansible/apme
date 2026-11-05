/* Copyright Red Hat. Licensed under the Apache License, Version 2.0. */
const { commonAncestorPath } = require("./portalPath.cjs");

describe("commonAncestorPath", () => {
    it("returns the shared directory for sibling paths", () => {
        expect(commonAncestorPath("/workspace/apme", "/workspace/sdk")).toBe(
            "/workspace",
        );
    });

    it("terminates at the filesystem root for different mount paths", () => {
        expect(commonAncestorPath("/workspace/apme", "/mnt/portal-sdk")).toBe(
            "/",
        );
    });
});
