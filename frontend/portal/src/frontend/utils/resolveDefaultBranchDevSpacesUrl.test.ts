/*
 * Copyright Red Hat
 */

import { resolveDefaultBranchDevSpacesUrl } from "./resolveDefaultBranchDevSpacesUrl";

describe("resolveDefaultBranchDevSpacesUrl", () => {
  const base = {
    devSpacesBaseUrl: "https://devspaces.example.com",
    repoUrl: "https://github.com/acme/ansible-apme",
    branch: "main",
  };

  it("builds factory URL with default branch", () => {
    expect(resolveDefaultBranchDevSpacesUrl(base)).toBe(
      "https://devspaces.example.com/#https://github.com/acme/ansible-apme/tree/main",
    );
  });

  it("returns null when devSpaces base URL is missing", () => {
    expect(
      resolveDefaultBranchDevSpacesUrl({
        ...base,
        devSpacesBaseUrl: undefined,
      }),
    ).toBeNull();
  });

  it("returns null when repo URL is missing", () => {
    expect(
      resolveDefaultBranchDevSpacesUrl({ ...base, repoUrl: null }),
    ).toBeNull();
  });

  it("falls back to main when branch is empty", () => {
    expect(resolveDefaultBranchDevSpacesUrl({ ...base, branch: "  " })).toBe(
      "https://devspaces.example.com/#https://github.com/acme/ansible-apme/tree/main",
    );
  });
});
